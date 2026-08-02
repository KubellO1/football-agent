"""MatchAnalysisPipeline 端到端单元测试。

用真实数学模型 + 准入 gate + 日级选择，配合假 ReasoningEngine 与内存仓储，
验证主流程三条路径：
① 通过 gate 且 LLM 保留 → 落库；
② 通过 gate 但 LLM 放弃(DISCARD) → 剔除不落库；
③ 无正 EV(gate 全拒) → 返回“今天没有值得下注的比赛”。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from app.agents.interfaces import ReasoningEngine
from app.models.entities.fixture import Fixture
from app.models.value_objects.betting import ValueEdge
from app.models.value_objects.decision import (
    DataCompleteness,
    DecisionScore,
    EvidenceLevel,
    RiskLevel,
)
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.models.value_objects.statistics import TeamStatistics
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.schemas.reasoning import (
    ReasoningContext,
    ReasoningOutput,
    SelectionAssessment,
    Verdict,
)
from app.services.analysis_pipeline import NO_VALUE_MESSAGE, MatchAnalysisPipeline
from app.services.daily_selection import DailySelectionService
from app.services.modeling import MarketQuote, ModelCandidate, ModelInput
from app.services.models.ensemble import EnsembleMatchModel
from app.services.models.lambda_estimator import LeagueAverages
from app.services.recommendation_gate import RecommendationGate

if TYPE_CHECKING:
    from app.models.entities.value_bet import ValueBet


class FakeReasoningEngine(ReasoningEngine):
    """假评审引擎：对每个候选给出固定裁决与信心。"""

    def __init__(self, verdict: Verdict = Verdict.KEEP) -> None:
        self._verdict = verdict

    async def analyze(self, context: ReasoningContext) -> ReasoningOutput:
        assessments = [
            SelectionAssessment(
                selection_label=cb.selection_label,
                verdict=self._verdict,
                confidence=0.7,
                rationale="测试理由",
            )
            for cb in context.candidate_bets
        ]
        return ReasoningOutput(chief_summary="测试汇总", selection_assessments=assessments)


class InMemoryValueBetRepository(ValueBetRepository):
    """内存版推荐仓储，用于测试。"""

    def __init__(self) -> None:
        self._store: dict[UUID, ValueBet] = {}

    async def get(self, entity_id: UUID) -> ValueBet | None:
        return self._store.get(entity_id)

    async def add(self, entity: ValueBet) -> ValueBet:
        self._store[entity.id] = entity
        return entity

    async def list_by_fixture(self, fixture_id: UUID) -> list[ValueBet]:
        return [v for v in self._store.values() if v.fixture_id == fixture_id]

    async def list_created_between(self, start: datetime, end: datetime) -> list[ValueBet]:
        return [v for v in self._store.values() if start <= v.created_at < end]


def _stats(*, gf: int, ga: int) -> TeamStatistics:
    return TeamStatistics(
        matches_played=10,
        wins=6,
        draws=2,
        losses=2,
        goals_for=gf,
        goals_against=ga,
        xg_for=float(gf),
        xg_against=float(ga),
    )


def _model_input(*, home_odds: float, completeness: float = 95.0) -> ModelInput:
    fixture = Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=datetime(2026, 7, 3, 18, 0, tzinfo=UTC),
    )
    return ModelInput(
        fixture=fixture,
        home_stats=_stats(gf=20, ga=10),  # 强主队 → 主胜概率高
        away_stats=_stats(gf=10, ga=20),
        league=LeagueAverages(goals_per_game=1.4),
        quotes=[
            MarketQuote(
                Selection(MarketType.MATCH_RESULT, "home"),
                Odds(Decimal(str(home_odds))),
            )
        ],
        bankroll=Money(Decimal("1000")),
        data_completeness=DataCompleteness(completeness),
        evidence_level=EvidenceLevel.B,
    )


def _pipeline(reasoning: ReasoningEngine, repo: ValueBetRepository) -> MatchAnalysisPipeline:
    return MatchAnalysisPipeline(
        model=EnsembleMatchModel(),
        gate=RecommendationGate(),
        selector=DailySelectionService(),
        reasoning=reasoning,
        value_bet_repository=repo,
    )


@pytest.mark.unit
def test_gate_input_uses_expected_value_per_unit_not_probability_edge() -> None:
    probability = Probability(0.55)
    odds = Odds(Decimal("2.00"))
    candidate = ModelCandidate(
        selection=Selection(MarketType.MATCH_RESULT, "home"),
        odds=odds,
        model_probability=probability,
        edge=ValueEdge(model_probability=probability, odds=odds),
        decision_score=DecisionScore(90.0),
        data_completeness=DataCompleteness(95.0),
        evidence_level=EvidenceLevel.B,
        risk_level=RiskLevel.MEDIUM,
    )
    pipeline = _pipeline(FakeReasoningEngine(), InMemoryValueBetRepository())

    evaluation = pipeline._evaluate(candidate)

    probability_edge = probability.value - odds.implied_probability.value
    assert probability_edge == pytest.approx(0.05)
    assert candidate.edge.expected_value_per_unit == pytest.approx(0.10)
    assert evaluation.gate_input.expected_value == pytest.approx(0.10)


@pytest.mark.unit
async def test_value_bet_is_persisted_when_kept() -> None:
    repo = InMemoryValueBetRepository()
    pipeline = _pipeline(FakeReasoningEngine(Verdict.KEEP), repo)
    model_input = _model_input(home_odds=2.5)  # 强主队 + 高赔率 → 正 EV、高评分

    result = await pipeline.analyze(model_input)

    assert result.message is None
    assert len(result.selected) >= 1
    bet = result.selected[0]
    assert bet.fixture_id == model_input.fixture.id
    # confidence/rationale 来自 LLM，数值来自模型
    assert bet.confidence == pytest.approx(0.7)
    assert bet.rationale == "测试理由"
    assert bet.edge.edge > 0.0
    # 已落库
    stored = await repo.list_by_fixture(model_input.fixture.id)
    assert len(stored) == len(result.selected)


@pytest.mark.unit
async def test_discarded_by_reviewer_is_not_persisted() -> None:
    repo = InMemoryValueBetRepository()
    pipeline = _pipeline(FakeReasoningEngine(Verdict.DISCARD), repo)
    model_input = _model_input(home_odds=2.5)

    result = await pipeline.analyze(model_input)

    # 通过 gate 但 LLM 放弃 → 不落库
    assert result.selected == []
    assert result.reasoning is not None
    assert result.message is None
    assert await repo.list_by_fixture(model_input.fixture.id) == []


@pytest.mark.unit
async def test_no_value_returns_message() -> None:
    repo = InMemoryValueBetRepository()
    pipeline = _pipeline(FakeReasoningEngine(), repo)
    model_input = _model_input(home_odds=1.05)  # 赔率过低 → 负 EV，gate 全拒

    result = await pipeline.analyze(model_input)

    assert result.message == NO_VALUE_MESSAGE
    assert result.selected == []
