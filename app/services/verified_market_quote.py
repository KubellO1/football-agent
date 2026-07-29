"""从赔率快照构建无未来泄漏、可追溯的 1X2 市场报价。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from math import isfinite
from statistics import median
from typing import TYPE_CHECKING

from app.models.value_objects.markets import MarketType
from app.services.modeling import MarketQuote

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from app.models.entities.odds_snapshot import OddsSnapshot
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository

_MATCH_RESULT_CODES = ("home", "draw", "away")


class MarketQuoteVerificationStatus(StrEnum):
    """完整市场报价的验证状态。"""

    VERIFIED = "verified"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"


class MarketQuoteRejectionReason(StrEnum):
    """报价拒绝原因；用于决策日志和数据质量审计。"""

    NO_SNAPSHOTS = "no_snapshots"
    NO_SUPPORTED_MARKET = "no_supported_market"
    MISSING_SELECTION = "missing_selection"
    STALE_SELECTION = "stale_selection"
    OUTLIER_FILTER_EMPTY = "outlier_filter_empty"
    INSUFFICIENT_BOOKMAKERS = "insufficient_bookmakers"


@dataclass(frozen=True, slots=True)
class VerifiedMarketQuotePolicy:
    """赔率新鲜度、交叉验证数量和异常值阈值。"""

    maximum_age: timedelta
    minimum_bookmakers: int = 2
    maximum_relative_deviation: float = 0.2

    def __post_init__(self) -> None:
        if self.maximum_age <= timedelta(0):
            raise ValueError("maximum_age must be positive")
        if (
            not isinstance(self.minimum_bookmakers, int)
            or isinstance(self.minimum_bookmakers, bool)
            or self.minimum_bookmakers < 2
        ):
            raise ValueError("minimum_bookmakers must be an integer of at least 2")
        if (
            isinstance(self.maximum_relative_deviation, bool)
            or not isfinite(self.maximum_relative_deviation)
            or not 0.0 < self.maximum_relative_deviation < 1.0
        ):
            raise ValueError("maximum_relative_deviation must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class MarketQuoteIssue:
    """某个选项或整个市场的拒绝证据。"""

    reason: MarketQuoteRejectionReason
    selection_code: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedSelectionQuote:
    """代表性真实报价及其市场共识和来源快照。"""

    quote: MarketQuote
    captured_at: datetime
    consensus_decimal_odds: Decimal
    contributing_snapshot_ids: tuple[UUID, ...]
    contributing_bookmaker_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if self.quote.bookmaker_id is None:
            raise ValueError("verified quote requires a bookmaker")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.consensus_decimal_odds <= 1:
            raise ValueError("consensus_decimal_odds must be greater than 1")
        if not self.contributing_snapshot_ids:
            raise ValueError("verified quote requires contributing snapshots")
        if len(self.contributing_snapshot_ids) != len(self.contributing_bookmaker_ids):
            raise ValueError("snapshot and bookmaker provenance counts must match")
        if len(set(self.contributing_snapshot_ids)) != len(self.contributing_snapshot_ids):
            raise ValueError("contributing snapshots must be unique")
        if len(set(self.contributing_bookmaker_ids)) != len(self.contributing_bookmaker_ids):
            raise ValueError("contributing bookmakers must be unique")
        if self.quote.bookmaker_id not in self.contributing_bookmaker_ids:
            raise ValueError("quote bookmaker must be part of the contributing market")


@dataclass(frozen=True, slots=True)
class VerifiedMarketQuoteResult:
    """完整 1X2 市场的验证结果；失败时不暴露部分报价。"""

    status: MarketQuoteVerificationStatus
    as_of: datetime
    quotes: tuple[VerifiedSelectionQuote, ...] = ()
    issues: tuple[MarketQuoteIssue, ...] = ()
    observed_snapshot_count: int = 0
    eligible_snapshot_count: int = 0

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if self.observed_snapshot_count < 0 or self.eligible_snapshot_count < 0:
            raise ValueError("snapshot counts cannot be negative")
        if self.eligible_snapshot_count > self.observed_snapshot_count:
            raise ValueError("eligible snapshots cannot exceed observed snapshots")
        if self.status is MarketQuoteVerificationStatus.VERIFIED:
            if len(self.quotes) != len(_MATCH_RESULT_CODES) or self.issues:
                raise ValueError("verified market requires three quotes and no issues")
            if tuple(item.quote.selection.code for item in self.quotes) != _MATCH_RESULT_CODES:
                raise ValueError("verified quotes must use deterministic 1X2 order")
        elif self.quotes or not self.issues:
            raise ValueError("rejected or missing market requires issues and no quotes")

    @property
    def accepted(self) -> bool:
        return self.status is MarketQuoteVerificationStatus.VERIFIED

    @property
    def market_quotes(self) -> tuple[MarketQuote, ...]:
        """只暴露经过完整市场准入的模型报价。"""
        return tuple(item.quote for item in self.quotes)


class VerifiedMarketQuoteService:
    """按决策时点验证 1X2 报价，并选择接近市场中位数的真实快照。"""

    def __init__(
        self,
        *,
        repository: OddsSnapshotRepository,
        policy: VerifiedMarketQuotePolicy,
    ) -> None:
        self._repository = repository
        self._policy = policy

    async def verify(
        self,
        fixture_id: UUID,
        *,
        as_of: datetime,
    ) -> VerifiedMarketQuoteResult:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        snapshots = await self._repository.list_by_fixture(fixture_id, as_of=as_of)
        if any(snapshot.captured_at > as_of for snapshot in snapshots):
            raise ValueError("repository returned a snapshot later than as_of")
        if not snapshots:
            return self._failure(
                MarketQuoteVerificationStatus.NOT_FOUND,
                as_of,
                (MarketQuoteIssue(MarketQuoteRejectionReason.NO_SNAPSHOTS),),
            )

        supported = [
            snapshot
            for snapshot in snapshots
            if snapshot.selection.market is MarketType.MATCH_RESULT
            and snapshot.selection.code in _MATCH_RESULT_CODES
        ]
        if not supported:
            return self._failure(
                MarketQuoteVerificationStatus.NOT_FOUND,
                as_of,
                (MarketQuoteIssue(MarketQuoteRejectionReason.NO_SUPPORTED_MARKET),),
                observed_snapshot_count=len(snapshots),
            )

        latest = self._latest_by_selection_and_bookmaker(supported)
        quotes: list[VerifiedSelectionQuote] = []
        issues: list[MarketQuoteIssue] = []
        eligible_count = 0
        freshness_boundary = as_of - self._policy.maximum_age

        for code in _MATCH_RESULT_CODES:
            selection_snapshots = [
                snapshot
                for (selection_code, _), snapshot in latest.items()
                if selection_code == code
            ]
            if not selection_snapshots:
                issues.append(
                    MarketQuoteIssue(
                        MarketQuoteRejectionReason.MISSING_SELECTION,
                        selection_code=code,
                    )
                )
                continue

            fresh = [
                snapshot
                for snapshot in selection_snapshots
                if snapshot.captured_at >= freshness_boundary
            ]
            if not fresh:
                issues.append(
                    MarketQuoteIssue(
                        MarketQuoteRejectionReason.STALE_SELECTION,
                        selection_code=code,
                    )
                )
                continue

            consensus = median(snapshot.odds.decimal for snapshot in fresh)
            kept = [
                snapshot
                for snapshot in fresh
                if abs(snapshot.odds.decimal - consensus) / consensus
                <= self._policy.maximum_relative_deviation
            ]
            if not kept:
                issues.append(
                    MarketQuoteIssue(
                        MarketQuoteRejectionReason.OUTLIER_FILTER_EMPTY,
                        selection_code=code,
                    )
                )
                continue

            eligible_count += len(kept)
            if len(kept) < self._policy.minimum_bookmakers:
                issues.append(
                    MarketQuoteIssue(
                        MarketQuoteRejectionReason.INSUFFICIENT_BOOKMAKERS,
                        selection_code=code,
                    )
                )
                continue
            quotes.append(self._build_quote(kept))

        if issues:
            return self._failure(
                MarketQuoteVerificationStatus.REJECTED,
                as_of,
                tuple(issues),
                observed_snapshot_count=len(snapshots),
                eligible_snapshot_count=eligible_count,
            )
        return VerifiedMarketQuoteResult(
            status=MarketQuoteVerificationStatus.VERIFIED,
            as_of=as_of,
            quotes=tuple(quotes),
            observed_snapshot_count=len(snapshots),
            eligible_snapshot_count=eligible_count,
        )

    @staticmethod
    def _latest_by_selection_and_bookmaker(
        snapshots: list[OddsSnapshot],
    ) -> dict[tuple[str, UUID], OddsSnapshot]:
        latest: dict[tuple[str, UUID], OddsSnapshot] = {}
        for snapshot in snapshots:
            key = (snapshot.selection.code, snapshot.bookmaker_id)
            current = latest.get(key)
            if current is None or (snapshot.captured_at, snapshot.id.int) > (
                current.captured_at,
                current.id.int,
            ):
                latest[key] = snapshot
        return latest

    @staticmethod
    def _build_quote(snapshots: list[OddsSnapshot]) -> VerifiedSelectionQuote:
        consensus = median(snapshot.odds.decimal for snapshot in snapshots)
        ordered = sorted(
            snapshots, key=lambda snapshot: (snapshot.bookmaker_id.int, snapshot.id.int)
        )
        representative = min(
            ordered,
            key=lambda snapshot: abs(snapshot.odds.decimal - consensus),
        )
        return VerifiedSelectionQuote(
            quote=MarketQuote(
                selection=representative.selection,
                odds=representative.odds,
                bookmaker_id=representative.bookmaker_id,
            ),
            captured_at=representative.captured_at,
            consensus_decimal_odds=consensus,
            contributing_snapshot_ids=tuple(snapshot.id for snapshot in ordered),
            contributing_bookmaker_ids=tuple(snapshot.bookmaker_id for snapshot in ordered),
        )

    @staticmethod
    def _failure(
        status: MarketQuoteVerificationStatus,
        as_of: datetime,
        issues: tuple[MarketQuoteIssue, ...],
        *,
        observed_snapshot_count: int = 0,
        eligible_snapshot_count: int = 0,
    ) -> VerifiedMarketQuoteResult:
        return VerifiedMarketQuoteResult(
            status=status,
            as_of=as_of,
            issues=issues,
            observed_snapshot_count=observed_snapshot_count,
            eligible_snapshot_count=eligible_snapshot_count,
        )
