"""性能追踪服务：结算后更新性能指标快照。

从 settlements 表读取所有已结算记录，结合 value_bets 的 EV/置信度/赔率数据，
计算完整的性能统计并持久化到 performance_snapshots 表。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.core.logging import get_logger
from app.models.entities.settlement import PerformanceSnapshot
from app.models.entities.settlement import SettlementResult as SR
from app.repositories.interfaces.settlement_repository import (
    PerformanceSnapshotRepository,
    SettlementRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository

logger = get_logger(__name__)


@dataclass
class PerformanceReport:
    period_start: datetime
    period_end: datetime
    total_bets: int
    win_count: int
    push_count: int
    loss_count: int
    win_rate: float | None
    total_pl: Decimal
    roi: float | None
    avg_ev: float | None
    avg_clv: float | None
    max_drawdown: float | None
    snapshot_id: str | None = None


class PerformanceTracker:
    """结算后性能统计与快照持久化。"""

    def __init__(
        self,
        *,
        settlements: SettlementRepository,
        value_bets: ValueBetRepository,
        snapshots: PerformanceSnapshotRepository,
    ) -> None:
        self._settlements = settlements
        self._value_bets = value_bets
        self._snapshots = snapshots

    async def update(self) -> PerformanceReport:
        """基于所有已结算记录更新性能快照（全量计算）。"""
        now = datetime.now(timezone.utc)

        # 获取全部已结算记录（不使用增量窗口，避免 settlement 时间早于上次快照 period_end 的情况）
        settled = await self._settlements.list_all()
        if not settled:
            return PerformanceReport(
                period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
                period_end=now,
                total_bets=0,
                win_count=0,
                push_count=0,
                loss_count=0,
                win_rate=None,
                total_pl=Decimal("0"),
                roi=None,
                avg_ev=None,
                avg_clv=None,
                max_drawdown=None,
            )

        total = len(settled)
        wins = sum(1 for s in settled if s.result == SR.WIN)
        pushes = sum(1 for s in settled if s.result == SR.PUSH)
        losses = sum(1 for s in settled if s.result == SR.LOSS)
        pl_list = [s.profit_loss for s in settled]
        total_pl = sum(pl_list, Decimal("0"))

        win_rate = wins / total if total > 0 else None

        # 加载 EV/CLV 数据
        evs: list[float] = []
        clvs: list[float] = []
        for s in settled:
            vb = await self._value_bets.get(s.value_bet_id)
            if vb is not None:
                if vb.edge is not None:
                    evs.append(vb.edge.expected_value_per_unit)
                if s.clv is not None:
                    clvs.append(s.clv)

        avg_ev = sum(evs) / len(evs) if evs else None
        avg_clv = sum(clvs) / len(clvs) if clvs else None

        # max drawdown (简单累积)
        max_dd = self._calc_max_drawdown([float(p) for p in pl_list]) if pl_list else None

        # ROI (简化: total_pl / 总 stakes)
        total_stakes = Decimal("0")
        for s in settled:
            vb = await self._value_bets.get(s.value_bet_id)
            if vb is not None and vb.stake is not None:
                total_stakes += vb.stake.amount.amount
        roi = float(total_pl / total_stakes) if total_stakes > 0 else None

        snapshot = PerformanceSnapshot(
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            period_end=now,
            total_bets=total,
            win_count=wins,
            push_count=pushes,
            loss_count=losses,
            win_rate=win_rate,
            total_pl=total_pl,
            roi=roi,
            avg_ev=avg_ev,
            avg_clv=avg_clv,
            max_drawdown=max_dd,
        )
        saved = await self._snapshots.add(snapshot)

        return PerformanceReport(
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            period_end=now,
            total_bets=total,
            win_count=wins,
            push_count=pushes,
            loss_count=losses,
            win_rate=win_rate,
            total_pl=total_pl,
            roi=roi,
            avg_ev=avg_ev,
            avg_clv=avg_clv,
            max_drawdown=max_dd,
            snapshot_id=str(saved.id),
        )

    @staticmethod
    def _calc_max_drawdown(pl_sequence: list[float]) -> float | None:
        if not pl_sequence:
            return None
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for pl in pl_sequence:
            cumulative += pl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd if max_dd > 0 else 0.0
