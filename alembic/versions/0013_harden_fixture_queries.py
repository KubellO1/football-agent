"""harden fixture query indexes

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-28

为球队与赛事的状态化历史查询增加复合索引，确保 asyncpg 参数化查询及
PostgreSQL 通用预编译计划都能稳定使用索引。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_fixtures_home_status_kickoff",
        "fixtures",
        ["home_team_id", "status", "kickoff"],
        unique=False,
    )
    op.create_index(
        "ix_fixtures_away_status_kickoff",
        "fixtures",
        ["away_team_id", "status", "kickoff"],
        unique=False,
    )
    op.create_index(
        "ix_fixtures_competition_status_kickoff",
        "fixtures",
        ["competition_id", "status", "kickoff"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fixtures_competition_status_kickoff", table_name="fixtures")
    op.drop_index("ix_fixtures_away_status_kickoff", table_name="fixtures")
    op.drop_index("ix_fixtures_home_status_kickoff", table_name="fixtures")
