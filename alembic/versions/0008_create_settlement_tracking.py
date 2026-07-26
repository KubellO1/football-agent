"""add settlement tracking: settlements, bankroll_entries, performance_snapshots

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-11

第八个迁移（结算与追踪层）：新增三张表完成生产追踪闭环——
- settlements: 每条 value_bet 的比赛结果与 P&L，唯一约束防重复结算
- bankroll_entries: 银行余额流水（初始化/结算/调整）
- performance_snapshots: 定期性能统计快照
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. settlements
    op.create_table(
        "settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("value_bet_id", sa.Uuid(), sa.ForeignKey("value_bets.id"), nullable=False, index=True),
        sa.Column("fixture_id", sa.Uuid(), sa.ForeignKey("fixtures.id"), nullable=False, index=True),
        sa.Column("result", sa.String(length=1), nullable=False),
        sa.Column("score_home", sa.Integer(), nullable=False),
        sa.Column("score_away", sa.Integer(), nullable=False),
        sa.Column("profit_loss", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("closing_odds", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("clv", sa.Float(), nullable=True),
        sa.Column("bankroll_before", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("bankroll_after", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("settlement_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("value_bet_id", name="uq_settlements_value_bet"),
    )

    # 2. bankroll_entries
    op.create_table(
        "bankroll_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 3. performance_snapshots
    op.create_table(
        "performance_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_bets", sa.Integer(), nullable=False),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("push_count", sa.Integer(), nullable=False),
        sa.Column("loss_count", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("total_pl", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("roi", sa.Float(), nullable=True),
        sa.Column("avg_ev", sa.Float(), nullable=True),
        sa.Column("avg_clv", sa.Float(), nullable=True),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("log_loss", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("breakdown_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("performance_snapshots")
    op.drop_table("bankroll_entries")
    op.drop_table("settlements")
