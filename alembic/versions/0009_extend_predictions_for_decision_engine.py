"""extend predictions table with decision-engine output columns

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-11

第九个迁移：predictions 表扩展，从「仅存数学概率」升级为「完整决策引擎输出存储」。
每行对应一个 selection（1X2 选项）的完整决策记录，包含：
- 比赛上下文（kickoff_time, competition, home_team, away_team）
- 预测元数据（prediction_timestamp, prediction_version, provider_sources）
- 市场决策（market, selection, odds, market_probability, model_probability）
- 价值评估（expected_value, kelly_stake, confidence）
- 最终判定（final_decision: BET/WATCH/NO_BET, why_not_bet, confidence_killer）
- 数据质量（data_quality）

原有列（prob_home/prob_draw/prob_away/xg_home/xg_away）保留并可空，
兼容旧的 per-fixture 预测记录。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("kickoff_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("predictions", sa.Column("competition", sa.String(length=120), nullable=True))
    op.add_column("predictions", sa.Column("home_team", sa.String(length=120), nullable=True))
    op.add_column("predictions", sa.Column("away_team", sa.String(length=120), nullable=True))
    op.add_column("predictions", sa.Column("prediction_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("predictions", sa.Column("prediction_version", sa.String(length=50), nullable=True))
    op.add_column("predictions", sa.Column("market", sa.String(length=30), nullable=True))
    op.add_column("predictions", sa.Column("selection", sa.String(length=30), nullable=True))
    op.add_column("predictions", sa.Column("odds", sa.Numeric(precision=6, scale=3), nullable=True))
    op.add_column("predictions", sa.Column("market_probability", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("model_probability", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("expected_value", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("kelly_stake", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("final_decision", sa.String(length=10), nullable=True))
    op.add_column("predictions", sa.Column("why_not_bet", sa.Text(), nullable=True))
    op.add_column("predictions", sa.Column("confidence_killer", sa.Text(), nullable=True))
    op.add_column("predictions", sa.Column("provider_sources", sa.JSON(), nullable=True))
    op.add_column("predictions", sa.Column("data_quality", sa.Float(), nullable=True))

    # 索引：按 fixture_id + final_decision 组合查询（结算/性能统计常用）
    op.create_index("ix_predictions_fixture_decision", "predictions", ["fixture_id", "final_decision"])


def downgrade() -> None:
    op.drop_index("ix_predictions_fixture_decision")
    op.drop_column("predictions", "data_quality")
    op.drop_column("predictions", "provider_sources")
    op.drop_column("predictions", "confidence_killer")
    op.drop_column("predictions", "why_not_bet")
    op.drop_column("predictions", "final_decision")
    op.drop_column("predictions", "confidence")
    op.drop_column("predictions", "kelly_stake")
    op.drop_column("predictions", "expected_value")
    op.drop_column("predictions", "model_probability")
    op.drop_column("predictions", "market_probability")
    op.drop_column("predictions", "odds")
    op.drop_column("predictions", "selection")
    op.drop_column("predictions", "market")
    op.drop_column("predictions", "prediction_version")
    op.drop_column("predictions", "prediction_timestamp")
    op.drop_column("predictions", "away_team")
    op.drop_column("predictions", "home_team")
    op.drop_column("predictions", "competition")
    op.drop_column("predictions", "kickoff_time")
