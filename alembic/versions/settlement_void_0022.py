"""add terminal VOID settlement support

Revision ID: settlement_void_0022
Revises: 0021
Create Date: 2026-10-10

This is the active master migration following 0021.  The historical
observation-layer revision named ``0022`` remains isolated on TASK-060 and is
intentionally not part of this migration graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "settlement_void_0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESULT_CONSTRAINT = "ck_settlements_result_terminal"
_SHAPE_CONSTRAINT = "ck_settlements_void_shape"


def _assert_existing_rows_are_compatible() -> None:
    incompatible = op.get_bind().execute(sa.text("""
            SELECT count(*)
            FROM settlements
            WHERE result NOT IN ('W', 'L', 'P')
               OR score_home IS NULL
               OR score_away IS NULL
            """)).scalar_one()
    if incompatible:
        raise RuntimeError(
            "settlement VOID migration refused: existing settlement rows "
            "do not satisfy the canonical 0021 W/L/P score contract"
        )


def upgrade() -> None:
    _assert_existing_rows_are_compatible()

    op.add_column(
        "settlements",
        sa.Column("void_reason_code", sa.String(length=64), nullable=True),
    )
    op.alter_column(
        "settlements",
        "score_home",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "settlements",
        "score_away",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_check_constraint(
        _RESULT_CONSTRAINT,
        "settlements",
        "result IN ('W', 'L', 'P', 'V')",
    )
    op.create_check_constraint(
        _SHAPE_CONSTRAINT,
        "settlements",
        """
        (
            result = 'V'
            AND score_home IS NULL
            AND score_away IS NULL
            AND profit_loss = 0
            AND void_reason_code IS NOT NULL
            AND length(btrim(void_reason_code)) > 0
        )
        OR
        (
            result IN ('W', 'L', 'P')
            AND score_home IS NOT NULL
            AND score_away IS NOT NULL
            AND void_reason_code IS NULL
        )
        """,
    )


def downgrade() -> None:
    void_rows = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM settlements WHERE result = 'V'"))
        .scalar_one()
    )
    if void_rows:
        raise RuntimeError(
            "settlement VOID downgrade refused: terminal VOID rows exist; "
            "no row may be deleted, converted to PUSH, or assigned invented scores"
        )

    op.drop_constraint(_SHAPE_CONSTRAINT, "settlements", type_="check")
    op.drop_constraint(_RESULT_CONSTRAINT, "settlements", type_="check")
    op.alter_column(
        "settlements",
        "score_away",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "settlements",
        "score_home",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("settlements", "void_reason_code")
