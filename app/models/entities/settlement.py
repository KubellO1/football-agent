"""Settlement entity — records the outcome of a settled value bet."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.models.entities.base import Entity, utcnow


class SettlementResult(str, Enum):
    WIN = "W"
    LOSS = "L"
    PUSH = "P"


@dataclass(eq=False, kw_only=True)
class Settlement(Entity):
    """Resolved outcome for one value bet after the fixture concludes."""

    value_bet_id: UUID
    fixture_id: UUID
    result: SettlementResult
    score_home: int
    score_away: int
    profit_loss: Decimal
    closing_odds: Decimal | None = None
    clv: float | None = None
    bankroll_before: Decimal | None = None
    bankroll_after: Decimal | None = None
    settlement_timestamp: datetime = field(default_factory=utcnow)


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
    breakdown_json: dict | None = None
    created_at: datetime = field(default_factory=utcnow)
