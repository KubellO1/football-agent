"""create fixtures table

Revision ID: 0001
Revises:
Create Date: 2026-07-02

首个迁移：创建 fixtures 表（Fixture 聚合的持久化样板）。
本机无法运行 alembic autogenerate，故手写；后续聚合可用 autogenerate 生成。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fixtures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("home_team_id", sa.Uuid(), nullable=False),
        sa.Column("away_team_id", sa.Uuid(), nullable=False),
        sa.Column("kickoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("score_home", sa.Integer(), nullable=True),
        sa.Column("score_away", sa.Integer(), nullable=True),
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
    op.create_index("ix_fixtures_competition_id", "fixtures", ["competition_id"])
    op.create_index("ix_fixtures_kickoff", "fixtures", ["kickoff"])


def downgrade() -> None:
    op.drop_index("ix_fixtures_kickoff", table_name="fixtures")
    op.drop_index("ix_fixtures_competition_id", table_name="fixtures")
    op.drop_table("fixtures")
