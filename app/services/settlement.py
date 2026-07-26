"""自动结算服务：将已完赛比赛与未结算 value_bets 匹配，计算 P&L 并记录。

数据流：
  1. 查询所有 FINISHED + score NOT NULL 的 fixtures
  2. 找出这些 fixture 下所有未结算的 value_bets
  3. 逐条判定 W/L/P（基于 selection vs 实际比分）
  4. 计算 P&L，更新 bankroll
  5. 写入 settlements + bankroll_entries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.core.logging import get_logger
from app.models.entities.fixture import Fixture
from app.models.entities.settlement import (
    BankrollEntry,
    Settlement,
    SettlementResult,
)
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.markets import MarketType
from app.models.value_objects.money import Money
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.settlement_repository import (
    BankrollRepository,
    SettlementRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository

logger = get_logger(__name__)


@dataclass
class SettlementResult_:
    """单条结算结果（内部报告用，避免与实体 SettlementResult 冲突）。"""

    value_bet_id: UUID
    fixture_id: UUID
    result: SettlementResult
    score_home: int
    score_away: int
    profit_loss: Decimal
    bankroll_before: Decimal
    bankroll_after: Decimal
    settled: bool
    message: str = ""


@dataclass
class SettlementReport:
    """一次结算运行的汇总报告。"""

    fixtures_checked: int
    bets_eligible: int
    bets_settled: int
    bets_skipped: int  # already settled or unsupported market
    total_pl: Decimal
    details: list[SettlementResult_] = field(default_factory=list)


class SettlementService:
    """自动结算服务。

    结算判定逻辑：
      - 1x2:home   → score_home > score_away = W, < = L, == = P
      - 1x2:draw   → score_home == score_away = W
      - 1x2:away   → score_away > score_home = W
      - over_under:over@line  → total_goals > line = W, == line = P
      - over_under:under@line → total_goals < line = W, == line = P
      - btts:yes → both scored = W
      - btts:no  → at least one didn't score = W
      - 其他市场 → 跳过（标记为未支持）
    """

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        value_bets: ValueBetRepository,
        settlements: SettlementRepository,
        bankroll: BankrollRepository,
        initial_bankroll: Money | None = None,
    ) -> None:
        self._fixtures = fixtures
        self._value_bets = value_bets
        self._settlements = settlements
        self._bankroll = bankroll
        self._initial_bankroll = initial_bankroll

    async def settle_all(self) -> SettlementReport:
        """结算所有已完赛但未结算的比赛。"""
        # 1. 获取所有已完赛且有比分的比赛
        finished = await self._fixtures.list_finished()
        scored = [f for f in finished if f.score is not None]

        if not scored:
            return SettlementReport(
                fixtures_checked=len(finished),
                bets_eligible=0,
                bets_settled=0,
                bets_skipped=0,
                total_pl=Decimal("0"),
            )

        # 2. 获取未结算的 value_bet IDs
        unsettled_ids = set(await self._settlements.list_unsettled_value_bet_ids())
        fixture_ids = {f.id for f in scored}

        # 3. 按 fixture 匹配
        details: list[SettlementResult_] = []
        bets_eligible = 0
        bets_settled = 0
        bets_skipped = 0

        for fixture in scored:
            vbs = await self._value_bets.list_by_fixture(fixture.id)
            for vb in vbs:
                if vb.id not in unsettled_ids:
                    continue
                bets_eligible += 1
                sr = self._resolve(vb, fixture)
                if not sr.settled:
                    bets_skipped += 1
                    details.append(sr)
                    continue

                # 写入结算记录
                br_before = await self._bankroll.get_latest_balance()
                settlement = Settlement(
                    value_bet_id=vb.id,
                    fixture_id=fixture.id,
                    result=sr.result,
                    score_home=sr.score_home,
                    score_away=sr.score_away,
                    profit_loss=sr.profit_loss,
                    bankroll_before=br_before,
                    bankroll_after=br_before + sr.profit_loss,
                    settlement_timestamp=datetime.utcnow(),
                )
                await self._settlements.add(settlement)

                # 记录 bankroll 变动
                new_balance = br_before + sr.profit_loss
                entry = BankrollEntry(
                    amount=sr.profit_loss,
                    balance_after=new_balance,
                    reason=f"Settlement: {fixture.id} | {vb.selection.label} → {sr.result.value}",
                )
                await self._bankroll.add(entry)

                sr.bankroll_before = br_before
                sr.bankroll_after = new_balance
                bets_settled += 1
                details.append(sr)

        total_pl = sum((d.profit_loss for d in details if d.settled), Decimal("0"))
        return SettlementReport(
            fixtures_checked=len(finished),
            bets_eligible=bets_eligible,
            bets_settled=bets_settled,
            bets_skipped=bets_skipped,
            total_pl=total_pl,
            details=details,
        )

    def _resolve(self, vb: ValueBet, fixture: Fixture) -> SettlementResult_:
        """判定单条 value_bet 的结算结果。"""
        assert fixture.score is not None
        home = fixture.score.home
        away = fixture.score.away
        total = home + away

        market = vb.selection.market
        code = vb.selection.code

        # 1x2
        if market == MarketType.MATCH_RESULT:
            if code == "home":
                return self._result(vb, fixture, SettlementResult.WIN if home > away else (SettlementResult.PUSH if home == away else SettlementResult.LOSS))
            elif code == "draw":
                return self._result(vb, fixture, SettlementResult.WIN if home == away else SettlementResult.LOSS)
            elif code == "away":
                return self._result(vb, fixture, SettlementResult.WIN if away > home else (SettlementResult.PUSH if away == home else SettlementResult.LOSS))

        # Over/Under
        if market == MarketType.OVER_UNDER:
            line = vb.selection.line
            if line is None:
                return self._skip(vb, fixture, "Over/Under without line")
            if code == "over":
                if total > line:
                    return self._result(vb, fixture, SettlementResult.WIN)
                elif total == line:
                    return self._result(vb, fixture, SettlementResult.PUSH)
                else:
                    return self._result(vb, fixture, SettlementResult.LOSS)
            elif code == "under":
                if total < line:
                    return self._result(vb, fixture, SettlementResult.WIN)
                elif total == line:
                    return self._result(vb, fixture, SettlementResult.PUSH)
                else:
                    return self._result(vb, fixture, SettlementResult.LOSS)

        # BTTS
        if market == MarketType.BOTH_TEAMS_TO_SCORE:
            both_scored = home > 0 and away > 0
            if code == "yes":
                return self._result(vb, fixture, SettlementResult.WIN if both_scored else SettlementResult.LOSS)
            elif code == "no":
                return self._result(vb, fixture, SettlementResult.WIN if not both_scored else SettlementResult.LOSS)

        return self._skip(vb, fixture, f"Unsupported market: {market.value}")

    def _result(
        self, vb: ValueBet, fixture: Fixture, sr: SettlementResult
    ) -> SettlementResult_:
        """计算 P&L。"""
        stake = vb.stake
        if stake is None:
            return self._skip(vb, fixture, "No stake defined")

        amount = stake.amount.amount
        odds = vb.odds.decimal

        if sr == SettlementResult.WIN:
            pl = amount * (odds - Decimal("1"))
        elif sr == SettlementResult.PUSH:
            pl = Decimal("0")
        else:
            pl = -amount

        assert fixture.score is not None
        return SettlementResult_(
            value_bet_id=vb.id,
            fixture_id=fixture.id,
            result=sr,
            score_home=fixture.score.home,
            score_away=fixture.score.away,
            profit_loss=pl,
            bankroll_before=Decimal("0"),
            bankroll_after=Decimal("0"),
            settled=True,
        )

    @staticmethod
    def _skip(vb: ValueBet, fixture: Fixture, reason: str) -> SettlementResult_:
        assert fixture.score is not None
        return SettlementResult_(
            value_bet_id=vb.id,
            fixture_id=fixture.id,
            result=SettlementResult.PUSH,
            score_home=fixture.score.home,
            score_away=fixture.score.away,
            profit_loss=Decimal("0"),
            bankroll_before=Decimal("0"),
            bankroll_after=Decimal("0"),
            settled=False,
            message=reason,
        )
