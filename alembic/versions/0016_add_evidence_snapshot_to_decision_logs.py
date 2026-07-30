"""add evidence snapshot to decision logs

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-30

保存委员会评审实际使用的结构化证据包，供审计与复盘。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_logs",
        sa.Column("evidence_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decision_logs", "evidence_snapshot")
