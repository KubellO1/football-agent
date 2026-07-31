"""SQLAlchemy ORM 模型（持久化表结构）。

ORM 模型与领域实体分离：领域实体是纯 dataclass，这里的 ORM 类只负责表映射，
二者通过 mappers.py 双向转换。所有 ORM 表集中在本模块，便于 Alembic 通过
Base.metadata 识别（见 alembic/env.py）。

聚合边界：各聚合根一张表、以 id 关联。MatchPrediction 不在库里嵌套存储其
recommendations —— ValueBet 是独立聚合根、独立表（带 fixture_id）。
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - SQLAlchemy 会在运行时解析 Mapped 注解
from decimal import Decimal  # noqa: TC003 - SQLAlchemy 会在运行时解析 Mapped 注解
from typing import Any
from uuid import UUID  # noqa: TC003 - SQLAlchemy 会在运行时解析 Mapped 注解

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin

PREDICTION_RECORD_AGGREGATE = "aggregate"
PREDICTION_RECORD_DECISION = "decision"

# ---------------------------------------------------------------------------
# 参考数据
# ---------------------------------------------------------------------------


class CompetitionORM(TimestampMixin, Base):
    """赛事表。external_source+external_id 为采集幂等键（唯一）。"""

    __tablename__ = "competitions"
    __table_args__ = (
        UniqueConstraint("external_source", "external_id", name="uq_competitions_external"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str] = mapped_column(String(80))
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SeasonORM(TimestampMixin, Base):
    """赛季表。"""

    __tablename__ = "seasons"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    competition_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("competitions.id"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class TeamORM(TimestampMixin, Base):
    """球队表。elo 存 EloRating 的数值。external_source+external_id 为采集幂等键（唯一）。"""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("external_source", "external_id", name="uq_teams_external"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    elo: Mapped[float | None] = mapped_column(Float, nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlayerORM(TimestampMixin, Base):
    """球员主数据表；外部来源与外部 ID 共同形成采集幂等身份。"""

    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint(
            "external_source",
            "external_id",
            name="uq_players_external",
        ),
        CheckConstraint(
            "position IN ('GK', 'DEF', 'MID', 'FWD')",
            name="ck_players_position",
        ),
        CheckConstraint(
            "(external_source IS NULL) = (external_id IS NULL)",
            name="ck_players_external_identity_pair",
        ),
        Index("ix_players_team_name", "team_id", "name", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str] = mapped_column(String(3))
    team_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("teams.id"),
        nullable=True,
    )
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class BookmakerORM(TimestampMixin, Base):
    """博彩公司表。external_source+external_id 为采集幂等键（唯一）。"""

    __tablename__ = "bookmakers"
    __table_args__ = (
        UniqueConstraint("external_source", "external_id", name="uq_bookmakers_external"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# 核心聚合
# ---------------------------------------------------------------------------


class FixtureORM(TimestampMixin, Base):
    """比赛表。Score 值对象拆为 score_home/score_away 两列；status 存枚举值。

    external_source+external_id 为采集幂等键（唯一）：重复采集同一场比赛只会更新，
    不会新增行。
    """

    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("external_source", "external_id", name="uq_fixtures_external"),
        Index(
            "ix_fixtures_home_status_kickoff",
            "home_team_id",
            "status",
            "kickoff",
        ),
        Index(
            "ix_fixtures_away_status_kickoff",
            "away_team_id",
            "status",
            "kickoff",
        ),
        Index(
            "ix_fixtures_competition_status_kickoff",
            "competition_id",
            "status",
            "kickoff",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    competition_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("competitions.id"), index=True)
    season_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("seasons.id"), nullable=True)
    home_team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    away_team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))
    score_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PredictionORM(TimestampMixin, Base):
    """比赛预测表，通过 ``record_kind`` 显式区分两种持久化记录。

    ``aggregate`` 保存 per-fixture 数学模型输出（1X2 概率、xG、模型版本），只由
    PredictionRepository 映射为 MatchPrediction；``decision`` 保存逐 selection
    决策引擎输出（比赛上下文、市场决策、EV、最终判定和数据质量），供日志与
    Dashboard 使用。两种记录禁止交叉映射。

    recommendations 不在此存储（由 value_bets 表管理）。
    """

    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint(
            "record_kind IN ('aggregate', 'decision')",
            name="ck_predictions_record_kind",
        ),
        Index("ix_predictions_fixture_decision", "fixture_id", "final_decision"),
        Index(
            "ix_predictions_fixture_kind_generated",
            "fixture_id",
            "record_kind",
            "generated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    record_kind: Mapped[str] = mapped_column(
        String(20),
        default=PREDICTION_RECORD_AGGREGATE,
        server_default=PREDICTION_RECORD_AGGREGATE,
    )
    # -- 旧字段（per-fixture 数学输出，向后兼容） --
    prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # -- 新字段（per-selection 决策引擎输出） --
    kickoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    competition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    home_team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prediction_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prediction_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    market: Mapped[str | None] = mapped_column(String(30), nullable=True)
    selection: Mapped[str | None] = mapped_column(String(30), nullable=True)
    odds: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    market_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    kelly_stake: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    why_not_bet: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_killer: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_sources: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data_quality: Mapped[float | None] = mapped_column(Float, nullable=True)


class ValueBetORM(TimestampMixin, Base):
    """价值投注推荐表。

    Selection 拆为 market/code/line；Stake 拆为 amount/currency/fraction；
    赔率与金额用 Numeric 保精度；ValueEdge 为派生值不落库（由概率+赔率重建）。
    领域实体自带的 created_at 映射到 TimestampMixin 的 created_at 列。
    """

    __tablename__ = "value_bets"
    __table_args__ = (Index("ix_value_bets_created_at", "created_at"),)

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


class OddsSnapshotORM(TimestampMixin, Base):
    """赔率快照表：某盘口某博彩公司在某时刻的一条价格观测。

    时间序列表：captured_at 取博彩公司该盘口的最后更新时间。幂等键为
    (fixture_id, bookmaker_id, market, code, line, captured_at) 唯一约束——
    价格未变（captured_at 相同）重复采集不会插入新行。selection_line 可空且
    1x2 恒为 NULL，故唯一约束用 NULLS NOT DISTINCT（Postgres 15+）以保证去重。
    """

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        Index(
            "ix_odds_snapshots_fixture_captured_at",
            "fixture_id",
            "captured_at",
        ),
        UniqueConstraint(
            "fixture_id",
            "bookmaker_id",
            "selection_market",
            "selection_code",
            "selection_line",
            "captured_at",
            name="uq_odds_snapshots_natural",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    bookmaker_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("bookmakers.id"), index=True)
    selection_market: Mapped[str] = mapped_column(String(30))
    selection_code: Mapped[str] = mapped_column(String(30))
    selection_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_decimal: Mapped[Decimal] = mapped_column(Numeric(9, 3))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TeamMatchStatisticsORM(TimestampMixin, Base):
    """球队单场原始统计快照；指标未知时保持 NULL，不用零值填充。"""

    __tablename__ = "team_match_statistics"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "team_id",
            "source",
            "captured_at",
            name="uq_team_match_statistics_natural",
        ),
        CheckConstraint("xg IS NULL OR xg >= 0", name="ck_team_match_statistics_xg"),
        CheckConstraint(
            "xg_against IS NULL OR xg_against >= 0",
            name="ck_team_match_statistics_xg_against",
        ),
        CheckConstraint("shots IS NULL OR shots >= 0", name="ck_team_match_statistics_shots"),
        CheckConstraint(
            "shots_on_target IS NULL OR shots_on_target >= 0",
            name="ck_team_match_statistics_shots_on_target",
        ),
        CheckConstraint(
            "possession_percentage IS NULL OR "
            "(possession_percentage >= 0 AND possession_percentage <= 100)",
            name="ck_team_match_statistics_possession",
        ),
        CheckConstraint("ppda IS NULL OR ppda > 0", name="ck_team_match_statistics_ppda"),
        CheckConstraint(
            "big_chances IS NULL OR big_chances >= 0",
            name="ck_team_match_statistics_big_chances",
        ),
        CheckConstraint(
            "goalkeeper_saves IS NULL OR goalkeeper_saves >= 0",
            name="ck_team_match_statistics_goalkeeper_saves",
        ),
        CheckConstraint(
            "set_piece_shots IS NULL OR set_piece_shots >= 0",
            name="ck_team_match_statistics_set_piece_shots",
        ),
        CheckConstraint(
            "headed_shots IS NULL OR headed_shots >= 0",
            name="ck_team_match_statistics_headed_shots",
        ),
        CheckConstraint(
            "conversion_rate IS NULL OR (conversion_rate >= 0 AND conversion_rate <= 1)",
            name="ck_team_match_statistics_conversion_rate",
        ),
        CheckConstraint(
            "shots IS NULL OR shots_on_target IS NULL OR shots_on_target <= shots",
            name="ck_team_match_statistics_shots_on_target_lte_shots",
        ),
        CheckConstraint(
            "shots IS NULL OR set_piece_shots IS NULL OR set_piece_shots <= shots",
            name="ck_team_match_statistics_set_piece_lte_shots",
        ),
        CheckConstraint(
            "shots IS NULL OR headed_shots IS NULL OR headed_shots <= shots",
            name="ck_team_match_statistics_headed_lte_shots",
        ),
        CheckConstraint(
            "source_updated_at IS NULL OR source_updated_at <= captured_at",
            name="ck_team_match_statistics_source_updated_at",
        ),
        Index(
            "ix_team_match_statistics_fixture_captured",
            "fixture_id",
            "captured_at",
        ),
        Index(
            "ix_team_match_statistics_team_captured",
            "team_id",
            "captured_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"))
    team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    source: Mapped[str] = mapped_column(String(40))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_against: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    possession_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    ppda: Mapped[float | None] = mapped_column(Float, nullable=True)
    big_chances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goalkeeper_saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    set_piece_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headed_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class PlayerAvailabilityObservationORM(TimestampMixin, Base):
    """球员可用性原始观察；未知状态保持显式值，不伪装为可出场。"""

    __tablename__ = "player_availability_observations"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "player_id",
            "source_name",
            "captured_at",
            name="uq_player_availability_observations_natural",
        ),
        CheckConstraint(
            "status IN ('unknown', 'available', 'doubtful', 'out', 'suspended', 'returned')",
            name="ck_player_availability_status",
        ),
        CheckConstraint(
            "evidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_player_availability_evidence_level",
        ),
        CheckConstraint(
            "source_updated_at IS NULL OR source_updated_at <= captured_at",
            name="ck_player_availability_source_updated_at",
        ),
        Index(
            "ix_player_availability_fixture_captured",
            "fixture_id",
            "captured_at",
        ),
        Index(
            "ix_player_availability_player_captured",
            "player_id",
            "captured_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"))
    team_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("teams.id"))
    player_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(20))
    source_name: Mapped[str] = mapped_column(String(80))
    evidence_level: Mapped[str] = mapped_column(String(1))
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expected_return: Mapped[date | None] = mapped_column(Date, nullable=True)


class DecisionLogORM(TimestampMixin, Base):
    """决策日志表（宪法第 16 节）。列表字段用 JSON 存储，避免多张关联表。"""

    __tablename__ = "decision_logs"
    __table_args__ = (Index("ix_decision_logs_created_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    value_bet_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("value_bets.id"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text)
    supporting_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    rejected_alternatives: Mapped[list[str]] = mapped_column(JSON, default=list)
    change_conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 可复现性：AI 评审的模型/提示词版本与完整结构化产出（存档，不改数值）。
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# 结算与追踪
# ---------------------------------------------------------------------------


class SettlementORM(TimestampMixin, Base):
    """结算记录：每一条 value_bet 对应的比赛结果。唯一约束防止重复结算。"""

    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("value_bet_id", name="uq_settlements_value_bet"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    value_bet_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("value_bets.id"), index=True)
    fixture_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("fixtures.id"), index=True)
    result: Mapped[str] = mapped_column(String(1))  # W / L / P
    score_home: Mapped[int] = mapped_column(Integer)
    score_away: Mapped[int] = mapped_column(Integer)
    profit_loss: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    closing_odds: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    clv: Mapped[float | None] = mapped_column(Float, nullable=True)
    bankroll_before: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    bankroll_after: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    settlement_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BankrollEntryORM(TimestampMixin, Base):
    """银行余额流水表。每条记录代表一次余额变动（初始化、结算、调整）。"""

    __tablename__ = "bankroll_entries"
    __table_args__ = (UniqueConstraint("sequence", name="uq_bankroll_entries_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(200))


class PerformanceSnapshotORM(TimestampMixin, Base):
    """定期性能统计快照表。"""

    __tablename__ = "performance_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_bets: Mapped[int] = mapped_column(Integer)
    win_count: Mapped[int] = mapped_column(Integer)
    push_count: Mapped[int] = mapped_column(Integer)
    loss_count: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_pl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_clv: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
