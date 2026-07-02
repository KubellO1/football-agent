"""SQLAlchemy ORM 模型（持久化表结构）。

ORM 模型与领域实体分离：领域实体是纯 dataclass，这里的 ORM 类只负责表映射，
二者通过 mappers.py 双向转换。所有 ORM 表集中在本模块，便于 Alembic 通过
Base.metadata 识别（见 alembic/env.py）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class FixtureORM(TimestampMixin, Base):
    """比赛表。Score 值对象拆为 score_home/score_away 两列；status 存枚举值。"""

    __tablename__ = "fixtures"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    competition_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    season_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    home_team_id: Mapped[UUID] = mapped_column(Uuid)
    away_team_id: Mapped[UUID] = mapped_column(Uuid)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))
    score_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
