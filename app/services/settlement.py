"""Fail-closed settlement of value bets from authoritative final results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from app.core.logging import get_logger
from app.models.entities.settlement import BankrollEntry, Settlement, SettlementResult
from app.models.value_objects.markets import MarketType
from app.models.value_objects.score import Score

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.value_bet import ValueBet
    from app.models.value_objects.money import Money
    from app.providers.interfaces.fixtures_provider import FixturesProvider
    from app.providers.schemas.fixtures import ProviderFixture
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.settlement_repository import (
        BankrollRepository,
        SettlementRepository,
    )
    from app.repositories.interfaces.value_bet_repository import ValueBetRepository

logger = get_logger(__name__)


class FixtureLifecycle(StrEnum):
    """Settlement-relevant lifecycle, stricter than the persisted 0021 enum."""

    NOT_STARTED = "NOT_STARTED"
    LIVE = "LIVE"
    INTERRUPTED = "INTERRUPTED"
    FT = "FT"
    AET = "AET"
    PEN = "PEN"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    AWARDED_WALKOVER = "AWARDED_WALKOVER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AuthoritativeSettlementState:
    lifecycle: FixtureLifecycle
    regulation_score: Score | None
    score_basis: str | None
    reason: str | None = None

    @property
    def can_settle(self) -> bool:
        return (
            self.lifecycle
            in {
                FixtureLifecycle.FT,
                FixtureLifecycle.AET,
                FixtureLifecycle.PEN,
            }
            and self.regulation_score is not None
        )


@dataclass(frozen=True, slots=True)
class AuthoritativeVoidEvidence:
    """Explicit terminal VOID instruction from an authoritative market source.

    Fixture lifecycle alone is never sufficient evidence.  The injected provider
    owns authentication and policy interpretation; this value binds its decision
    to one persisted value bet and fixture.
    """

    value_bet_id: UUID
    fixture_id: UUID
    reason_code: str
    source: str
    evidence_reference: str
    bookmaker_id: UUID | None = None

    def __post_init__(self) -> None:
        reason = self.reason_code.strip()
        source = self.source.strip()
        reference = self.evidence_reference.strip()
        if not reason or len(reason) > 64:
            raise ValueError("VOID reason code must contain 1-64 characters")
        if not source:
            raise ValueError("VOID evidence requires an authoritative source")
        if not reference:
            raise ValueError("VOID evidence requires an evidence reference")
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "evidence_reference", reference)


class VoidSettlementEvidenceProvider(Protocol):
    """Port for explicit bookmaker/market settlement instructions."""

    async def get_void_evidence(
        self,
        *,
        value_bet: ValueBet,
        fixture: Fixture,
        lifecycle: FixtureLifecycle,
    ) -> AuthoritativeVoidEvidence | None: ...


@dataclass
class SettlementResult_:
    value_bet_id: UUID
    fixture_id: UUID
    result: SettlementResult
    score_home: int | None
    score_away: int | None
    profit_loss: Decimal
    bankroll_before: Decimal
    bankroll_after: Decimal
    settled: bool
    message: str = ""
    void_reason_code: str | None = None


@dataclass
class SettlementReport:
    fixtures_checked: int
    bets_eligible: int
    bets_settled: int
    bets_skipped: int
    total_pl: Decimal
    details: list[SettlementResult_] = field(default_factory=list)
    provider_requests: int = 0
    deferred_fixtures: dict[str, str] = field(default_factory=dict)


class SettlementService:
    """Settle only unresolved bets after targeted final-state validation."""

    def __init__(
        self,
        *,
        fixtures_provider: FixturesProvider,
        fixtures: FixtureRepository,
        value_bets: ValueBetRepository,
        settlements: SettlementRepository,
        bankroll: BankrollRepository,
        initial_bankroll: Money | None = None,
        void_evidence_provider: VoidSettlementEvidenceProvider | None = None,
    ) -> None:
        self._fixtures_provider = fixtures_provider
        self._fixtures = fixtures
        self._value_bets = value_bets
        self._settlements = settlements
        self._bankroll = bankroll
        self._initial_bankroll = initial_bankroll
        self._void_evidence_provider = void_evidence_provider

    async def settle_all(self) -> SettlementReport:
        """Settle all currently eligible bets, failing closed per fixture."""
        initial_unsettled = await self._settlements.list_unsettled_value_bet_ids()
        if not initial_unsettled:
            return SettlementReport(0, 0, 0, 0, Decimal("0"))

        candidates: list[tuple[ValueBet, Fixture]] = []
        fixtures_by_id: dict[UUID, Fixture] = {}
        for value_bet_id in initial_unsettled:
            value_bet = await self._value_bets.get(value_bet_id)
            if value_bet is None:
                logger.warning("Unsettled value bet %s is missing; deferring", value_bet_id)
                continue
            fixture = fixtures_by_id.get(value_bet.fixture_id)
            if fixture is None:
                fixture = await self._fixtures.get(value_bet.fixture_id)
                if fixture is None:
                    logger.warning(
                        "Fixture %s for unsettled value bet %s is missing; deferring",
                        value_bet.fixture_id,
                        value_bet.id,
                    )
                    continue
                fixtures_by_id[fixture.id] = fixture
            candidates.append((value_bet, fixture))

        states: dict[UUID, AuthoritativeSettlementState] = {}
        provider_requests = 0
        for fixture in fixtures_by_id.values():
            if fixture.external_source != "api-football" or not fixture.external_id:
                reason = "missing API-Football fixture identity"
                states[fixture.id] = AuthoritativeSettlementState(
                    FixtureLifecycle.UNKNOWN, None, None, reason
                )
                continue
            provider_requests += 1
            refreshed = await self._fixtures_provider.get_fixture(fixture.external_id)
            state = self._authoritative_state(fixture, refreshed)
            states[fixture.id] = state

        void_evidence: dict[UUID, AuthoritativeVoidEvidence] = {}
        if self._void_evidence_provider is not None:
            for value_bet, fixture in candidates:
                state = states[fixture.id]
                if state.can_settle or state.lifecycle not in _VOID_EVIDENCE_LIFECYCLES:
                    continue
                evidence = await self._void_evidence_provider.get_void_evidence(
                    value_bet=value_bet,
                    fixture=fixture,
                    lifecycle=state.lifecycle,
                )
                if evidence is None:
                    continue
                self._validate_void_evidence(evidence, value_bet, fixture)
                void_evidence[value_bet.id] = evidence

        deferred: dict[str, str] = {}
        for fixture_id, state in states.items():
            if state.can_settle:
                continue
            unresolved = any(
                fixture.id == fixture_id and value_bet.id not in void_evidence
                for value_bet, fixture in candidates
            )
            if unresolved:
                deferred[str(fixture_id)] = state.reason or state.lifecycle.value

        # Provider reads finish before any mutation. The xact lock then makes a
        # concurrent waiter re-read rows committed by the first transaction.
        default_balance = (
            self._initial_bankroll.amount if self._initial_bankroll is not None else Decimal("0")
        )
        running_balance = await self._bankroll.lock_and_get_latest_balance(default_balance)
        still_unsettled = set(await self._settlements.list_unsettled_value_bet_ids())

        details: list[SettlementResult_] = []
        bets_eligible = 0
        bets_settled = 0
        bets_skipped = 0
        for value_bet, fixture in candidates:
            if value_bet.id not in still_unsettled:
                continue
            bets_eligible += 1
            state = states[fixture.id]
            evidence = void_evidence.get(value_bet.id)
            if not state.can_settle and evidence is None:
                bets_skipped += 1
                continue
            if evidence is not None:
                result = self._void_result(value_bet, fixture, evidence)
                settlement_basis = f"authoritative_void:{evidence.reason_code}"
            else:
                assert state.regulation_score is not None
                assert state.score_basis is not None
                result = self._resolve(value_bet, fixture, state.regulation_score)
                settlement_basis = state.score_basis
            if not result.settled:
                bets_skipped += 1
                details.append(result)
                continue

            bankroll_before = running_balance
            bankroll_after = bankroll_before + result.profit_loss
            await self._settlements.add(
                Settlement(
                    value_bet_id=value_bet.id,
                    fixture_id=fixture.id,
                    result=result.result,
                    score_home=result.score_home,
                    score_away=result.score_away,
                    profit_loss=result.profit_loss,
                    void_reason_code=result.void_reason_code,
                    bankroll_before=bankroll_before,
                    bankroll_after=bankroll_after,
                    settlement_timestamp=datetime.now(UTC),
                )
            )
            await self._bankroll.add(
                BankrollEntry(
                    amount=result.profit_loss,
                    balance_after=bankroll_after,
                    reason=(
                        f"Settlement: {fixture.id} | {value_bet.selection.label} "
                        f"→ {result.result.value} | basis={settlement_basis}"
                    ),
                )
            )
            running_balance = bankroll_after
            result.bankroll_before = bankroll_before
            result.bankroll_after = bankroll_after
            bets_settled += 1
            details.append(result)

        total_pl = sum((item.profit_loss for item in details if item.settled), Decimal("0"))
        return SettlementReport(
            fixtures_checked=len(fixtures_by_id),
            bets_eligible=bets_eligible,
            bets_settled=bets_settled,
            bets_skipped=bets_skipped,
            total_pl=total_pl,
            details=details,
            provider_requests=provider_requests,
            deferred_fixtures=deferred,
        )

    @staticmethod
    def _validate_void_evidence(
        evidence: AuthoritativeVoidEvidence,
        value_bet: ValueBet,
        fixture: Fixture,
    ) -> None:
        if evidence.value_bet_id != value_bet.id or evidence.fixture_id != fixture.id:
            raise RuntimeError("VOID evidence identity does not match the settlement candidate")
        if evidence.bookmaker_id != value_bet.bookmaker_id:
            raise RuntimeError("VOID evidence bookmaker does not match the value bet")

    @staticmethod
    def _void_result(
        value_bet: ValueBet,
        fixture: Fixture,
        evidence: AuthoritativeVoidEvidence,
    ) -> SettlementResult_:
        return SettlementResult_(
            value_bet_id=value_bet.id,
            fixture_id=fixture.id,
            result=SettlementResult.VOID,
            score_home=None,
            score_away=None,
            profit_loss=Decimal("0"),
            bankroll_before=Decimal("0"),
            bankroll_after=Decimal("0"),
            settled=True,
            message="authoritative VOID evidence accepted",
            void_reason_code=evidence.reason_code,
        )

    @classmethod
    def _authoritative_state(
        cls,
        fixture: Fixture,
        refreshed: ProviderFixture | None,
    ) -> AuthoritativeSettlementState:
        if refreshed is None:
            return AuthoritativeSettlementState(
                FixtureLifecycle.UNKNOWN, None, None, "provider fixture missing"
            )
        if refreshed.provider_id != fixture.external_id:
            return AuthoritativeSettlementState(
                FixtureLifecycle.UNKNOWN, None, None, "provider fixture identity mismatch"
            )
        lifecycle = cls._lifecycle(refreshed.status)
        if lifecycle not in {FixtureLifecycle.FT, FixtureLifecycle.AET, FixtureLifecycle.PEN}:
            return AuthoritativeSettlementState(
                lifecycle, None, None, f"fixture lifecycle {lifecycle.value} is not settleable"
            )
        regulation = cls._score_pair(
            refreshed.regulation_home_score,
            refreshed.regulation_away_score,
        )
        if regulation is not None:
            return AuthoritativeSettlementState(lifecycle, regulation, "provider.score.fulltime")
        # Aggregate goals are regulation-time only for a plain FT fixture.
        if lifecycle is FixtureLifecycle.FT:
            aggregate = cls._score_pair(refreshed.home_score, refreshed.away_score)
            if aggregate is not None:
                return AuthoritativeSettlementState(lifecycle, aggregate, "provider.goals")
        return AuthoritativeSettlementState(
            lifecycle, None, None, "authoritative regulation-time score unavailable"
        )

    @staticmethod
    def _score_pair(home: int | None, away: int | None) -> Score | None:
        if home is None or away is None or home < 0 or away < 0:
            return None
        return Score(home=home, away=away)

    @staticmethod
    def _lifecycle(raw_status: str) -> FixtureLifecycle:
        status = raw_status.strip().upper()
        if status in {"TBD", "NS"}:
            return FixtureLifecycle.NOT_STARTED
        if status in {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}:
            return FixtureLifecycle.LIVE
        if status in {"SUSP", "INT"}:
            return FixtureLifecycle.INTERRUPTED
        if status == "FT":
            return FixtureLifecycle.FT
        if status == "AET":
            return FixtureLifecycle.AET
        if status == "PEN":
            return FixtureLifecycle.PEN
        if status == "PST":
            return FixtureLifecycle.POSTPONED
        if status == "CANC":
            return FixtureLifecycle.CANCELLED
        if status == "ABD":
            return FixtureLifecycle.ABANDONED
        if status in {"AWD", "WO"}:
            return FixtureLifecycle.AWARDED_WALKOVER
        return FixtureLifecycle.UNKNOWN

    def _resolve(self, value_bet: ValueBet, fixture: Fixture, score: Score) -> SettlementResult_:
        home, away = score.home, score.away
        total = home + away
        market = value_bet.selection.market
        code = value_bet.selection.code
        if market == MarketType.MATCH_RESULT:
            if code == "home":
                return self._result(
                    value_bet,
                    fixture,
                    score,
                    SettlementResult.WIN if home > away else SettlementResult.LOSS,
                )
            if code == "draw":
                return self._result(
                    value_bet,
                    fixture,
                    score,
                    SettlementResult.WIN if home == away else SettlementResult.LOSS,
                )
            if code == "away":
                return self._result(
                    value_bet,
                    fixture,
                    score,
                    SettlementResult.WIN if away > home else SettlementResult.LOSS,
                )
        if market == MarketType.OVER_UNDER:
            line = value_bet.selection.line
            if line is None:
                return self._skip(value_bet, fixture, score, "Over/Under without line")
            if code == "over":
                outcome = (
                    SettlementResult.WIN
                    if total > line
                    else SettlementResult.PUSH if total == line else SettlementResult.LOSS
                )
                return self._result(value_bet, fixture, score, outcome)
            if code == "under":
                outcome = (
                    SettlementResult.WIN
                    if total < line
                    else SettlementResult.PUSH if total == line else SettlementResult.LOSS
                )
                return self._result(value_bet, fixture, score, outcome)
        if market == MarketType.BOTH_TEAMS_TO_SCORE:
            both_scored = home > 0 and away > 0
            if code == "yes":
                return self._result(
                    value_bet,
                    fixture,
                    score,
                    SettlementResult.WIN if both_scored else SettlementResult.LOSS,
                )
            if code == "no":
                return self._result(
                    value_bet,
                    fixture,
                    score,
                    SettlementResult.WIN if not both_scored else SettlementResult.LOSS,
                )
        return self._skip(value_bet, fixture, score, f"Unsupported market: {market.value}")

    @staticmethod
    def _result(
        value_bet: ValueBet,
        fixture: Fixture,
        score: Score,
        outcome: SettlementResult,
    ) -> SettlementResult_:
        stake = value_bet.stake
        if stake is None:
            return SettlementService._skip(value_bet, fixture, score, "No stake defined")
        amount = stake.amount.amount
        if outcome == SettlementResult.WIN:
            profit_loss = amount * (value_bet.odds.decimal - Decimal("1"))
        elif outcome == SettlementResult.PUSH:
            profit_loss = Decimal("0")
        else:
            profit_loss = -amount
        return SettlementResult_(
            value_bet.id,
            fixture.id,
            outcome,
            score.home,
            score.away,
            profit_loss,
            Decimal("0"),
            Decimal("0"),
            True,
        )

    @staticmethod
    def _skip(
        value_bet: ValueBet,
        fixture: Fixture,
        score: Score,
        reason: str,
    ) -> SettlementResult_:
        return SettlementResult_(
            value_bet.id,
            fixture.id,
            SettlementResult.PUSH,
            score.home,
            score.away,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            False,
            reason,
        )


_VOID_EVIDENCE_LIFECYCLES = frozenset(
    {
        FixtureLifecycle.INTERRUPTED,
        FixtureLifecycle.POSTPONED,
        FixtureLifecycle.CANCELLED,
        FixtureLifecycle.ABANDONED,
        FixtureLifecycle.AWARDED_WALKOVER,
        FixtureLifecycle.UNKNOWN,
    }
)
