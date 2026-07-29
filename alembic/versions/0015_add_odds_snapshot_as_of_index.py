"""add odds snapshot as-of index

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-29

为赔率快照的赛事实时点查询增加复合索引。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_odds_snapshots_fixture_captured_at"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "odds_snapshots",
        ["fixture_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="odds_snapshots")
