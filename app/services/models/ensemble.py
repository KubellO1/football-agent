"""EnsembleMatchModel：把各子模型组合成 MatchModel 的真实实现。

串联链路：强度→λ 估计 → Poisson 概率 → ValueDetector 价值评估 → Kelly 下注。
产出 ModelOutput / ModelCandidate（含 gate 所需的评分/完整度/证据/风险）。

Elo 作为独立信号接入：透传球队评分并计算主队 Elo 期望得分，但**不参与概率
融合**——概率仍由 Poisson 主导（融合需回测标定，未标定即臆造）。

起步仅支持胜平负(1X2)市场；大小球、BTTS 等后续扩展。综合评分与风险等级
采用可解释的简单规则起步，后续可外置为独立策略。
"""

from __future__ import annotations

from app.models.value_objects.betting import ValueEdge
from app.models.value_objects.decision import DecisionScore, RiskLevel
from app.models.value_objects.metrics import ExpectedGoals
from app.models.value_objects.score import MatchResult
from app.services.modeling import MatchModel, ModelCandidate, ModelInput, ModelOutput
from app.services.models.calibration import TemperatureCalibrator
from app.services.models.elo import EloModel
from app.services.models.kelly import KellyCalculator
from app.services.models.lambda_estimator import LambdaEstimator
from app.services.models.poisson import PoissonModel
from app.services.models.value_detector import ValueDetector

_CODE_TO_RESULT: dict[str, MatchResult] = {
    "home": MatchResult.HOME,
    "draw": MatchResult.DRAW,
    "away": MatchResult.AWAY,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class EnsembleMatchModel(MatchModel):
    """由 λ估计 + Poisson + 价值检测 + Kelly 组合的比赛模型（Elo 作独立信号）。"""

    def __init__(
        self,
        *,
        estimator: LambdaEstimator | None = None,
        poisson: PoissonModel | None = None,
        detector: ValueDetector | None = None,
        kelly: KellyCalculator | None = None,
        elo: EloModel | None = None,
        calibrator: TemperatureCalibrator | None = None,
    ) -> None:
        self._estimator = estimator or LambdaEstimator()
        self._poisson = poisson or PoissonModel()
        self._detector = detector or ValueDetector()
        self._kelly = kelly or KellyCalculator()
        self._elo = elo or EloModel()
        # 概率校准（温度缩放）。默认 T=1（恒等）→ 不改变既有行为，配置后才生效。
        self._calibrator = calibrator or TemperatureCalibrator()

    async def analyze(self, model_input: ModelInput) -> ModelOutput:
        lam_home, lam_away = self._estimator.estimate(
            model_input.home_stats, model_input.away_stats, model_input.league
        )
        probabilities = self._poisson.match_result_probabilities(lam_home, lam_away)
        # 校准：温度缩放修正过度自信（保持 argmax，仅改概率大小 → 修正 EV/Kelly/信心）。
        probabilities = self._calibrator.calibrate(probabilities)

        candidates: list[ModelCandidate] = []
        for quote in model_input.quotes:
            result = _CODE_TO_RESULT.get(quote.selection.code)
            # 起步仅支持 1X2；无法映射的市场跳过（后续扩展 大小球/BTTS）
            if result is None:
                continue

            probability = probabilities[result]
            assessment = self._detector.assess(probability, quote.odds)
            edge = ValueEdge(model_probability=probability, odds=quote.odds)
            stake = self._kelly.compute(probability, quote.odds, model_input.bankroll)

            candidates.append(
                ModelCandidate(
                    selection=quote.selection,
                    odds=quote.odds,
                    model_probability=probability,
                    edge=edge,
                    decision_score=self._score(
                        model_input.data_completeness.value,
                        probability.value,
                        assessment.edge,
                    ),
                    data_completeness=model_input.data_completeness,
                    evidence_level=model_input.evidence_level,
                    risk_level=self._risk(model_input.data_completeness.value, probability.value),
                    stake=stake,
                    bookmaker_id=quote.bookmaker_id,
                )
            )

        # Elo：透传评分并计算主队期望得分（独立信号，不改概率）
        elo_expected: float | None = None
        if model_input.home_elo is not None and model_input.away_elo is not None:
            elo_expected = self._elo.expected_score(model_input.home_elo, model_input.away_elo)

        return ModelOutput(
            outcome_probabilities=probabilities,
            expected_goals=ExpectedGoals(home=lam_home, away=lam_away),
            elo_home=model_input.home_elo,
            elo_away=model_input.away_elo,
            elo_expected_home=elo_expected,
            candidates=candidates,
        )

    @staticmethod
    def _score(completeness: float, probability: float, edge: float) -> DecisionScore:
        # 起步综合评分（0-100，可解释；后续可外置为独立评分策略）：
        # 0.5×数据完整度 + 0.3×概率 + 0.2×edge（edge 达 0.2 记为满分项）
        edge_component = _clamp(max(edge, 0.0) / 0.2 * 100.0)
        score = 0.5 * completeness + 0.3 * (probability * 100.0) + 0.2 * edge_component
        return DecisionScore(_clamp(score))

    @staticmethod
    def _risk(completeness: float, probability: float) -> RiskLevel:
        # 起步风险规则：完整度与概率越高，风险越低
        if completeness >= 90.0 and probability >= 0.5:
            return RiskLevel.LOW
        if completeness >= 90.0 or probability >= 0.5:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH
