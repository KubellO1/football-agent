"""CommitteeReviewService 的集成测试（需真实 Postgres）。

用假的 CommitteeReviewer（不触达 LLM）+ 真实数学模型 + 真实仓储，验证：
- 每次评审落一条 DecisionLog（含 model_version / prompt_version / 完整 review JSON）；
- gate 批准的推荐落入 ValueBet，且数值与模型一致（LLM 不改数值）；
- LLM 的异议被记录进 DecisionLog（不改变落库决策）；
- 数据不足 / 无赔率时不调用 LLM、不落库。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from app.schemas.committee_review import (
    CommitteeReview,
    CommitteeReviewContext,
    SelectionReview,
    SelectionStance,
)
from app.services.committee_review import CommitteeReviewService
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

PAST = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


class FakeReviewer(CommitteeReviewer):
    """确定性假评审：记录调用次数，可配置是否认同模型。"""

    def __init__(self, *, agree: bool = True) -> None:
        self.calls = 0
        self._agree = agree

    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        self.calls += 1
        stance = SelectionStance.SUPPORT if self._agree else SelectionStance.AGAINST
        return CommitteeReview(
            executive_summary="执行摘要",
            key_strengths=["优势1"],
            key_risks=["风险1"],
            why_market_may_be_wrong="市场反应偏慢",
            why_model_recommends_or_rejects="模型基于近况与赔率",
            confidence_explanation="信心中等",
            betting_recommendation_explanation="小额价值下注",
            disagreements=[] if self._agree else ["整体上对模型持保留意见"],
            selection_reviews=[
                SelectionReview(
                    selection_label=s.selection_label,
                    stance=stance,
                    agrees_with_model=self._agree,
                    explanation=f"对 {s.selection_label} 的解释",
                )
                for s in context.selections
            ],
        )


def _service(
    session: AsyncSession, reviewer: CommitteeReviewer, *, permissive_gate: bool = False
) -> CommitteeReviewService:
    gate = (
        RecommendationGate(
            min_decision_score=0.0,
            min_data_completeness=0.0,
            min_evidence_level=EvidenceLevel.E,
        )
        if permissive_gate
        else RecommendationGate()
    )
    builder = MatchAnalysisInputBuilder(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        bankroll=Money(Decimal("1000"), "EUR"),
        form_window=10,
    )
    analysis = FixtureAnalysisService(builder=builder, model=EnsembleMatchModel(), gate=gate)
    return CommitteeReviewService(
        analysis=analysis,
        reviewer=reviewer,
        decision_logs=SqlAlchemyDecisionLogRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        model_version="gpt-test",
    )


async def _count(session: AsyncSession, orm) -> int:  # type: ignore[no-untyped-def]
    return (await session.execute(select(func.count()).select_from(orm))).scalar_one()


async def _finished(
    session: AsyncSession, comp: UUID, home: UUID, away: UUID, hs: int, a_s: int, *, day: int
) -> None:
    await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=comp,
            home_team_id=home,
            away_team_id=away,
            kickoff=PAST + timedelta(days=day),
            status=MatchStatus.FINISHED,
            score=Score(home=hs, away=a_s),
        )
    )


async def _seed(session: AsyncSession, *, with_odds: bool) -> Fixture:
    comp = (
        await SqlAlchemyCompetitionRepository(session).add(Competition(name="L", country="C"))
    ).id
    teams = SqlAlchemyTeamRepository(session)
    home = (await teams.add(Team(name="Home"))).id
    away = (await teams.add(Team(name="Away"))).id
    o1 = (await teams.add(Team(name="Opp1"))).id
    o2 = (await teams.add(Team(name="Opp2"))).id

    await _finished(session, comp, home, o1, 2, 0, day=1)
    await _finished(session, comp, o1, home, 1, 2, day=2)
    await _finished(session, comp, home, o2, 3, 1, day=3)
    await _finished(session, comp, away, o1, 0, 1, day=4)
    await _finished(session, comp, o2, away, 2, 1, day=5)
    await _finished(session, comp, away, o2, 1, 1, day=6)

    fixture = await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=comp,
            home_team_id=home,
            away_team_id=away,
            kickoff=KICKOFF,
            status=MatchStatus.SCHEDULED,
        )
    )
    if with_odds:
        bookmaker = await SqlAlchemyBookmakerRepository(session).add(Bookmaker(name="BM"))
        snaps = SqlAlchemyOddsSnapshotRepository(session)
        # 隐含概率之和 < 1（0.4+0.278+0.2），保证至少一个选项正 edge。
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
    return fixture


@pytest.mark.integration
async def test_review_persists_decision_log_and_value_bets(db_session: AsyncSession) -> None:
    fixture = await _seed(db_session, with_odds=True)
    reviewer = FakeReviewer(agree=True)
    result = await _service(db_session, reviewer, permissive_gate=True).review(fixture)

    assert reviewer.calls == 1
    assert result.review is not None
    assert result.decision_log_id is not None

    # 落库的 ValueBet 数 == gate 批准的推荐数（>0），且数值与分析一致
    approved = [s for s in result.analysis.selections if s.recommended]
    assert len(approved) >= 1
    assert len(result.value_bet_ids) == len(approved)
    assert await _count(db_session, ValueBetORM) == len(approved)

    approved_probs = sorted(round(s.model_probability, 6) for s in approved)
    rows = (await db_session.execute(select(ValueBetORM))).scalars().all()
    assert sorted(round(r.model_probability, 6) for r in rows) == approved_probs  # 数值未被改动

    # DecisionLog：可复现性元数据 + 完整 review 存档
    log = (await db_session.execute(select(DecisionLogORM))).scalar_one()
    assert log.model_version == "gpt-test"
    assert log.prompt_version == "committee-review/zh-v2"
    assert log.summary == "执行摘要"
    assert log.review is not None and log.review["executive_summary"] == "执行摘要"
    assert log.fixture_id == fixture.id


@pytest.mark.integration
async def test_reviewer_disagreement_is_recorded_not_acted_on(db_session: AsyncSession) -> None:
    fixture = await _seed(db_session, with_odds=True)
    reviewer = FakeReviewer(agree=False)  # LLM 不认同模型
    result = await _service(db_session, reviewer, permissive_gate=True).review(fixture)

    # 尽管 LLM 反对，被 gate 批准的推荐仍照常落库（数值/决策不受 LLM 影响）
    approved = [s for s in result.analysis.selections if s.recommended]
    assert len(result.value_bet_ids) == len(approved) >= 1

    # 异议被记录进 DecisionLog
    log = (await db_session.execute(select(DecisionLogORM))).scalar_one()
    assert any("保留意见" in r or "不认同" in r for r in log.rejected_alternatives)


@pytest.mark.integration
async def test_no_odds_skips_reviewer_and_persistence(db_session: AsyncSession) -> None:
    fixture = await _seed(db_session, with_odds=False)
    reviewer = FakeReviewer()
    result = await _service(db_session, reviewer).review(fixture)

    assert reviewer.calls == 0  # 无候选可评审 → 不调用 LLM
    assert result.review is None
    assert result.decision_log_id is None
    assert await _count(db_session, DecisionLogORM) == 0
    assert await _count(db_session, ValueBetORM) == 0


@pytest.mark.integration
async def test_insufficient_history_skips_reviewer(db_session: AsyncSession) -> None:
    comp = (
        await SqlAlchemyCompetitionRepository(db_session).add(Competition(name="L", country="C"))
    ).id
    teams = SqlAlchemyTeamRepository(db_session)
    home = (await teams.add(Team(name="H"))).id
    away = (await teams.add(Team(name="A"))).id
    fixture = await SqlAlchemyFixtureRepository(db_session).add(
        Fixture(competition_id=comp, home_team_id=home, away_team_id=away, kickoff=KICKOFF)
    )
    reviewer = FakeReviewer()
    result = await _service(db_session, reviewer).review(fixture)

    assert reviewer.calls == 0
    assert result.decision_log_id is None
    assert await _count(db_session, DecisionLogORM) == 0
