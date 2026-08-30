"""add odds snapshot provider provenance

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "odds_snapshots",
        sa.Column("provider_source", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "odds_snapshots",
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_odds_snapshots_provider_event",
        "odds_snapshots",
        ["provider_source", "provider_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_odds_snapshots_provider_event", table_name="odds_snapshots")
    op.drop_column("odds_snapshots", "provider_event_id")
    op.drop_column("odds_snapshots", "provider_source")
