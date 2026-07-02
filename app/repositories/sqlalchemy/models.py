"""SQLAlchemy ORM 模型（持久化表结构）。

ORM 模型与领域实体分离：领域实体是纯 dataclass，这里的 ORM 类只负责表映射，
二者通过 mappers.py 双向转换。所有 ORM 表集中在本模块，便于 Alembic 通过
Base.metadata 识别（见 alembic/env.py）。

聚合边界：各聚合根一张表、以 id 关联。MatchPrediction 不在库里嵌套存储其
recommendations —— ValueBet 是独立聚合根、独立表（带 fixture_id）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# 参考数据
# ---------------------------------------------------------------------------


class CompetitionORM(TimestampMixin, Base):
    """赛事表。"""

    __tablename__ = "competitions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str] = mapped_column(String(80))
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SeasonORM(TimestampMixin, Base):
    """赛季表。"""

    __tablename__ = "seasons"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    competition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("competitions.id"), index=True
    )
    label: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class TeamORM(TimestampMixin, Base):
    """球队表。elo 存 EloRating 的数值。"""

    __tablename__ = "teams"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    elo: Mapped[float | None] = mapped_column(Float, nullable=True)


class BookmakerORM(TimestampMixin, Base):
    """博彩公司表。"""

    __tablename__ = "bookmakers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)


# ---------------------------------------------------------------------------
# 核心聚合
# ---------------------------------------------------------------------------


class FixtureORM(TimestampMixin, Base):
    """比赛表。Score 值对象拆为 score_home/score_away 两列；status 存枚举值。"""

    __tablename__ = "fixtures"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    competition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("competitions.id"), index=True
    )
    season_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("seasons.id"), nullable=True
    )
    home_team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    away_team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))
    score_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_away: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PredictionORM(TimestampMixin, Base):
    """比赛预测表。胜平负概率拆为三列；xG 拆为两列；均可空。

    recommendations 不在此存储（由 value_bets 表管理）。
    """

    __tablename__ = "predictions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ValueBetORM(TimestampMixin, Base):
    """价值投注推荐表。

    Selection 拆为 market/code/line；Stake 拆为 amount/currency/fraction；
    赔率与金额用 Numeric 保精度；ValueEdge 为派生值不落库（由概率+赔率重建）。
    领域实体自带的 created_at 映射到 TimestampMixin 的 created_at 列。
    """

    __tablename__ = "value_bets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    selection_market: Mapped[str] = mapped_column(String(30))
    selection_code: Mapped[str] = mapped_column(String(30))
    selection_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_decimal: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    bookmaker_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("bookmakers.id"), nullable=True
    )
    model_probability: Mapped[float] = mapped_column(Float)
    stake_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    stake_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    stake_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
