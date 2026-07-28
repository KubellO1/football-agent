"""create team match statistics snapshots

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-28

创建球队单场原始统计快照表。所有指标允许为空，避免把未知数据伪装成零；
数据库约束与领域值对象保持一致，唯一键用于重复采集去重。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_match_statistics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_final",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("xg", sa.Float(), nullable=True),
        sa.Column("xg_against", sa.Float(), nullable=True),
        sa.Column("shots", sa.Integer(), nullable=True),
        sa.Column("shots_on_target", sa.Integer(), nullable=True),
        sa.Column("possession_percentage", sa.Float(), nullable=True),
        sa.Column("ppda", sa.Float(), nullable=True),
        sa.Column("big_chances", sa.Integer(), nullable=True),
        sa.Column("goalkeeper_saves", sa.Integer(), nullable=True),
        sa.Column("set_piece_shots", sa.Integer(), nullable=True),
        sa.Column("headed_shots", sa.Integer(), nullable=True),
        sa.Column("conversion_rate", sa.Float(), nullable=True),
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
        sa.CheckConstraint("xg IS NULL OR xg >= 0", name="ck_team_match_statistics_xg"),
        sa.CheckConstraint(
            "xg_against IS NULL OR xg_against >= 0",
            name="ck_team_match_statistics_xg_against",
        ),
        sa.CheckConstraint(
            "shots IS NULL OR shots >= 0",
            name="ck_team_match_statistics_shots",
        ),
        sa.CheckConstraint(
            "shots_on_target IS NULL OR shots_on_target >= 0",
            name="ck_team_match_statistics_shots_on_target",
        ),
        sa.CheckConstraint(
            "possession_percentage IS NULL OR "
            "(possession_percentage >= 0 AND possession_percentage <= 100)",
            name="ck_team_match_statistics_possession",
        ),
        sa.CheckConstraint(
            "ppda IS NULL OR ppda > 0",
            name="ck_team_match_statistics_ppda",
        ),
        sa.CheckConstraint(
            "big_chances IS NULL OR big_chances >= 0",
            name="ck_team_match_statistics_big_chances",
        ),
        sa.CheckConstraint(
            "goalkeeper_saves IS NULL OR goalkeeper_saves >= 0",
            name="ck_team_match_statistics_goalkeeper_saves",
        ),
        sa.CheckConstraint(
            "set_piece_shots IS NULL OR set_piece_shots >= 0",
            name="ck_team_match_statistics_set_piece_shots",
        ),
        sa.CheckConstraint(
            "headed_shots IS NULL OR headed_shots >= 0",
            name="ck_team_match_statistics_headed_shots",
        ),
        sa.CheckConstraint(
            "conversion_rate IS NULL OR (conversion_rate >= 0 AND conversion_rate <= 1)",
            name="ck_team_match_statistics_conversion_rate",
        ),
        sa.CheckConstraint(
            "shots IS NULL OR shots_on_target IS NULL OR shots_on_target <= shots",
            name="ck_team_match_statistics_shots_on_target_lte_shots",
        ),
        sa.CheckConstraint(
            "shots IS NULL OR set_piece_shots IS NULL OR set_piece_shots <= shots",
            name="ck_team_match_statistics_set_piece_lte_shots",
        ),
        sa.CheckConstraint(
            "shots IS NULL OR headed_shots IS NULL OR headed_shots <= shots",
            name="ck_team_match_statistics_headed_lte_shots",
        ),
        sa.CheckConstraint(
            "source_updated_at IS NULL OR source_updated_at <= captured_at",
            name="ck_team_match_statistics_source_updated_at",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["fixtures.id"],
            name="fk_team_match_statistics_fixture",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_team_match_statistics_team",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fixture_id",
            "team_id",
            "source",
            "captured_at",
            name="uq_team_match_statistics_natural",
        ),
    )
    op.create_index(
        "ix_team_match_statistics_fixture_captured",
        "team_match_statistics",
        ["fixture_id", "captured_at"],
    )
    op.create_index(
        "ix_team_match_statistics_team_captured",
        "team_match_statistics",
        ["team_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_team_match_statistics_team_captured",
        table_name="team_match_statistics",
    )
    op.drop_index(
        "ix_team_match_statistics_fixture_captured",
        table_name="team_match_statistics",
    )
    op.drop_table("team_match_statistics")
