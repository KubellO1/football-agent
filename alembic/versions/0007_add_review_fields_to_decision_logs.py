"""add AI-review reproducibility fields to decision_logs

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-02

第七个迁移（AI 评审层）：为 decision_logs 增加可复现性字段——
model_version / prompt_version（评审所用模型与提示词版本）以及 review（AI 评审的
完整结构化产出，JSON 原样存档）。三列均可空，向后兼容既有行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decision_logs", sa.Column("model_version", sa.String(length=50), nullable=True))
    op.add_column("decision_logs", sa.Column("prompt_version", sa.String(length=50), nullable=True))
    op.add_column("decision_logs", sa.Column("review", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("decision_logs", "review")
    op.drop_column("decision_logs", "prompt_version")
    op.drop_column("decision_logs", "model_version")
