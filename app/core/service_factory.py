"""从 (Container, Session) 组装应用服务的工厂。

把「按会话构造服务」的接线集中于此，供 FastAPI 依赖（请求作用域会话）与后台
worker（自管理会话）复用，确保二者用的是**同一套服务与接线**。

Provider / 数学模型 / gate / 评审器等单例从容器解析；仓储按传入会话构造。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.agents.interfaces import CommitteeReviewer
from app.models.value_objects.money import Money
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.repositories.sqlalchemy.decision_log_repository import SqlAlchemyDecisionLogRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository
from app.services.committee_review import CommitteeReviewService
from app.services.daily_top_picks import DailyRecommendationsReader, DailyTopPicksService
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.ingestion import IngestionService
from app.services.modeling import MatchModel
from app.services.odds_ingestion import OddsIngestionService
from app.services.recommendation_gate import RecommendationGate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.container import Container


def build_ingestion_service(container: Container, session: AsyncSession) -> IngestionService:
    return IngestionService(
        fixtures_provider=container.resolve(FixturesProvider),
        competitions=SqlAlchemyCompetitionRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        fixtures=SqlAlchemyFixtureRepository(session),
    )


def build_odds_ingestion_service(
    container: Container, session: AsyncSession
) -> OddsIngestionService:
    s = container.settings
    return OddsIngestionService(
        odds_provider=container.resolve(OddsProvider),
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        bookmakers=SqlAlchemyBookmakerRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        sport_keys=s.odds_sport_keys,
        regions=s.odds_regions,
        tolerance_minutes=s.odds_match_tolerance_minutes,
    )


def build_fixture_analysis_service(
    container: Container, session: AsyncSession
) -> FixtureAnalysisService:
    s = container.settings
    bankroll = Money(Decimal(str(s.analysis_default_bankroll)), s.analysis_currency)
    builder = MatchAnalysisInputBuilder(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        bankroll=bankroll,
        form_window=s.analysis_form_window,
    )
    return FixtureAnalysisService(
        builder=builder,
        model=container.resolve(MatchModel),
        gate=container.resolve(RecommendationGate),
    )


def build_committee_review_service(
    container: Container,
    session: AsyncSession,
    *,
    analysis: FixtureAnalysisService | None = None,
) -> CommitteeReviewService:
    return CommitteeReviewService(
        analysis=analysis or build_fixture_analysis_service(container, session),
        reviewer=container.resolve(CommitteeReviewer),
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        model_version=container.settings.anthropic_model,
    )


def build_daily_top_picks_service(
    container: Container, session: AsyncSession
) -> DailyTopPicksService:
    s = container.settings
    analysis = build_fixture_analysis_service(container, session)
    review = build_committee_review_service(container, session, analysis=analysis)
    return DailyTopPicksService(
        fixtures=SqlAlchemyFixtureRepository(session),
        analysis=analysis,
        review=review,
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        min_ev=s.recommendations_min_ev,
        min_kelly=s.recommendations_min_kelly,
        min_confidence=s.recommendations_min_confidence,
        max_picks=s.recommendations_max_picks,
    )


def build_daily_recommendations_reader(
    container: Container, session: AsyncSession
) -> DailyRecommendationsReader:
    return DailyRecommendationsReader(
        fixtures=SqlAlchemyFixtureRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        decision_logs=SqlAlchemyDecisionLogRepository(session),
    )
