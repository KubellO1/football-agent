"""create odds_snapshots table and bookmaker external ref

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-02

第六个迁移（赔率采集）：
- 为 bookmakers 增加 external_source/external_id 两列及唯一约束（采集幂等键）。
- 新建 odds_snapshots 表（赔率时间序列）。幂等键为
  (fixture_id, bookmaker_id, selection_market, selection_code, selection_line,
  captured_at) 唯一约束，且用 NULLS NOT DISTINCT（Postgres 15+）——因为 1x2 的
  selection_line 恒为 NULL，默认 NULL 互不相等会破坏去重。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- bookmakers 幂等键 ---
    op.add_column("bookmakers", sa.Column("external_source", sa.String(length=40), nullable=True))
    op.add_column("bookmakers", sa.Column("external_id", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_bookmakers_external", "bookmakers", ["external_source", "external_id"]
    )

    # --- odds_snapshots ---
    op.create_table(
        "odds_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("bookmaker_id", sa.Uuid(), nullable=False),
        sa.Column("selection_market", sa.String(length=30), nullable=False),
        sa.Column("selection_code", sa.String(length=30), nullable=False),
        sa.Column("selection_line", sa.Float(), nullable=True),
        sa.Column("odds_decimal", sa.Numeric(9, 3), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], name="fk_odds_snapshots_fixture"),
        sa.ForeignKeyConstraint(
            ["bookmaker_id"], ["bookmakers.id"], name="fk_odds_snapshots_bookmaker"
        ),
        sa.UniqueConstraint(
            "fixture_id",
            "bookmaker_id",
            "selection_market",
            "selection_code",
            "selection_line",
            "captured_at",
            name="uq_odds_snapshots_natural",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_odds_snapshots_fixture_id", "odds_snapshots", ["fixture_id"])
    op.create_index("ix_odds_snapshots_bookmaker_id", "odds_snapshots", ["bookmaker_id"])
    op.create_index("ix_odds_snapshots_captured_at", "odds_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_odds_snapshots_captured_at", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_bookmaker_id", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_fixture_id", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")

    op.drop_constraint("uq_bookmakers_external", "bookmakers", type_="unique")
    op.drop_column("bookmakers", "external_id")
    op.drop_column("bookmakers", "external_source")
