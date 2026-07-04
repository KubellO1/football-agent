"""回测框架：用历史比赛验证既有确定性预测管线（不新增算法、不评审、不落库）。

复用 FixtureAnalysisService（Poisson/Elo/gate/EV/Kelly/信心）。唯一新增：
- ``BacktestInputBuilder``：按每场「赛前」时点（kickoff 之前）汇总近况与联赛基准，
  避免未来信息泄漏；
- 纯函数 ``compute_stats``：把逐场结果汇总为准确率/ROI/回撤等统计（可单测）。

结果全程在内存计算，导出 CSV/Markdown；不创建 ValueBet/DecisionLog、不调用 Claude。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean
from uuid import UUID

from app.models.entities.fixture import Fixture
from app.models.value_objects.decision import EvidenceLevel
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.modeling import ModelInput
from app.services.models.poisson import PoissonModel

_OU_LINE = 2.5


class BacktestInputBuilder(MatchAnalysisInputBuilder):
    """点时（point-in-time）输入构造器：只用 kickoff 之前的比赛。"""

    async def build(self, fixture: Fixture) -> ModelInput | None:
        as_of = fixture.kickoff
        home_stats = await self._team_stats(fixture.home_team_id, exclude=fixture.id, before=as_of)
        away_stats = await self._team_stats(fixture.away_team_id, exclude=fixture.id, before=as_of)
        league = await self._league_averages(fixture.competition_id, before=as_of)
        if home_stats.matches_played == 0 or away_stats.matches_played == 0 or league is None:
            return None
        quotes = await self._quotes(fixture.id)
        home_elo, away_elo = await self._elos(fixture.home_team_id, fixture.away_team_id)
        completeness = self._completeness(home_stats, away_stats, quotes, home_elo, away_elo)
        return ModelInput(
            fixture=fixture,
            home_stats=home_stats,
            away_stats=away_stats,
            league=league,
            quotes=quotes,
            bankroll=self._bankroll,
            data_completeness=completeness,
            evidence_level=EvidenceLevel.B,
            home_elo=home_elo,
            away_elo=away_elo,
        )


@dataclass(frozen=True, slots=True)
class BetPlaced:
    code: str
    odds: float
    ev: float
    kelly_fraction: float
    confidence: float
    won: bool


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    fixture_id: UUID
    kickoff: datetime
    competition_id: UUID
    home_team_id: UUID
    away_team_id: UUID
    predicted: str
    actual: str
    p_home: float
    p_draw: float
    p_away: float
    total_goals: int
    over_pred: bool | None
    over_actual: bool
    bet: BetPlaced | None


@dataclass
class BacktestStats:
    fixtures_evaluated: int = 0
    fixtures_skipped: int = 0
    winner_accuracy: float = 0.0
    draw_precision: float = 0.0
    draw_recall: float = 0.0
    over_under_accuracy: float | None = None
    bets_placed: int = 0
    avg_ev: float = 0.0
    avg_kelly: float = 0.0
    avg_confidence: float = 0.0
    win_rate: float = 0.0
    flat_roi: float = 0.0
    kelly_roi: float = 0.0
    max_drawdown: float = 0.0  # flat 权益曲线的最大回撤（单位数）
    kelly_max_drawdown: float = 0.0  # Kelly 资金曲线的最大回撤（占峰值比例）
    flat_curve: list[float] = field(default_factory=list)
    kelly_curve: list[float] = field(default_factory=list)


def _max_drawdown(curve: list[float], *, start: float = 0.0) -> float:
    peak = start
    dd = 0.0
    for value in curve:
        peak = max(peak, value)
        dd = max(dd, peak - value)
    return dd


def _max_drawdown_fraction(curve: list[float], start: float) -> float:
    peak = start
    dd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            dd = max(dd, (peak - value) / peak)
    return dd


def compute_stats(
    outcomes: list[MatchOutcome], *, fixtures_skipped: int = 0, bankroll_start: float = 1.0
) -> BacktestStats:
    """把逐场结果汇总为回测统计（纯函数，便于单测）。"""
    evaluated = len(outcomes)
    if evaluated == 0:
        return BacktestStats(fixtures_evaluated=0, fixtures_skipped=fixtures_skipped)

    winner_correct = sum(1 for o in outcomes if o.predicted == o.actual)
    pred_draw = [o for o in outcomes if o.predicted == "draw"]
    act_draw = [o for o in outcomes if o.actual == "draw"]
    tp_draw = sum(1 for o in pred_draw if o.actual == "draw")
    ou = [o for o in outcomes if o.over_pred is not None]

    bets = [o.bet for o in outcomes if o.bet is not None]
    n = len(bets)

    flat_curve: list[float] = []
    cum = 0.0
    for b in bets:
        cum += (b.odds - 1.0) if b.won else -1.0
        flat_curve.append(cum)

    kelly_curve: list[float] = []
    bankroll = bankroll_start
    for b in bets:
        stake = b.kelly_fraction * bankroll
        bankroll += stake * (b.odds - 1.0) if b.won else -stake
        kelly_curve.append(bankroll)

    return BacktestStats(
        fixtures_evaluated=evaluated,
        fixtures_skipped=fixtures_skipped,
        winner_accuracy=winner_correct / evaluated,
        draw_precision=(tp_draw / len(pred_draw)) if pred_draw else 0.0,
        draw_recall=(tp_draw / len(act_draw)) if act_draw else 0.0,
        over_under_accuracy=(
            sum(1 for o in ou if o.over_pred == o.over_actual) / len(ou) if ou else None
        ),
        bets_placed=n,
        avg_ev=mean(b.ev for b in bets) if bets else 0.0,
        avg_kelly=mean(b.kelly_fraction for b in bets) if bets else 0.0,
        avg_confidence=mean(b.confidence for b in bets) if bets else 0.0,
        win_rate=(sum(1 for b in bets if b.won) / n) if n else 0.0,
        flat_roi=(cum / n) if n else 0.0,
        kelly_roi=((bankroll - bankroll_start) / bankroll_start) if n else 0.0,
        max_drawdown=_max_drawdown(flat_curve),
        kelly_max_drawdown=_max_drawdown_fraction(kelly_curve, bankroll_start),
        flat_curve=flat_curve,
        kelly_curve=kelly_curve,
    )


class BacktestService:
    """回放历史比赛：对每场做点时分析，与真实结果比对，产出统计。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        analysis: FixtureAnalysisService,
        poisson: PoissonModel | None = None,
    ) -> None:
        self._fixtures = fixtures
        self._analysis = analysis
        self._poisson = poisson or PoissonModel()

    async def run(
        self,
        *,
        competition_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[BacktestStats, list[MatchOutcome]]:
        fixtures = await self._fixtures.list_finished(
            competition_id=competition_id, start=start, end=end
        )
        outcomes: list[MatchOutcome] = []
        skipped = 0
        for fixture in fixtures:
            if fixture.score is None:
                skipped += 1
                continue
            try:
                detailed = await self._analysis.analyze_detailed(fixture)
            except ValueError:
                skipped += 1  # 退化的赛前数据（如 λ≤0），无法建模
                continue
            if detailed.model_input is None:
                skipped += 1  # 赛前历史不足，无法建模
                continue

            probs = detailed.result.probabilities
            predicted = max(probs, key=lambda k: probs[k])
            actual = fixture.score.result.value

            over_pred: bool | None = None
            output = detailed.model_output
            if output is not None and output.expected_goals is not None:
                over_prob = self._poisson.over_under(
                    output.expected_goals.home, output.expected_goals.away, _OU_LINE
                )[0].value
                over_pred = over_prob > 0.5

            recommended = [s for s in detailed.result.selections if s.recommended]
            bet: BetPlaced | None = None
            if recommended:
                best = max(recommended, key=lambda s: s.expected_value)
                bet = BetPlaced(
                    code=best.code,
                    odds=best.decimal_odds,
                    ev=best.expected_value,
                    kelly_fraction=best.kelly_fraction,
                    confidence=best.confidence,
                    won=best.code == actual,
                )

            outcomes.append(
                MatchOutcome(
                    fixture_id=fixture.id,
                    kickoff=fixture.kickoff,
                    competition_id=fixture.competition_id,
                    home_team_id=fixture.home_team_id,
                    away_team_id=fixture.away_team_id,
                    predicted=predicted,
                    actual=actual,
                    p_home=probs.get("home", 0.0),
                    p_draw=probs.get("draw", 0.0),
                    p_away=probs.get("away", 0.0),
                    total_goals=fixture.score.total_goals,
                    over_pred=over_pred,
                    over_actual=fixture.score.total_goals > _OU_LINE,
                    bet=bet,
                )
            )

        return compute_stats(outcomes, fixtures_skipped=skipped), outcomes


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

_CSV_HEADER = [
    "fixture_id",
    "kickoff",
    "competition_id",
    "home_team_id",
    "away_team_id",
    "predicted",
    "actual",
    "correct",
    "p_home",
    "p_draw",
    "p_away",
    "over_pred",
    "over_actual",
    "bet_code",
    "bet_odds",
    "bet_ev",
    "bet_kelly",
    "bet_confidence",
    "bet_won",
    "flat_cum_profit",
    "kelly_bankroll",
]


def write_csv(path: str, outcomes: list[MatchOutcome], *, bankroll_start: float = 1.0) -> None:
    flat = 0.0
    bank = bankroll_start
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        for o in outcomes:
            if o.bet is not None:
                flat += (o.bet.odds - 1.0) if o.bet.won else -1.0
                stake = o.bet.kelly_fraction * bank
                bank += stake * (o.bet.odds - 1.0) if o.bet.won else -stake
            b = o.bet
            writer.writerow(
                [
                    o.fixture_id,
                    o.kickoff.isoformat(),
                    o.competition_id,
                    o.home_team_id,
                    o.away_team_id,
                    o.predicted,
                    o.actual,
                    int(o.predicted == o.actual),
                    f"{o.p_home:.4f}",
                    f"{o.p_draw:.4f}",
                    f"{o.p_away:.4f}",
                    "" if o.over_pred is None else int(o.over_pred),
                    int(o.over_actual),
                    b.code if b else "",
                    f"{b.odds:.3f}" if b else "",
                    f"{b.ev:.4f}" if b else "",
                    f"{b.kelly_fraction:.4f}" if b else "",
                    f"{b.confidence:.4f}" if b else "",
                    int(b.won) if b else "",
                    f"{flat:.4f}",
                    f"{bank:.4f}",
                ]
            )


def render_markdown(stats: BacktestStats, *, title: str = "Backtest report") -> str:
    ou = "n/a" if stats.over_under_accuracy is None else f"{stats.over_under_accuracy:.1%}"
    lines = [
        f"# {title}",
        "",
        "## Prediction accuracy",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fixtures evaluated | {stats.fixtures_evaluated} |",
        f"| Fixtures skipped (insufficient history) | {stats.fixtures_skipped} |",
        f"| Match winner accuracy | {stats.winner_accuracy:.1%} |",
        f"| Draw precision | {stats.draw_precision:.1%} |",
        f"| Draw recall | {stats.draw_recall:.1%} |",
        f"| Over/Under 2.5 accuracy | {ou} |",
        "",
        "## Betting (fixtures with odds that passed the gate)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Gate passes (bets placed) | {stats.bets_placed} |",
        f"| Average EV | {stats.avg_ev:+.3f} |",
        f"| Average Kelly | {stats.avg_kelly:.3f} |",
        f"| Average confidence | {stats.avg_confidence:.3f} |",
        f"| Win rate | {stats.win_rate:.1%} |",
        f"| ROI (flat stake) | {stats.flat_roi:+.1%} |",
        f"| ROI (Kelly staking) | {stats.kelly_roi:+.1%} |",
        f"| Max drawdown (flat, units) | {stats.max_drawdown:.2f} |",
        f"| Max drawdown (Kelly, % of peak) | {stats.kelly_max_drawdown:.1%} |",
        "",
    ]
    return "\n".join(lines)


def write_markdown(path: str, stats: BacktestStats, *, title: str = "Backtest report") -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(stats, title=title))
