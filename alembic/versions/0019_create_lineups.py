"""create lineups

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-01

创建追加式比赛阵容快照及有序球员明细，并保留来源证据与决策时间边界。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_name", sa.String(length=80), nullable=False),
        sa.Column("evidence_level", sa.String(length=1), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("formation", sa.String(length=20), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('predicted', 'confirmed')",
            name="ck_lineups_status",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_lineups_evidence_level",
        ),
        sa.CheckConstraint(
            "source_updated_at IS NULL OR source_updated_at <= captured_at",
            name="ck_lineups_source_updated_at",
        ),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], name="fk_lineups_fixture"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_lineups_team"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fixture_id",
            "team_id",
            "source_name",
            "captured_at",
            name="uq_lineups_natural",
        ),
    )
    op.create_index(
        "ix_lineups_fixture_captured",
        "lineups",
        ["fixture_id", "captured_at"],
    )
    op.create_index(
        "ix_lineups_team_captured",
        "lineups",
        ["team_id", "captured_at"],
    )

    op.create_table(
        "lineup_players",
        sa.Column("lineup_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('starting', 'substitute')",
            name="ck_lineup_players_role",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_lineup_players_ordinal"),
        sa.ForeignKeyConstraint(
            ["lineup_id"],
            ["lineups.id"],
            name="fk_lineup_players_lineup",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_lineup_players_player",
        ),
        sa.PrimaryKeyConstraint("lineup_id", "player_id"),
        sa.UniqueConstraint(
            "lineup_id",
            "role",
            "ordinal",
            name="uq_lineup_players_role_ordinal",
        ),
    )


def downgrade() -> None:
    op.drop_table("lineup_players")
    op.drop_index("ix_lineups_team_captured", table_name="lineups")
    op.drop_index("ix_lineups_fixture_captured", table_name="lineups")
    op.drop_table("lineups")
