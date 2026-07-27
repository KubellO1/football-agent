"""classify aggregate and decision prediction records

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-27

predictions 表同时保存聚合数学预测和逐 selection 决策记录。本迁移增加显式类型，
避免仓储把决策日志错误映射成空概率的 MatchPrediction。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column(
            "record_kind",
            sa.String(length=20),
            server_default=sa.text("'aggregate'"),
            nullable=False,
        ),
    )
    op.execute("""
        UPDATE predictions
        SET record_kind = 'decision'
        WHERE prediction_timestamp IS NOT NULL
        """)
    op.create_check_constraint(
        "ck_predictions_record_kind",
        "predictions",
        "record_kind IN ('aggregate', 'decision')",
    )
    op.create_index(
        "ix_predictions_fixture_kind_generated",
        "predictions",
        ["fixture_id", "record_kind", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_predictions_fixture_kind_generated",
        table_name="predictions",
    )
    op.drop_constraint(
        "ck_predictions_record_kind",
        "predictions",
        type_="check",
    )
    op.drop_column("predictions", "record_kind")
