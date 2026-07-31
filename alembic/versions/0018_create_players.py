"""create players and enforce availability player reference

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-31

创建 Player 主数据表，并为球员可用性观察补充 Player 外键。
若数据库已存在孤立观察记录，迁移明确失败，禁止编造占位球员。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.String(length=3), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("external_source", sa.String(length=40), nullable=True),
        sa.Column("external_id", sa.String(length=120), nullable=True),
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
            "position IN ('GK', 'DEF', 'MID', 'FWD')",
            name="ck_players_position",
        ),
        sa.CheckConstraint(
            "(external_source IS NULL) = (external_id IS NULL)",
            name="ck_players_external_identity_pair",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_players_team",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_source",
            "external_id",
            name="uq_players_external",
        ),
    )
    op.create_index(
        "ix_players_team_name",
        "players",
        ["team_id", "name", "id"],
    )

    op.execute(sa.text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM player_availability_observations AS observation
                    LEFT JOIN players AS player ON player.id = observation.player_id
                    WHERE player.id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'cannot add player availability FK: orphan player_id values exist';
                END IF;
            END
            $$;
            """))
    op.create_foreign_key(
        "fk_player_availability_player",
        "player_availability_observations",
        "players",
        ["player_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_player_availability_player",
        "player_availability_observations",
        type_="foreignkey",
    )
    op.drop_index("ix_players_team_name", table_name="players")
    op.drop_table("players")
