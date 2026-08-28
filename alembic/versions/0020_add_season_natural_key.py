"""add season natural key

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_seasons_competition_label",
        "seasons",
        ["competition_id", "label"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_seasons_competition_label",
        "seasons",
        type_="unique",
    )
