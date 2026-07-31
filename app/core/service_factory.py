"""从 (Container, Session) 组装应用服务的工厂。

把「按会话构造服务」的接线集中于此，供 FastAPI 依赖（请求作用域会话）与后台
worker（自管理会话）复用，确保二者用的是**同一套服务与接线**。

Provider / 数学模型 / gate / 评审器等单例从容器解析；仓储按传入会话构造。
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from app.agents.interfaces import CommitteeReviewer
from app.config.whitelist import get_whitelist
from app.core.logging import get_logger
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.money import Money
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.interfaces.player_availability_provider import (
    PlayerAvailabilityProvider,
)
from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.repositories.sqlalchemy.decision_log_repository import SqlAlchemyDecisionLogRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.player_availability_repository import (
    SqlAlchemyPlayerAvailabilityObservationRepository,
)
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.settlement_repository import (
    SqlAlchemyBankrollRepository,
    SqlAlchemyPerformanceSnapshotRepository,
    SqlAlchemySettlementRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository
from app.services.committee_review import CommitteeReviewService
from app.services.daily_top_picks import DailyRecommendationsReader, DailyTopPicksService
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.fixture_squad_ingestion import FixtureSquadIngestionService
from app.services.ingestion import IngestionService
from app.services.modeling import MatchModel
from app.services.odds_ingestion import OddsIngestionService
from app.services.performance_tracker import PerformanceTracker
from app.services.player_availability_ingestion import (
    PlayerAvailabilityIngestionService,
)
from app.services.player_squad_ingestion import PlayerSquadIngestionService
from app.services.recommendation_gate import RecommendationGate
from app.services.settlement import SettlementService
from app.services.verified_market_movement import VerifiedMarketMovementService
from app.services.verified_market_quote import (
    VerifiedMarketQuotePolicy,
    VerifiedMarketQuoteService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config.settings import Settings
    from app.core.container import Container
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository


class SportKeyDiagnostics(TypedDict):
    """今日动态赔率联赛键的诊断结果。"""

    needed: list[str]
    configured: int
    saved: int
    saved_pct: float
    empty_keys: list[str]


async def derive_today_sport_keys(
    session: AsyncSession,
    on_date: date,
) -> SportKeyDiagnostics:
    """Derive the minimal set of Odds API sport_keys needed for today.

    Queries today's fixtures, extracts unique competition names,
    maps them to whitelist sport_keys, and returns diagnostics.

    Returns:
        dict with keys:
        - needed: list[str] — sport_keys to request today
        - configured: int — total whitelist sport_keys
        - saved: int — keys NOT needed today
        - saved_pct: float — percentage saved
        - empty_keys: list[str] — whitelist keys with zero fixtures today
    """
    whitelist = get_whitelist()
    all_keys = sorted(whitelist.sport_keys)
    configured = len(all_keys)

    day_start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=UTC)
    day_end = datetime(on_date.year, on_date.month, on_date.day, 23, 59, 59, tzinfo=UTC)

    fixture_repo = SqlAlchemyFixtureRepository(session)
    fixtures = await fixture_repo.list_by_kickoff_window(day_start, day_end)

    needed: set[str] = set()
    for fixture in fixtures:
        # Resolve competition → whitelist entry → sport_key
        comp_repo = SqlAlchemyCompetitionRepository(session)
        comp = await comp_repo.get(fixture.competition_id)
        comp_name = comp.name if comp else ""
        # Resolve league_id + country for exact-match whitelist
        league_id: int | None = None
        country = comp.country if comp else None
        if comp and comp.external_id:
            with suppress(ValueError, TypeError):
                league_id = int(comp.external_id)

        if whitelist.is_allowed(comp_name, league_id=league_id, country=country):
            sk = whitelist.get_sport_key_for(comp_name, league_id=league_id, country=country)
            if sk:
                needed.add(sk)

    needed_sorted = sorted(needed)
    empty_keys = sorted(set(all_keys) - needed)
    saved = len(empty_keys)
    saved_pct = (saved / configured * 100) if configured else 0.0

    logger_ = get_logger(__name__)
    logger_.info(
        "Dynamic sport_key derivation: configured=%d needed=%d saved=%d (%.1f%%)",
        configured,
        len(needed_sorted),
        saved,
        saved_pct,
    )
    if empty_keys:
        logger_.info("Empty whitelist sport_keys (0 fixtures today): %s", empty_keys)

    return {
        "needed": needed_sorted,
        "configured": configured,
        "saved": saved,
        "saved_pct": saved_pct,
        "empty_keys": empty_keys,
    }


def build_ingestion_service(container: Container, session: AsyncSession) -> IngestionService:
    return IngestionService(
        fixtures_provider=container.resolve(FixturesProvider),
        competitions=SqlAlchemyCompetitionRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        fixtures=SqlAlchemyFixtureRepository(session),
    )


def build_player_availability_ingestion_service(
    container: Container,
    session: AsyncSession,
) -> PlayerAvailabilityIngestionService:
    """组装 API-Football 球员可用性采集服务。"""
    return PlayerAvailabilityIngestionService(
        provider=container.resolve(PlayerAvailabilityProvider),
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        players=SqlAlchemyPlayerRepository(session),
        observations=SqlAlchemyPlayerAvailabilityObservationRepository(session),
        source="api-football",
        evidence_level=EvidenceLevel.B,
    )


def build_player_squad_ingestion_service(
    container: Container,
    session: AsyncSession,
) -> PlayerSquadIngestionService:
    """组装 API-Football 球队阵容主数据采集服务。"""
    return PlayerSquadIngestionService(
        provider=container.resolve(PlayerSquadProvider),
        teams=SqlAlchemyTeamRepository(session),
        players=SqlAlchemyPlayerRepository(session),
        source="api-football",
    )


def build_fixture_squad_ingestion_service(
    container: Container,
    session: AsyncSession,
) -> FixtureSquadIngestionService:
    """组装按比赛同步主客两队阵容的应用服务。"""
    return FixtureSquadIngestionService(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        squads=build_player_squad_ingestion_service(container, session),
        source="api-football",
    )


def build_odds_ingestion_service(
    container: Container,
    session: AsyncSession,
    *,
    sport_keys: list[str] | None = None,
) -> OddsIngestionService:
    s = container.settings
    # Derive sport_keys from production whitelist when available,
    # falling back to the configured ODDS_SPORT_KEYS env var.
    # A caller may override by passing sport_keys explicitly
    # (useful for dynamic derivation after fixture ingestion).
    if sport_keys is not None:
        used_keys = sport_keys
        from app.core.logging import get_logger

        get_logger(__name__).info(
            "Odds ingestion using caller-supplied sport_keys (n=%d)", len(used_keys)
        )
    else:
        try:
            whitelist = get_whitelist()
            used_keys = sorted(whitelist.sport_keys)
            if used_keys:
                from app.core.logging import get_logger

                get_logger(__name__).info(
                    "Odds ingestion using whitelist sport_keys (n=%d)", len(used_keys)
                )
            else:
                used_keys = s.odds_sport_keys
        except Exception:
            used_keys = s.odds_sport_keys
    return OddsIngestionService(
        odds_provider=container.resolve(OddsProvider),
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        bookmakers=SqlAlchemyBookmakerRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        sport_keys=used_keys,
        regions=s.odds_regions,
        tolerance_minutes=s.odds_match_tolerance_minutes,
    )


def build_market_quote_policy(settings: Settings) -> VerifiedMarketQuotePolicy:
    """从类型安全配置构造所有分析入口共享的赔率验证策略。"""
    return VerifiedMarketQuotePolicy(
        maximum_age=timedelta(minutes=settings.analysis_odds_max_age_minutes),
        minimum_bookmakers=settings.analysis_odds_min_bookmakers,
        maximum_relative_deviation=settings.analysis_odds_max_relative_deviation,
    )


def build_market_movement_service(
    settings: Settings,
    odds_snapshots: OddsSnapshotRepository,
) -> VerifiedMarketMovementService:
    """用共享赔率准入策略组装可验证的市场变化服务。"""
    return VerifiedMarketMovementService(
        market_quotes=VerifiedMarketQuoteService(
            repository=odds_snapshots,
            policy=build_market_quote_policy(settings),
        )
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
        market_quote_policy=build_market_quote_policy(s),
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
    settings = container.settings
    return CommitteeReviewService(
        analysis=analysis or build_fixture_analysis_service(container, session),
        reviewer=container.resolve(CommitteeReviewer),
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        model_version=settings.openai_model,
        market_movements=build_market_movement_service(
            settings,
            SqlAlchemyOddsSnapshotRepository(session),
        ),
        movement_lookback=timedelta(hours=settings.analysis_market_movement_lookback_hours),
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
        teams=SqlAlchemyTeamRepository(session),
        competitions=SqlAlchemyCompetitionRepository(session),
        session=session,
        min_ev=s.recommendations_min_ev,
        min_kelly=s.recommendations_min_kelly,
        min_confidence=s.recommendations_min_confidence,
        max_picks=s.recommendations_max_picks,
        model_version=s.openai_model,
    )


def build_daily_recommendations_reader(
    container: Container, session: AsyncSession
) -> DailyRecommendationsReader:
    return DailyRecommendationsReader(
        fixtures=SqlAlchemyFixtureRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        decision_logs=SqlAlchemyDecisionLogRepository(session),
    )


def build_settlement_service(container: Container, session: AsyncSession) -> SettlementService:
    s = container.settings
    return SettlementService(
        fixtures=SqlAlchemyFixtureRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        settlements=SqlAlchemySettlementRepository(session),
        bankroll=SqlAlchemyBankrollRepository(session),
        initial_bankroll=Money(Decimal(str(s.analysis_default_bankroll)), s.analysis_currency),
    )


def build_performance_tracker(container: Container, session: AsyncSession) -> PerformanceTracker:
    return PerformanceTracker(
        settlements=SqlAlchemySettlementRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        snapshots=SqlAlchemyPerformanceSnapshotRepository(session),
    )
