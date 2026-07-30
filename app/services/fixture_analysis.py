"""DB 驱动的单场比赛分析（纯数学、确定性、无外部 API）。

本模块把「读库 → 组装不可变输入 → 数学模型 → 准入 gate」串成一条自洽链路：

- ``MatchAnalysisInputBuilder``：只从 PostgreSQL 读取（比赛、球队、赔率快照），
  把双方近期完赛战绩汇总为 TeamStatistics、由赛事历史算出联赛场均进球基准、
  从赔率快照取每个 1X2 选项的最优赔率，组装成**不可变**的 ModelInput。
- ``FixtureAnalysisService``：用既有的 Poisson/Elo/Kelly/Value 集成模型产出概率、
  edge、EV、Kelly 下注，并用 RecommendationGate 判定是否推荐；**不调用 LLM、
  不访问任何外部数据源**，confidence 与 explanation 均由数值确定性地导出。

红线：本路径不做任何网络抓取（宪法：分析基于已入库数据）；xG 未采集，故以实际
进球作为 xG 代理（λ 估计器默认吃 xG）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.markets import MarketType
from app.models.value_objects.odds import Odds
from app.models.value_objects.statistics import TeamStatistics
from app.services.modeling import (
    MarketQuote,
    MatchModel,
    ModelCandidate,
    ModelInput,
    ModelOutput,
)
from app.services.models.lambda_estimator import LeagueAverages
from app.services.recommendation_gate import GateDecision, GateInput, RecommendationGate

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.odds_snapshot import OddsSnapshot
    from app.models.value_objects.money import Money
    from app.providers.interfaces.injury_provider import InjuryProvider
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
    from app.repositories.interfaces.reference import TeamRepository

logger = get_logger(__name__)

INSUFFICIENT_DATA_MESSAGE = "历史数据不足，无法建模分析（缺少双方近期完赛记录或联赛基准）。"
NO_ODDS_MESSAGE = "缺少可用赔率，只能给出概率，无法评估价值。"
NO_VALUE_MESSAGE = "本场无满足准入门槛的价值投注。"

# 采集数据来自 API-Football（专业统计源），证据等级记为 B。
_EVIDENCE_LEVEL = EvidenceLevel.B

# 赔率去噪参数：某盘口最新快照晚于「最新 - 该窗口」才视为新鲜（否则丢弃为过期）；
# 相对中位数偏离超过该倍数的赔率视为极端离群点丢弃。
_ODDS_STALE_WINDOW = timedelta(days=2)
_ODDS_OUTLIER_RATIO = 3.0


@dataclass(frozen=True, slots=True)
class SelectionAnalysis:
    """单个 1X2 选项的分析结论（数值全部来自数学模型 + gate）。"""

    code: str
    selection_label: str
    decimal_odds: float
    model_probability: float
    implied_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    kelly_stake: float
    currency: str
    recommended: bool
    confidence: float
    reasons: list[str]
    explanation: str
    confidence_killer: str | None = None


@dataclass(frozen=True, slots=True)
class FixtureAnalysisResult:
    """一场比赛的分析结果（不可变）。数据不足时 probabilities 为空并给出 message。"""

    fixture_id: UUID
    probabilities: dict[str, float] = field(default_factory=dict)
    expected_goals_home: float | None = None
    expected_goals_away: float | None = None
    selections: list[SelectionAnalysis] = field(default_factory=list)
    data_completeness: float = 0.0
    message: str | None = None
    confidence_killer: str | None = None


class MatchAnalysisInputBuilder:
    """只读库、把一场比赛所需的全部输入汇总为不可变 ModelInput。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        odds_snapshots: OddsSnapshotRepository,
        bankroll: Money,
        form_window: int = 10,
        injury_provider: InjuryProvider | None = None,
    ) -> None:
        self._fixtures = fixtures
        self._teams = teams
        self._odds = odds_snapshots
        self._bankroll = bankroll
        self._form_window = form_window
        self._injury = injury_provider

    async def build(self, fixture: Fixture, *, as_of: datetime) -> ModelInput | None:
        """按同一决策时点组装 ModelInput；数据不足时返回 None。"""
        self._validate_as_of(as_of)
        home_stats = await self._team_stats(
            fixture.home_team_id,
            exclude=fixture.id,
            before=as_of,
        )
        away_stats = await self._team_stats(
            fixture.away_team_id,
            exclude=fixture.id,
            before=as_of,
        )
        league = await self._league_averages(fixture.competition_id, before=as_of)
        if home_stats.matches_played == 0 or away_stats.matches_played == 0 or league is None:
            return None

        quotes = await self._quotes(fixture.id, as_of=as_of)
        home_elo, away_elo = await self._elos(fixture.home_team_id, fixture.away_team_id)

        # Query injury data for this fixture (if provider available).
        injury_count = await self._query_injury_count(fixture)

        completeness = self._completeness(
            home_stats, away_stats, quotes, home_elo, away_elo, injury_count
        )

        return ModelInput(
            fixture=fixture,
            home_stats=home_stats,
            away_stats=away_stats,
            league=league,
            quotes=quotes,
            bankroll=self._bankroll,
            data_completeness=completeness,
            evidence_level=_EVIDENCE_LEVEL,
            home_elo=home_elo,
            away_elo=away_elo,
        )

    async def _team_stats(
        self, team_id: UUID, *, exclude: UUID, before: datetime | None = None
    ) -> TeamStatistics:
        fixtures = await self._fixtures.list_finished_by_team(
            team_id, limit=self._form_window, exclude_fixture_id=exclude, before=before
        )
        wins = draws = losses = goals_for = goals_against = played = 0
        for f in fixtures:
            if f.score is None:
                continue
            is_home = f.home_team_id == team_id
            gf = f.score.home if is_home else f.score.away
            ga = f.score.away if is_home else f.score.home
            played += 1
            goals_for += gf
            goals_against += ga
            if gf > ga:
                wins += 1
            elif gf < ga:
                losses += 1
            else:
                draws += 1
        # 无 xG 采集：以实际进球作为 xG 代理（λ 估计器默认吃 xG）。
        return TeamStatistics(
            matches_played=played,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
            xg_for=float(goals_for),
            xg_against=float(goals_against),
        )

    async def _league_averages(
        self, competition_id: UUID, *, before: datetime | None = None
    ) -> LeagueAverages | None:
        fixtures = await self._fixtures.list_finished_by_competition(competition_id, before=before)
        total_goals = 0
        games = 0
        for f in fixtures:
            if f.score is None:
                continue
            total_goals += f.score.home + f.score.away
            games += 1
        if games == 0:
            return None
        # 每队场均进球 = 总进球 /（2 × 场次）
        per_team = total_goals / (2 * games)
        if per_team <= 0:
            return None
        return LeagueAverages(goals_per_game=per_team)

    async def _quotes(self, fixture_id: UUID, *, as_of: datetime) -> list[MarketQuote]:
        """按选项汇总去噪后的赔率报价。

        去噪三步（不取全体最大值）：① 每个博彩公司只保留最新一条快照；② 丢弃明显
        过期（远早于该选项最新时间）与相对中位数极端离群的赔率；③ 取剩余各家中位数。
        """
        self._validate_as_of(as_of)
        snapshots = await self._odds.list_by_fixture(fixture_id, as_of=as_of)
        if any(snapshot.captured_at > as_of for snapshot in snapshots):
            raise ValueError("repository returned an odds snapshot later than as_of")
        # code -> bookmaker_id -> 该家最新快照
        by_code: dict[str, dict[UUID, OddsSnapshot]] = {}
        for snap in snapshots:
            if snap.selection.market is not MarketType.MATCH_RESULT:
                continue
            per_book = by_code.setdefault(snap.selection.code, {})
            current = per_book.get(snap.bookmaker_id)
            if current is None or snap.captured_at > current.captured_at:
                per_book[snap.bookmaker_id] = snap

        quotes: list[MarketQuote] = []
        for latest_per_book in by_code.values():
            quote = self._denoise_selection(list(latest_per_book.values()))
            if quote is not None:
                quotes.append(quote)
        return quotes

    @staticmethod
    def _denoise_selection(snaps: list[OddsSnapshot]) -> MarketQuote | None:
        """从「各家最新快照」中去掉过期/离群，取中位数赔率，返回一条报价。"""
        if not snaps:
            return None
        # ① 过期过滤：只保留接近最新时间的家
        newest = max(s.captured_at for s in snaps)
        fresh = [s for s in snaps if s.captured_at >= newest - _ODDS_STALE_WINDOW]
        # ② 离群过滤：相对中位数偏离过大者剔除（样本 >=3 时才做，避免误伤）
        values = [float(s.odds.decimal) for s in fresh]
        med = median(values)
        if len(fresh) >= 3:
            lo, hi = med / _ODDS_OUTLIER_RATIO, med * _ODDS_OUTLIER_RATIO
            kept = [s for s in fresh if lo <= float(s.odds.decimal) <= hi]
            if kept:
                fresh = kept
        # ③ 取中位数赔率；代表性 bookmaker 取最接近中位数的一家
        med_odds = median(float(s.odds.decimal) for s in fresh)
        representative = min(fresh, key=lambda s: abs(float(s.odds.decimal) - med_odds))
        return MarketQuote(
            selection=representative.selection,
            odds=Odds(Decimal(str(round(med_odds, 3)))),
            bookmaker_id=representative.bookmaker_id,
        )

    @staticmethod
    def _validate_as_of(as_of: datetime) -> None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

    async def _elos(
        self, home_team_id: UUID, away_team_id: UUID
    ) -> tuple[float | None, float | None]:
        home = await self._teams.get(home_team_id)
        away = await self._teams.get(away_team_id)
        home_elo = home.elo.value if home is not None and home.elo is not None else None
        away_elo = away.elo.value if away is not None and away.elo is not None else None
        return home_elo, away_elo

    def _completeness(
        self,
        home_stats: TeamStatistics,
        away_stats: TeamStatistics,
        quotes: list[MarketQuote],
        home_elo: float | None,
        away_elo: float | None,
        injury_count: int = 0,
    ) -> DataCompleteness:
        # 可解释启发式（0-100）：近况覆盖 40 + 联赛基准 20 + 赔率 30 + Elo 10 - 伤病惩罚。
        form_ratio = min(home_stats.matches_played, away_stats.matches_played) / self._form_window
        score = 40.0 * min(form_ratio, 1.0)
        score += 20.0  # 能走到这里说明联赛基准已具备
        score += 30.0 if quotes else 0.0
        score += 10.0 if home_elo is not None and away_elo is not None else 0.0
        # Injury penalty: -5 per injured player, max -20.
        score -= min(injury_count * 5.0, 20.0)
        return DataCompleteness(max(0.0, min(100.0, score)))

    async def _query_injury_count(self, fixture: Fixture) -> int:
        """Query injury data for fixture; return total player count or 0 if unavailable."""
        if self._injury is None:
            return 0
        try:
            ext_id = fixture.external_id
            if ext_id is None:
                return 0
            teams = await self._injury.get_injuries(fixture_id=int(ext_id))
            return sum(t.total_injured for t in teams)
        except Exception:
            logger.debug("Injury query failed for fixture %s", fixture.id, exc_info=True)
            return 0


