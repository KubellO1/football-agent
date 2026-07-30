"""回测框架：用历史比赛验证既有确定性预测管线（不新增算法、不评审、不落库）。

复用 FixtureAnalysisService（Poisson/Elo/gate/EV/Kelly/信心）。唯一新增：
- ``BacktestInputBuilder``：按每场「赛前」时点（kickoff 之前）汇总近况与联赛基准，
  避免未来信息泄漏；
- 纯函数 ``compute_stats``：把逐场结果汇总为准确率/ROI/回撤等统计（可单测）。

结果全程在内存计算，导出 CSV/Markdown；不创建 ValueBet/DecisionLog、不调用 LLM。
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median, pstdev
from typing import TYPE_CHECKING

from app.models.value_objects.decision import EvidenceLevel
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.modeling import ModelInput
from app.services.models.poisson import PoissonModel

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository

_OU_LINE = 2.5
_CLASSES = ("home", "draw", "away")
_LOG_EPS = 1e-15
# 分桶边界（左闭右开，最后一桶右端为 +inf）。
_CONFIDENCE_EDGES = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, float("inf")]
_ODDS_EDGES = [1.0, 1.5, 2.0, 3.0, 5.0, float("inf")]


class BacktestInputBuilder(MatchAnalysisInputBuilder):
    """点时（point-in-time）输入构造器：只用 kickoff 之前的比赛。"""

    async def build(self, fixture: Fixture, *, as_of: datetime) -> ModelInput | None:
        self._validate_as_of(as_of)
        home_stats = await self._team_stats(fixture.home_team_id, exclude=fixture.id, before=as_of)
        away_stats = await self._team_stats(fixture.away_team_id, exclude=fixture.id, before=as_of)
        league = await self._league_averages(fixture.competition_id, before=as_of)
        if home_stats.matches_played == 0 or away_stats.matches_played == 0 or league is None:
            return None
        quotes, quote_issues = await self._quotes(fixture.id, as_of=as_of)
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
            quote_issues=quote_issues,
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
    closing_odds: float | None = None  # 收盘赔率（若有赛前赔率时序），用于 CLV


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """校准（可靠性）曲线的一个分箱：预测概率落入 [lo, hi) 的样本聚合。"""

    lo: float
    hi: float
    count: int
    avg_predicted: float  # 该箱内平均预测概率
    observed_freq: float  # 该箱内实际发生频率


@dataclass(frozen=True, slots=True)
class BucketStat:
    """按信心/赔率分桶后的下注盈亏统计。"""

    label: str
    bets: int
    profit_units: float  # flat 单位（每注 1 单位）累计盈亏
    roi: float  # profit_units / bets
    win_rate: float


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
    has_odds: bool = False  # 该场是否有赛前赔率喂入模型（用于统计赔率覆盖/未匹配率）


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
    # --- 概率校准（无需赔率，对每场评估都可算）---
    brier_score: float | None = None  # 多分类 Brier（越低越好，0..2）
    log_loss: float | None = None  # 多分类对数损失（越低越好）
    calibration: list[CalibrationBin] = field(default_factory=list)
    # --- 风险/收益（需赔率）---
    sharpe_ratio: float | None = None  # 每注 flat 收益的夏普（mean/std）
    clv: float | None = None  # 平均收盘线价值（需赛前赔率时序，否则 None）
    confidence_buckets: list[BucketStat] = field(default_factory=list)
    odds_buckets: list[BucketStat] = field(default_factory=list)
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


def _probs(o: MatchOutcome) -> dict[str, float]:
    return {"home": o.p_home, "draw": o.p_draw, "away": o.p_away}


def _brier_score(outcomes: list[MatchOutcome]) -> float:
    """多分类 Brier：每场 sum_k (p_k - y_k)^2 的均值（y 为实际结果的 one-hot）。"""
    total = 0.0
    for o in outcomes:
        p = _probs(o)
        total += sum((p[c] - (1.0 if o.actual == c else 0.0)) ** 2 for c in _CLASSES)
    return total / len(outcomes)


def _log_loss(outcomes: list[MatchOutcome]) -> float:
    """多分类对数损失：-mean(ln p_actual)，对概率做 eps 截断避免 log(0)。"""
    total = 0.0
    for o in outcomes:
        p_actual = _probs(o).get(o.actual, 0.0)
        total += -math.log(min(max(p_actual, _LOG_EPS), 1.0))
    return total / len(outcomes)


def _calibration(outcomes: list[MatchOutcome], *, bins: int = 10) -> list[CalibrationBin]:
    """三分类合并的可靠性曲线：把每场每一类的 (预测概率, 是否发生) 汇入 10 个分箱。"""
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for o in outcomes:
        p = _probs(o)
        for c in _CLASSES:
            prob = p[c]
            idx = min(int(prob * bins), bins - 1)  # prob==1.0 归入最后一箱
            buckets[idx].append((prob, 1.0 if o.actual == c else 0.0))
    result: list[CalibrationBin] = []
    for i, pairs in enumerate(buckets):
        if not pairs:
            continue
        result.append(
            CalibrationBin(
                lo=i / bins,
                hi=(i + 1) / bins,
                count=len(pairs),
                avg_predicted=mean(pr for pr, _ in pairs),
                observed_freq=mean(y for _, y in pairs),
            )
        )
    return result


def _sharpe(bets: list[BetPlaced]) -> float | None:
    """每注 flat 收益（赢 odds-1，输 -1）的夏普比 mean/std；样本不足或零方差返回 None。"""
    if len(bets) < 2:
        return None
    returns = [(b.odds - 1.0) if b.won else -1.0 for b in bets]
    sd = pstdev(returns)
    return (mean(returns) / sd) if sd > 0 else None


def _clv(bets: list[BetPlaced]) -> float | None:
    """平均收盘线价值：mean(下注赔率 / 收盘赔率 - 1)，仅在有收盘赔率的注上计算。"""
    priced = [b for b in bets if b.closing_odds is not None and b.closing_odds > 0]
    if not priced:
        return None
    return mean(b.odds / b.closing_odds - 1.0 for b in priced)  # type: ignore[operator]


def _bucketize(
    bets: list[BetPlaced], key: Callable[[BetPlaced], float], edges: list[float], *, pct: bool
) -> list[BucketStat]:
    """按 key(bet) 落入 edges 定义的桶，聚合 flat 盈亏/ROI/胜率。"""
    groups: list[list[BetPlaced]] = [[] for _ in range(len(edges) - 1)]
    for b in bets:
        v = key(b)
        for i in range(len(edges) - 1):
            if edges[i] <= v < edges[i + 1]:
                groups[i].append(b)
                break
    stats: list[BucketStat] = []
    for i, group in enumerate(groups):
        if not group:
            continue
        lo, hi = edges[i], edges[i + 1]
        if pct:
            label = f"{lo:.0%}-{hi:.0%}" if hi != float("inf") else f"{lo:.0%}+"
        else:
            label = f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+"
        profit = sum((b.odds - 1.0) if b.won else -1.0 for b in group)
        wins = sum(1 for b in group if b.won)
        stats.append(
            BucketStat(
                label=label,
                bets=len(group),
                profit_units=profit,
                roi=profit / len(group),
                win_rate=wins / len(group),
            )
        )
    return stats


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
        brier_score=_brier_score(outcomes),
        log_loss=_log_loss(outcomes),
        calibration=_calibration(outcomes),
        sharpe_ratio=_sharpe(bets),
        clv=_clv(bets),
        confidence_buckets=_bucketize(bets, lambda b: b.confidence, _CONFIDENCE_EDGES, pct=True),
        odds_buckets=_bucketize(bets, lambda b: b.odds, _ODDS_EDGES, pct=False),
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
        odds_snapshots: OddsSnapshotRepository | None = None,
    ) -> None:
        self._fixtures = fixtures
        self._analysis = analysis
        self._poisson = poisson or PoissonModel()
        # 可选：用于 CLV（需赛前赔率时序）。缺省时 CLV 报 n/a。
        self._snapshots = odds_snapshots

    async def _closing_odds(self, fixture: Fixture, code: str) -> float | None:
        """某选项的收盘赔率：赛前赔率快照按时间取最晚一批的中位数；不足两个时点则 None。"""
        if self._snapshots is None:
            return None
        as_of = fixture.kickoff - timedelta(microseconds=1)
        snaps = [
            snapshot
            for snapshot in await self._snapshots.list_by_fixture(fixture.id, as_of=as_of)
            if snapshot.selection.code == code
        ]
        times = {s.captured_at for s in snaps}
        if len(times) < 2:  # 只有单一时点 → 无「开盘→收盘」时序，无法算 CLV
            return None
        latest = max(times)
        closing = [float(s.odds.decimal) for s in snaps if s.captured_at == latest]
        return median(closing) if closing else None

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
                detailed = await self._analysis.analyze_detailed(
                    fixture,
                    as_of=fixture.kickoff - timedelta(microseconds=1),
                )
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
                    closing_odds=await self._closing_odds(fixture, best.code),
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
                    has_odds=bool(detailed.model_input.quotes),
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
        f"| Sharpe ratio (per bet) | {_fmt(stats.sharpe_ratio, '{:+.3f}')} |",
        f"| Closing line value | {_fmt(stats.clv, '{:+.2%}')} |",
        f"| Max drawdown (flat, units) | {stats.max_drawdown:.2f} |",
        f"| Max drawdown (Kelly, % of peak) | {stats.kelly_max_drawdown:.1%} |",
        "",
        "## Probability calibration (all evaluated fixtures, no odds needed)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Brier score (multiclass, lower=better) | {_fmt(stats.brier_score, '{:.4f}')} |",
        f"| Log loss (multiclass, lower=better) | {_fmt(stats.log_loss, '{:.4f}')} |",
        "",
        "| Predicted prob bin | N | Avg predicted | Observed freq |",
        "|---|---|---|---|",
        *(
            f"| {b.lo:.0%}-{b.hi:.0%} | {b.count} | {b.avg_predicted:.1%} | {b.observed_freq:.1%} |"
            for b in stats.calibration
        ),
        "",
        "## Profit by confidence bucket",
        "",
        *_bucket_table(stats.confidence_buckets),
        "",
        "## Profit by odds bucket",
        "",
        *_bucket_table(stats.odds_buckets),
        "",
    ]
    return "\n".join(lines)


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else spec.format(value)


def _bucket_table(buckets: list[BucketStat]) -> list[str]:
    if not buckets:
        return ["_(no bets)_"]
    rows = ["| Bucket | Bets | Profit (units) | ROI | Win rate |", "|---|---|---|---|---|"]
    rows += [
        f"| {b.label} | {b.bets} | {b.profit_units:+.2f} | {b.roi:+.1%} | {b.win_rate:.1%} |"
        for b in buckets
    ]
    return rows


def write_markdown(path: str, stats: BacktestStats, *, title: str = "Backtest report") -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(stats, title=title))
