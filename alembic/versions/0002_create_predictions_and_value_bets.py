"""create predictions and value_bets tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02

第二个迁移：创建 predictions 与 value_bets 表（Prediction / ValueBet 聚合）。
手写以与 ORM 模型保持一致；后续可用 autogenerate。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("prob_home", sa.Float(), nullable=True),
        sa.Column("prob_draw", sa.Float(), nullable=True),
        sa.Column("prob_away", sa.Float(), nullable=True),
        sa.Column("xg_home", sa.Float(), nullable=True),
        sa.Column("xg_away", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(length=50), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_predictions_fixture_id", "predictions", ["fixture_id"])

    op.create_table(
        "value_bets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("selection_market", sa.String(length=30), nullable=False),
        sa.Column("selection_code", sa.String(length=30), nullable=False),
        sa.Column("selection_line", sa.Float(), nullable=True),
        sa.Column("odds_decimal", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("bookmaker_id", sa.Uuid(), nullable=True),
        sa.Column("model_probability", sa.Float(), nullable=False),
        sa.Column("stake_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stake_currency", sa.String(length=3), nullable=True),
        sa.Column("stake_fraction", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_value_bets_fixture_id", "value_bets", ["fixture_id"])
    op.create_index("ix_value_bets_created_at", "value_bets", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_value_bets_created_at", table_name="value_bets")
    op.drop_index("ix_value_bets_fixture_id", table_name="value_bets")
    op.drop_table("value_bets")
    op.drop_index("ix_predictions_fixture_id", table_name="predictions")
    op.drop_table("predictions")
