"""add external reference columns to ingested tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02

第五个迁移：为可采集的表（competitions/teams/fixtures）增加 external_source +
external_id 两列及唯一约束，作为外部数据源（如 API-Football）的采集幂等键。

唯一约束按 (external_source, external_id)。两列可空，且 Postgres 默认把 NULL 视为
互不相等——因此历史数据/测试数据（无外部 id）不会因唯一约束而冲突，只有带外部
id 的行才受唯一性约束。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("competitions", "teams", "fixtures")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("external_source", sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column("external_id", sa.String(length=64), nullable=True))
        op.create_unique_constraint(
            f"uq_{table}_external", table, ["external_source", "external_id"]
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"uq_{table}_external", table, type_="unique")
        op.drop_column(table, "external_id")
        op.drop_column(table, "external_source")
