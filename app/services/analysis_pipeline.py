"""比赛分析编排（宪法工作流骨架）。

把已有的积木串成主流程（对应宪法工作流第 4–11 步）：
    数学模型产出 → 逐候选准入 gate → 日级 Top-N 选择
    → 组装证据包 → Claude 评审 → 融合并落库。

红线：落库的 ValueBet 数值全部来自数学模型（ModelCandidate）；只有 confidence
与 rationale 取自 Claude 评审。若 Claude 对某候选裁决为 DISCARD，则剔除不落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.interfaces import ReasoningEngine
from app.models.entities.fixture import Fixture
from app.models.entities.value_bet import ValueBet
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.schemas.reasoning import (
    CandidateBet,
    OutcomeProbability,
    ReasoningContext,
    ReasoningOutput,
    Verdict,
)
from app.services.daily_selection import CandidateEvaluation, DailySelectionService
from app.services.modeling import MatchModel, ModelCandidate, ModelOutput
from app.services.recommendation_gate import GateInput, RecommendationGate

NO_VALUE_MESSAGE = "今天没有值得下注的比赛。"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """一场比赛分析的产出。"""

    selected: list[ValueBet] = field(default_factory=list)
    reasoning: ReasoningOutput | None = None
    message: str | None = None  # 无价值时给出说明文案


class MatchAnalysisPipeline:
    """单场比赛分析编排。数学模型、gate、选择器、评审引擎、仓储均由外部注入。"""

    def __init__(
        self,
        *,
        model: MatchModel,
        gate: RecommendationGate,
        selector: DailySelectionService,
        reasoning: ReasoningEngine,
        value_bet_repository: ValueBetRepository,
    ) -> None:
        self._model = model
        self._gate = gate
        self._selector = selector
        self._reasoning = reasoning
        self._value_bets = value_bet_repository

    async def analyze_fixture(self, fixture: Fixture) -> AnalysisResult:
        # Step 4-7：数学模型产出概率、EV、候选（真相来源）
        output = await self._model.analyze(fixture)

        # Step 8：逐候选跑准入 gate
        evaluations = [self._evaluate(candidate) for candidate in output.candidates]

        # 日级 Top-N 选择（宪法：每日最多 3 场）
        selection = self._selector.select(evaluations)
        if not selection.has_recommendations:
            return AnalysisResult(message=NO_VALUE_MESSAGE)

        by_label = {c.selection.label: c for c in output.candidates}
        selected_candidates = [by_label[e.label] for e in selection.selected]

        # Step 9-10：组装证据包 → Claude 评审（只产出定性评审，不含数值）
        context = self._build_context(fixture, output, selected_candidates)
        reasoning = await self._reasoning.analyze(context)

        # Step 11：融合并落库（数值来自模型，confidence/rationale 来自 Claude）
        value_bets = await self._persist(fixture, selected_candidates, reasoning)
        return AnalysisResult(selected=value_bets, reasoning=reasoning)

    def _evaluate(self, candidate: ModelCandidate) -> CandidateEvaluation:
        gate_input = GateInput(
            decision_score=candidate.decision_score,
            expected_value=candidate.edge.edge,
            data_completeness=candidate.data_completeness,
            evidence_level=candidate.evidence_level,
            risk_level=candidate.risk_level,
        )
        decision = self._gate.evaluate(gate_input)
        return CandidateEvaluation(
            label=candidate.selection.label,
            gate_input=gate_input,
            gate_decision=decision,
        )

    @staticmethod
    def _build_context(
        fixture: Fixture,
        output: ModelOutput,
        candidates: list[ModelCandidate],
    ) -> ReasoningContext:
        # 注意：队名/赛事名、伤停、首发、盘口变化等应由数据层填充；
        # 骨架阶段先以 id 占位，后续接入 providers/repositories 后补全。
        return ReasoningContext(
            fixture_summary=f"{fixture.home_team_id}（主） vs {fixture.away_team_id}（客）",
            kickoff_iso=fixture.kickoff.isoformat(),
            competition=str(fixture.competition_id),
            outcome_probabilities=[
                OutcomeProbability(outcome=result.value, model_probability=prob.value)
                for result, prob in output.outcome_probabilities.items()
            ],
            expected_goals_home=output.expected_goals.home if output.expected_goals else None,
            expected_goals_away=output.expected_goals.away if output.expected_goals else None,
            elo_home=output.elo_home,
            elo_away=output.elo_away,
            candidate_bets=[_to_candidate_bet(c) for c in candidates],
        )

    async def _persist(
        self,
        fixture: Fixture,
        candidates: list[ModelCandidate],
        reasoning: ReasoningOutput,
    ) -> list[ValueBet]:
        assessments = {a.selection_label: a for a in reasoning.selection_assessments}
        saved: list[ValueBet] = []
        for candidate in candidates:
            assessment = assessments.get(candidate.selection.label)
            # 尊重 Claude 评审：裁决为放弃则不落库
            if assessment is not None and assessment.verdict is Verdict.DISCARD:
                continue
            value_bet = ValueBet(
                fixture_id=fixture.id,
                selection=candidate.selection,
                odds=candidate.odds,
                bookmaker_id=candidate.bookmaker_id,
                model_probability=candidate.model_probability,
                edge=candidate.edge,
                stake=candidate.stake,
                confidence=assessment.confidence if assessment is not None else None,
                rationale=assessment.rationale if assessment is not None else None,
            )
            saved.append(await self._value_bets.add(value_bet))
        return saved


def _to_candidate_bet(candidate: ModelCandidate) -> CandidateBet:
    """把模型候选转换为证据包中的候选条目（供 Claude 评审阅读）。"""
    stake = candidate.stake
    return CandidateBet(
        selection_label=candidate.selection.label,
        decimal_odds=float(candidate.odds.decimal),
        model_probability=candidate.model_probability.value,
        edge=candidate.edge.edge,
        expected_value=candidate.edge.expected_value_per_unit,
        kelly_fraction=stake.fraction_of_bankroll if stake is not None else 0.0,
        recommended_stake=float(stake.amount.amount) if stake is not None else None,
        bookmaker=str(candidate.bookmaker_id) if candidate.bookmaker_id is not None else None,
    )
