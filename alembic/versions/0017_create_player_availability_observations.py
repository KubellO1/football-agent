"""create player availability observations

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-31

创建追加式球员可用性观察表，显式保存未知状态、来源证据和决策时点。
player_id 暂不添加外键，等待 Player 聚合的正式持久化边界建立。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_availability_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_name", sa.String(length=80), nullable=False),
        sa.Column("evidence_level", sa.String(length=1), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("expected_return", sa.Date(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('unknown', 'available', 'doubtful', 'out', 'suspended', 'returned')",
            name="ck_player_availability_status",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_player_availability_evidence_level",
        ),
        sa.CheckConstraint(
            "source_updated_at IS NULL OR source_updated_at <= captured_at",
            name="ck_player_availability_source_updated_at",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["fixtures.id"],
            name="fk_player_availability_fixture",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_player_availability_team",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fixture_id",
            "player_id",
            "source_name",
            "captured_at",
            name="uq_player_availability_observations_natural",
        ),
    )
    op.create_index(
        "ix_player_availability_fixture_captured",
        "player_availability_observations",
        ["fixture_id", "captured_at"],
    )
    op.create_index(
        "ix_player_availability_player_captured",
        "player_availability_observations",
        ["player_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_availability_player_captured",
        table_name="player_availability_observations",
    )
    op.drop_index(
        "ix_player_availability_fixture_captured",
        table_name="player_availability_observations",
    )
    op.drop_table("player_availability_observations")
