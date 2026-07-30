"""每日 Top Picks 批处理与读取的集成测试（需真实 Postgres）。

用假评审器（不触达 LLM）+ 真实数学模型 + 真实仓储，验证成本控制行为：
- 对当日全部比赛跑数学分析，但只对通过阈值的前 N 场调用 LLM（上限=5）；
- 重复运行跳过已评审比赛（不再花费 LLM）；
- 阈值不达标时不调用 LLM；
- 读取推荐纯读库、不触发 LLM。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from app.agents.interfaces import CommitteeReviewer
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.team import Team
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.decision_log_repository import SqlAlchemyDecisionLogRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import DecisionLogORM, ValueBetORM
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository
from app.schemas.committee_review import CommitteeReview, CommitteeReviewContext, SelectionReview
from app.services.committee_review import CommitteeReviewService
from app.services.daily_top_picks import DailyRecommendationsReader, DailyTopPicksService
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate
from app.services.verified_market_quote import VerifiedMarketQuotePolicy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

TARGET = date(2026, 7, 10)
KICKOFF = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)
PAST = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)


class FakeReviewer(CommitteeReviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        self.calls += 1
        return CommitteeReview(
            executive_summary="摘要",
            key_strengths=["优势"],
            key_risks=["风险"],
            why_market_may_be_wrong="市场偏慢",
            why_model_recommends_or_rejects="模型依据",
            confidence_explanation="信心",
            betting_recommendation_explanation="小额价值",
            disagreements=[],
            selection_reviews=[
                SelectionReview(
                    selection_label=s.selection_label,
                    stance="support",  # type: ignore[arg-type]
                    agrees_with_model=True,
                    explanation="解释",
                )
                for s in context.selections
            ],
        )


def _picks_service(
    session: AsyncSession,
    reviewer: CommitteeReviewer,
    *,
    min_ev: float = 0.0,
    min_kelly: float = 0.0,
    min_confidence: float = 0.0,
    max_picks: int = 5,
) -> DailyTopPicksService:
    gate = RecommendationGate(
        min_decision_score=0.0, min_data_completeness=0.0, min_evidence_level=EvidenceLevel.E
    )
    builder = MatchAnalysisInputBuilder(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        bankroll=Money(Decimal("1000"), "EUR"),
        form_window=10,
        market_quote_policy=VerifiedMarketQuotePolicy(
            maximum_age=timedelta(days=36500),
        ),
    )
    analysis = FixtureAnalysisService(builder=builder, model=EnsembleMatchModel(), gate=gate)
    review = CommitteeReviewService(
        analysis=analysis,
        reviewer=reviewer,
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        model_version="gpt-test",
    )
    return DailyTopPicksService(
        fixtures=SqlAlchemyFixtureRepository(session),
        analysis=analysis,
        review=review,
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        competitions=SqlAlchemyCompetitionRepository(session),
        session=session,
        min_ev=min_ev,
        min_kelly=min_kelly,
        min_confidence=min_confidence,
        max_picks=max_picks,
    )


async def _count(session: AsyncSession, orm) -> int:  # type: ignore[no-untyped-def]
    return (await session.execute(select(func.count()).select_from(orm))).scalar_one()


async def _seed_six_fixtures(session: AsyncSession) -> list[Fixture]:
    """4 队循环赛（已完赛，提供近况）+ 6 场当日已排期比赛（各带宽松赔率）。"""
    comp = (
        await SqlAlchemyCompetitionRepository(session).add(
            Competition(name="FIFA World Cup", country="International")
        )
    ).id
    teams_repo = SqlAlchemyTeamRepository(session)
    fixtures_repo = SqlAlchemyFixtureRepository(session)
    teams = [(await teams_repo.add(Team(name=f"T{i}"))).id for i in range(4)]
    pairs = list(combinations(teams, 2))  # 6 对

    # 已完赛循环赛（PAST），给每支球队积累近况
    for day, (home, away) in enumerate(pairs, start=1):
        await fixtures_repo.add(
            Fixture(
                competition_id=comp,
                home_team_id=home,
                away_team_id=away,
                kickoff=PAST + timedelta(days=day),
                status=MatchStatus.FINISHED,
                score=Score(home=2, away=1),
            )
        )

    # 6 场当日已排期比赛 + 宽松赔率（隐含概率之和 < 1，保证至少一个正 EV）
    bookmakers = [
        await SqlAlchemyBookmakerRepository(session).add(Bookmaker(name=name))
        for name in ("BM-A", "BM-B")
    ]
    snaps = SqlAlchemyOddsSnapshotRepository(session)
    scheduled: list[Fixture] = []
    for home, away in pairs:
        fixture = await fixtures_repo.add(
            Fixture(competition_id=comp, home_team_id=home, away_team_id=away, kickoff=KICKOFF)
        )
        for bookmaker in bookmakers:
            for code, price in [("home", "2.50"), ("draw", "3.60"), ("away", "5.00")]:
                await snaps.add(
                    OddsSnapshot(
                        fixture_id=fixture.id,
                        bookmaker_id=bookmaker.id,
                        selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                        odds=Odds(Decimal(price)),
                        captured_at=KICKOFF - timedelta(hours=6),
                    )
                )
        scheduled.append(fixture)
    return scheduled


@pytest.mark.integration
async def test_reviews_only_top_five_and_calls_reviewer_at_most_five(
    db_session: AsyncSession,
) -> None:
    await _seed_six_fixtures(db_session)
    reviewer = FakeReviewer()
    report = await _picks_service(db_session, reviewer, max_picks=5).run(TARGET)

    assert report.fixtures_analyzed == 6  # 全部当日比赛都跑了数学分析
    assert report.fixtures_qualified == 6  # 全部达阈值（阈值=0 + 宽松 gate）
    assert report.fixtures_reviewed == 5  # 但只评审前 5（成本上限）
    assert report.fixtures_skipped_existing == 0
    assert reviewer.calls == 5  # LLM 最多被调用 5 次
    assert await _count(db_session, DecisionLogORM) == 5
    assert await _count(db_session, ValueBetORM) >= 5


@pytest.mark.integration
async def test_rerun_skips_already_reviewed_and_spends_no_more_llm_calls(
    db_session: AsyncSession,
) -> None:
    await _seed_six_fixtures(db_session)
    reviewer = FakeReviewer()
    service = _picks_service(db_session, reviewer, max_picks=5)

    first = await service.run(TARGET)
    assert first.fixtures_reviewed == 5

    second = await service.run(TARGET)
    assert second.fixtures_reviewed == 0
    assert second.fixtures_skipped_existing == 5
    assert reviewer.calls == 5  # 未再增加
    assert await _count(db_session, DecisionLogORM) == 5  # 未重复落库


@pytest.mark.integration
async def test_thresholds_gate_out_reviewer(db_session: AsyncSession) -> None:
    await _seed_six_fixtures(db_session)
    reviewer = FakeReviewer()
    # EV 阈值高到不可能达到（edge 上限约为 odds-1）→ 无人合格
    report = await _picks_service(db_session, reviewer, min_ev=100.0).run(TARGET)

    assert report.fixtures_qualified == 0
    assert report.fixtures_reviewed == 0
    assert reviewer.calls == 0
    assert await _count(db_session, DecisionLogORM) == 0


@pytest.mark.integration
async def test_reading_recommendations_never_calls_reviewer(
    db_session: AsyncSession,
) -> None:
    await _seed_six_fixtures(db_session)
    reviewer = FakeReviewer()
    await _picks_service(db_session, reviewer, max_picks=5).run(TARGET)

    # 读取器没有评审器依赖，结构上就不可能触发 LLM
    reader = DailyRecommendationsReader(
        fixtures=SqlAlchemyFixtureRepository(db_session),
        value_bets=SqlAlchemyValueBetRepository(db_session),
        decision_logs=SqlAlchemyDecisionLogRepository(db_session),
    )
    view = await reader.read(TARGET)

    assert view.count == 5  # 5 场有推荐
    rec = view.recommendations[0]
    assert rec.bets
    assert rec.review_summary == "摘要"
    assert reviewer.calls == 5  # 读取过程未再调用 LLM