@dataclass(frozen=True, slots=True)
class ReviewedSelection:
    """一个模型候选连同其确定性 gate 判定（供上层评审/落库使用）。"""

    candidate: ModelCandidate
    decision: GateDecision


@dataclass(frozen=True, slots=True)
class DetailedAnalysis:
    """一场比赛的完整分析：原始模型输入/输出 + 逐候选 gate 判定 + 对外结果视图。

    评审层需要权威的 ModelCandidate（含 Odds/ValueEdge/Stake 领域对象）来落库，
    故在对外的 FixtureAnalysisResult 之外额外暴露这些内部产物。
    """

    fixture: Fixture
    model_input: ModelInput | None
    model_output: ModelOutput | None
    reviewed: list[ReviewedSelection]
    result: FixtureAnalysisResult


class FixtureAnalysisService:
    """DB 驱动的单场分析：数学模型 + 准入 gate，确定性、无外部 API。"""

    def __init__(
        self,
        *,
        builder: MatchAnalysisInputBuilder,
        model: MatchModel,
        gate: RecommendationGate,
    ) -> None:
        self._builder = builder
        self._model = model
        self._gate = gate

    async def analyze(
        self,
        fixture: Fixture,
        *,
        as_of: datetime | None = None,
    ) -> FixtureAnalysisResult:
        """对外的确定性分析结果（行为与产出保持稳定）。"""
        return (await self.analyze_detailed(fixture, as_of=as_of)).result

    async def analyze_detailed(
        self,
        fixture: Fixture,
        *,
        as_of: datetime | None = None,
    ) -> DetailedAnalysis:
        """在对外结果之外，额外返回模型输入/输出与逐候选 gate 判定（供评审层落库）。"""
        decision_time = as_of or datetime.now(UTC)
        MatchAnalysisInputBuilder._validate_as_of(decision_time)
        model_input = await self._builder.build(fixture, as_of=decision_time)
        if model_input is None:
            return DetailedAnalysis(
                fixture=fixture,
                model_input=None,
                model_output=None,
                reviewed=[],
                result=FixtureAnalysisResult(
                    fixture_id=fixture.id, message=INSUFFICIENT_DATA_MESSAGE
                ),
            )

        output = await self._model.analyze(model_input)
        reviewed = [
            ReviewedSelection(candidate=c, decision=self._evaluate(c)) for c in output.candidates
        ]
        selections = [
            self._to_selection_analysis(rs, confidence_killer=output.confidence_killer)
            for rs in reviewed
        ]

        probabilities = {
            result.value: prob.value for result, prob in output.outcome_probabilities.items()
        }
        message: str | None = None
        if not model_input.quotes:
            message = NO_ODDS_MESSAGE
        elif not any(s.recommended for s in selections):
            message = NO_VALUE_MESSAGE

        result = FixtureAnalysisResult(
            fixture_id=fixture.id,
            probabilities=probabilities,
            expected_goals_home=output.expected_goals.home if output.expected_goals else None,
            expected_goals_away=output.expected_goals.away if output.expected_goals else None,
            selections=selections,
            data_completeness=model_input.data_completeness.value,
            message=message,
            confidence_killer=output.confidence_killer,
        )
        return DetailedAnalysis(
            fixture=fixture,
            model_input=model_input,
            model_output=output,
            reviewed=reviewed,
            result=result,
        )

    def _evaluate(self, candidate: ModelCandidate) -> GateDecision:
        return self._gate.evaluate(
            GateInput(
                decision_score=candidate.decision_score,
                expected_value=candidate.edge.edge,
                data_completeness=candidate.data_completeness,
                evidence_level=candidate.evidence_level,
                risk_level=candidate.risk_level,
            )
        )

    def _to_selection_analysis(
        self, reviewed: ReviewedSelection, *, confidence_killer: str | None = None
    ) -> SelectionAnalysis:
        candidate = reviewed.candidate
        decision = reviewed.decision
        stake = candidate.stake
        kelly_fraction = stake.fraction_of_bankroll if stake is not None else 0.0
        kelly_stake = float(stake.amount.amount) if stake is not None else 0.0
        currency = stake.amount.currency if stake is not None else "EUR"
        model_prob = candidate.model_probability.value
        implied = candidate.odds.implied_probability.value
        confidence = candidate.decision_score.value / 100.0

        explanation = (
            f"模型概率 {model_prob:.1%}，市场隐含 {implied:.1%}（赔率 "
            f"{float(candidate.odds.decimal):.2f}）；edge {candidate.edge.edge:+.3f}，"
            f"EV {candidate.edge.expected_value_per_unit:+.3f}/单位；"
            f"Kelly {kelly_fraction:.1%}。准入：{'；'.join(decision.reasons)}"
        )
        return SelectionAnalysis(
            code=candidate.selection.code,
            selection_label=candidate.selection.label,
            decimal_odds=float(candidate.odds.decimal),
            model_probability=model_prob,
            implied_probability=implied,
            edge=candidate.edge.edge,
            expected_value=candidate.edge.expected_value_per_unit,
            kelly_fraction=kelly_fraction,
            kelly_stake=kelly_stake,
            currency=currency,
            recommended=decision.approved,
            confidence=confidence,
            reasons=list(decision.reasons),
            explanation=explanation,
            confidence_killer=confidence_killer,
        )
