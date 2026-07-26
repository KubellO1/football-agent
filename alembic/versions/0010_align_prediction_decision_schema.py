"""align prediction decision schema with ORM metadata

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26

将 predictions.final_decision 从 VARCHAR(10) 扩展到 VARCHAR(30)，
以容纳 NO_ODDS_MARKET_NOT_FOUND 等细分状态。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "predictions",
        "final_decision",
        existing_type=sa.String(length=10),
        type_=sa.String(length=30),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "predictions",
        "final_decision",
        existing_type=sa.String(length=30),
        type_=sa.String(length=10),
        existing_nullable=True,
    )
