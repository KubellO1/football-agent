"""请求作用域的 FastAPI 依赖。

从 DI 容器取基础设施（DB session、Redis）与单例服务组件，并把仓储、分析编排
作为请求作用域依赖暴露。仓储依赖返回抽象接口类型、内部构造 SQLAlchemy 实现
——依赖倒置在此 wiring 边界完成：endpoint / service 只依赖接口，不见具体实现。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Annotated, cast

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.interfaces import CommitteeReviewer, ReasoningEngine
from app.core.container import container
from app.models.value_objects.money import Money
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
from app.repositories.interfaces.prediction_repository import PredictionRepository
from app.repositories.interfaces.reference import (
    BookmakerRepository,
    CompetitionRepository,
    SeasonRepository,
    TeamRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.repositories.sqlalchemy.decision_log_repository import SqlAlchemyDecisionLogRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.prediction_repository import SqlAlchemyPredictionRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemySeasonRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository
from app.services.analysis_pipeline import MatchAnalysisPipeline
from app.services.committee_review import CommitteeReviewService
from app.services.daily_selection import DailySelectionService
from app.services.fixture_analysis import (
    FixtureAnalysisService,
    MatchAnalysisInputBuilder,
)
from app.services.ingestion import IngestionService
from app.services.modeling import MatchModel
from app.services.odds_ingestion import OddsIngestionService
from app.services.recommendation_gate import RecommendationGate


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """从容器的 Database 产出一个事务性 session。"""
    async with container.database.session() as session:
        yield session


async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """从容器的连接池产出一个 Redis 客户端。"""
    client = container.redis.client()
    try:
        yield client
    finally:
        await client.aclose()


# 用于 endpoint 签名的类型别名。
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]


# --- 仓储依赖（返回接口类型，注入 SQLAlchemy 实现）---


def get_fixture_repository(session: SessionDep) -> FixtureRepository:
    return SqlAlchemyFixtureRepository(session)


def get_prediction_repository(session: SessionDep) -> PredictionRepository:
    return SqlAlchemyPredictionRepository(session)


def get_value_bet_repository(session: SessionDep) -> ValueBetRepository:
    return SqlAlchemyValueBetRepository(session)


def get_decision_log_repository(session: SessionDep) -> DecisionLogRepository:
    return SqlAlchemyDecisionLogRepository(session)


def get_team_repository(session: SessionDep) -> TeamRepository:
    return SqlAlchemyTeamRepository(session)


def get_competition_repository(session: SessionDep) -> CompetitionRepository:
    return SqlAlchemyCompetitionRepository(session)


def get_season_repository(session: SessionDep) -> SeasonRepository:
    return SqlAlchemySeasonRepository(session)


def get_bookmaker_repository(session: SessionDep) -> BookmakerRepository:
    return SqlAlchemyBookmakerRepository(session)


def get_odds_snapshot_repository(session: SessionDep) -> OddsSnapshotRepository:
    return SqlAlchemyOddsSnapshotRepository(session)


FixtureRepositoryDep = Annotated[FixtureRepository, Depends(get_fixture_repository)]
PredictionRepositoryDep = Annotated[PredictionRepository, Depends(get_prediction_repository)]
ValueBetRepositoryDep = Annotated[ValueBetRepository, Depends(get_value_bet_repository)]
DecisionLogRepositoryDep = Annotated[DecisionLogRepository, Depends(get_decision_log_repository)]
TeamRepositoryDep = Annotated[TeamRepository, Depends(get_team_repository)]
CompetitionRepositoryDep = Annotated[CompetitionRepository, Depends(get_competition_repository)]
SeasonRepositoryDep = Annotated[SeasonRepository, Depends(get_season_repository)]
BookmakerRepositoryDep = Annotated[BookmakerRepository, Depends(get_bookmaker_repository)]
OddsSnapshotRepositoryDep = Annotated[OddsSnapshotRepository, Depends(get_odds_snapshot_repository)]


# --- 外部数据源 Provider 依赖（容器持有的单例，返回接口类型）---


def get_fixtures_provider() -> FixturesProvider:
    return cast("FixturesProvider", container.resolve(FixturesProvider))


def get_odds_provider() -> OddsProvider:
    return cast("OddsProvider", container.resolve(OddsProvider))


FixturesProviderDep = Annotated[FixturesProvider, Depends(get_fixtures_provider)]
OddsProviderDep = Annotated[OddsProvider, Depends(get_odds_provider)]


def get_ingestion_service(
    competitions: CompetitionRepositoryDep,
    teams: TeamRepositoryDep,
    fixtures: FixtureRepositoryDep,
) -> IngestionService:
    """组装数据采集服务：容器持有的 FixturesProvider + 请求作用域仓储。"""
    return IngestionService(
        fixtures_provider=container.resolve(FixturesProvider),
        competitions=competitions,
        teams=teams,
        fixtures=fixtures,
    )


IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]


def get_odds_ingestion_service(
    teams: TeamRepositoryDep,
    fixtures: FixtureRepositoryDep,
    bookmakers: BookmakerRepositoryDep,
    odds_snapshots: OddsSnapshotRepositoryDep,
) -> OddsIngestionService:
    """组装赔率采集服务：容器持有的 OddsProvider + 请求作用域仓储 + settings。"""
    settings = container.settings
    return OddsIngestionService(
        odds_provider=container.resolve(OddsProvider),
        fixtures=fixtures,
        teams=teams,
        bookmakers=bookmakers,
        odds_snapshots=odds_snapshots,
        sport_keys=settings.odds_sport_keys,
        regions=settings.odds_regions,
        tolerance_minutes=settings.odds_match_tolerance_minutes,
    )


OddsIngestionServiceDep = Annotated[OddsIngestionService, Depends(get_odds_ingestion_service)]


def get_fixture_analysis_service(
    fixtures: FixtureRepositoryDep,
    teams: TeamRepositoryDep,
    odds_snapshots: OddsSnapshotRepositoryDep,
) -> FixtureAnalysisService:
    """组装 DB 驱动的单场分析服务：请求作用域仓储 + 容器中的数学模型/gate + settings。"""
    settings = container.settings
    bankroll = Money(Decimal(str(settings.analysis_default_bankroll)), settings.analysis_currency)
    builder = MatchAnalysisInputBuilder(
        fixtures=fixtures,
        teams=teams,
        odds_snapshots=odds_snapshots,
        bankroll=bankroll,
        form_window=settings.analysis_form_window,
    )
    return FixtureAnalysisService(
        builder=builder,
        model=container.resolve(MatchModel),
        gate=container.resolve(RecommendationGate),
    )


FixtureAnalysisServiceDep = Annotated[FixtureAnalysisService, Depends(get_fixture_analysis_service)]


def get_committee_review_service(
    analysis: FixtureAnalysisServiceDep,
    decision_logs: DecisionLogRepositoryDep,
    value_bets: ValueBetRepositoryDep,
) -> CommitteeReviewService:
    """组装 AI 评审服务：确定性分析 + 容器中的 Claude 评审器 + 请求作用域仓储。"""
    return CommitteeReviewService(
        analysis=analysis,
        reviewer=container.resolve(CommitteeReviewer),
        decision_logs=decision_logs,
        value_bets=value_bets,
        model_version=container.settings.anthropic_model,
    )


CommitteeReviewServiceDep = Annotated[CommitteeReviewService, Depends(get_committee_review_service)]


# --- 分析编排依赖（单例组件来自容器 + 请求作用域仓储）---


def get_analysis_pipeline(
    value_bet_repository: ValueBetRepositoryDep,
) -> MatchAnalysisPipeline:
    """组装单场比赛分析编排。"""
    return MatchAnalysisPipeline(
        model=container.resolve(MatchModel),
        gate=container.resolve(RecommendationGate),
        selector=container.resolve(DailySelectionService),
        reasoning=container.resolve(ReasoningEngine),
        value_bet_repository=value_bet_repository,
    )


AnalysisPipelineDep = Annotated[MatchAnalysisPipeline, Depends(get_analysis_pipeline)]

__all__ = [
    "AnalysisPipelineDep",
    "BookmakerRepositoryDep",
    "CommitteeReviewServiceDep",
    "CompetitionRepositoryDep",
    "DecisionLogRepositoryDep",
    "FixtureAnalysisServiceDep",
    "FixtureRepositoryDep",
    "FixturesProviderDep",
    "IngestionServiceDep",
    "OddsIngestionServiceDep",
    "OddsProviderDep",
    "OddsSnapshotRepositoryDep",
    "PredictionRepositoryDep",
    "RedisDep",
    "SeasonRepositoryDep",
    "SessionDep",
    "TeamRepositoryDep",
    "ValueBetRepositoryDep",
    "get_analysis_pipeline",
    "get_bookmaker_repository",
    "get_committee_review_service",
    "get_competition_repository",
    "get_db_session",
    "get_decision_log_repository",
    "get_fixture_analysis_service",
    "get_fixture_repository",
    "get_fixtures_provider",
    "get_ingestion_service",
    "get_odds_ingestion_service",
    "get_odds_provider",
    "get_odds_snapshot_repository",
    "get_prediction_repository",
    "get_redis",
    "get_season_repository",
    "get_team_repository",
    "get_value_bet_repository",
]
