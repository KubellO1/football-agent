"""create reference tables and add foreign keys

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-02

第三个迁移：创建参考数据表（competitions/teams/bookmakers/seasons），并把
fixtures/predictions/value_bets 之前只存 UUID 的关系补成真正的外键约束。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # --- 参考数据表 ---
    op.create_table(
        "competitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_competitions_name", "competitions", ["name"])

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("short_name", sa.String(length=40), nullable=True),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("elo", sa.Float(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_name", "teams", ["name"])

    op.create_table(
        "bookmakers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookmakers_name", "bookmakers", ["name"])

    op.create_table(
        "seasons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["competition_id"], ["competitions.id"], name="fk_seasons_competition"
        ),
    )
    op.create_index("ix_seasons_competition_id", "seasons", ["competition_id"])

    # --- 补齐核心聚合表的外键 ---
    op.create_foreign_key(
        "fk_fixtures_competition", "fixtures", "competitions", ["competition_id"], ["id"]
    )
    op.create_foreign_key("fk_fixtures_season", "fixtures", "seasons", ["season_id"], ["id"])
    op.create_foreign_key("fk_fixtures_home_team", "fixtures", "teams", ["home_team_id"], ["id"])
    op.create_foreign_key("fk_fixtures_away_team", "fixtures", "teams", ["away_team_id"], ["id"])

    op.create_foreign_key(
        "fk_predictions_fixture", "predictions", "fixtures", ["fixture_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_value_bets_fixture", "value_bets", "fixtures", ["fixture_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_value_bets_bookmaker", "value_bets", "bookmakers", ["bookmaker_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_value_bets_bookmaker", "value_bets", type_="foreignkey")
    op.drop_constraint("fk_value_bets_fixture", "value_bets", type_="foreignkey")
    op.drop_constraint("fk_predictions_fixture", "predictions", type_="foreignkey")
    op.drop_constraint("fk_fixtures_away_team", "fixtures", type_="foreignkey")
    op.drop_constraint("fk_fixtures_home_team", "fixtures", type_="foreignkey")
    op.drop_constraint("fk_fixtures_season", "fixtures", type_="foreignkey")
    op.drop_constraint("fk_fixtures_competition", "fixtures", type_="foreignkey")

    op.drop_index("ix_seasons_competition_id", table_name="seasons")
    op.drop_table("seasons")
    op.drop_index("ix_bookmakers_name", table_name="bookmakers")
    op.drop_table("bookmakers")
    op.drop_index("ix_teams_name", table_name="teams")
    op.drop_table("teams")
    op.drop_index("ix_competitions_name", table_name="competitions")
    op.drop_table("competitions")
