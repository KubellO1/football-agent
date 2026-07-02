"""create decision_logs table

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-02

第四个迁移：创建决策日志表（宪法第 16 节可追溯性）。列表字段用 JSON 存储。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("value_bet_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("risks", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rejected_alternatives", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("change_conditions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
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
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], name="fk_decision_logs_fixture"),
        sa.ForeignKeyConstraint(
            ["value_bet_id"], ["value_bets.id"], name="fk_decision_logs_value_bet"
        ),
    )
    op.create_index("ix_decision_logs_fixture_id", "decision_logs", ["fixture_id"])
    op.create_index("ix_decision_logs_created_at", "decision_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_decision_logs_created_at", table_name="decision_logs")
    op.drop_index("ix_decision_logs_fixture_id", table_name="decision_logs")
    op.drop_table("decision_logs")
