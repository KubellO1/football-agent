"""Settlement entity — records the outcome of a settled value bet."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.entities.base import Entity, utcnow

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class SettlementResult(StrEnum):
    WIN = "W"
    LOSS = "L"
    PUSH = "P"
    VOID = "V"


@dataclass(eq=False, kw_only=True)
class Settlement(Entity):
    """Resolved outcome for one value bet after the fixture concludes."""

    value_bet_id: UUID
    fixture_id: UUID
    result: SettlementResult
    score_home: int | None
    score_away: int | None
    profit_loss: Decimal
    void_reason_code: str | None = None
    closing_odds: Decimal | None = None
    clv: float | None = None
    bankroll_before: Decimal | None = None
    bankroll_after: Decimal | None = None
    settlement_timestamp: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.result is SettlementResult.VOID:
            reason = (self.void_reason_code or "").strip()
            if self.score_home is not None or self.score_away is not None:
                raise ValueError("VOID settlement must not contain a score")
            if self.profit_loss != Decimal("0"):
                raise ValueError("VOID settlement profit/loss must be zero")
            if not reason:
                raise ValueError("VOID settlement requires a non-empty reason code")
            if len(reason) > 64:
                raise ValueError("VOID settlement reason code must be at most 64 characters")
            self.void_reason_code = reason
            return

        if self.score_home is None or self.score_away is None:
            raise ValueError("W/L/P settlement requires a regulation-time score")
        if self.void_reason_code is not None:
            raise ValueError("W/L/P settlement must not contain a VOID reason code")


@dataclass(eq=False, kw_only=True)
class BankrollEntry(Entity):
    """A single change to the tracked bankroll (initialisation, settlement, adjustment)."""

    amount: Decimal
    balance_after: Decimal
    reason: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass(eq=False, kw_only=True)
class PerformanceSnapshot(Entity):
    """A periodic snapshot of aggregated performance statistics."""

    period_start: datetime
    period_end: datetime
    total_bets: int
    win_count: int
    push_count: int
    loss_count: int
    win_rate: float | None = None
    total_pl: Decimal | None = None
    roi: float | None = None
    avg_ev: float | None = None
    avg_clv: float | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    max_drawdown: float | None = None
    sharpe_ratio: float | None = None
    breakdown_json: dict[str, object] | None = None
    created_at: datetime = field(default_factory=utcnow)
