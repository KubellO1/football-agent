"""add stable ordering to bankroll entries

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-27

为资金流水增加数据库生成的单调序号。余额查询不能仅依赖时间戳排序，因为并发写入
或相同时间戳会导致“最新流水”不确定。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bankroll_entries",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_bankroll_entries_sequence",
        "bankroll_entries",
        ["sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_bankroll_entries_sequence",
        "bankroll_entries",
        type_="unique",
    )
    op.drop_column("bankroll_entries", "sequence")
