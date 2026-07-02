"""每日 Top Picks：成本可控的当日推荐编排。

对当日**所有**比赛跑确定性数学分析（便宜、无 Claude、无外部 API），用准入 gate +
阈值预筛，按 EV 排序取前 N（默认 5），**仅**这最多 5 场进入 Claude 评审并落库。
读取推荐（DailyRecommendationsReader）纯读库，绝不触发 Claude。

成本护栏：已有 DecisionLog 的比赛视为「已评审」，重复运行直接跳过，不再花费 Claude。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.models.entities.value_bet import ValueBet
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.services.committee_review import CommitteeReviewService
from app.services.fixture_analysis import FixtureAnalysisService, SelectionAnalysis

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailyRunReport:
    """一次每日批处理的结果统计。"""

    date: str
    fixtures_analyzed: int
    fixtures_qualified: int
    fixtures_reviewed: int
    fixtures_skipped_existing: int
    value_bets_created: int
    reviewed_fixture_ids: list[UUID] = field(default_factory=list)


def _day_window(on_date: date) -> tuple[datetime, datetime]:
    start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


class DailyTopPicksService:
    """跑当日全部比赛的数学分析，筛选后仅对 Top-N 调用 Claude 评审。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        analysis: FixtureAnalysisService,
        review: CommitteeReviewService,
        decision_logs: DecisionLogRepository,
        min_ev: float,
        min_kelly: float,
        min_confidence: float,
        max_picks: int,
    ) -> None:
        self._fixtures = fixtures
        self._analysis = analysis
        self._review = review
        self._decision_logs = decision_logs
        self._min_ev = min_ev
        self._min_kelly = min_kelly
        self._min_confidence = min_confidence
        self._max_picks = max_picks

    def _qualifies(self, selection: SelectionAnalysis) -> bool:
        """gate 已批准，且 EV / Kelly / 信心均达到阈值（值得花 Claude 评审）。"""
        return (
            selection.recommended
            and selection.expected_value >= self._min_ev
            and selection.kelly_fraction >= self._min_kelly
            and selection.confidence >= self._min_confidence
        )

    async def run(self, on_date: date) -> DailyRunReport:
        start, end = _day_window(on_date)
        fixtures = await self._fixtures.list_by_kickoff_window(start, end)

        # 对所有比赛跑确定性数学分析；记录有合格候选的比赛及其最佳 EV。
        scored: list[tuple[UUID, float]] = []
        detailed_by_id = {}
        for fixture in fixtures:
            detailed = await self._analysis.analyze_detailed(fixture)
            qualifying = [s for s in detailed.result.selections if self._qualifies(s)]
            if qualifying:
                best_ev = max(s.expected_value for s in qualifying)
                scored.append((fixture.id, best_ev))
                detailed_by_id[fixture.id] = detailed

        # 按最佳 EV 降序取前 N。
        scored.sort(key=lambda item: item[1], reverse=True)
        top = scored[: self._max_picks]

        reviewed = 0
        skipped = 0
        value_bets = 0
        reviewed_ids: list[UUID] = []
        for fixture_id, _ in top:
            # 成本护栏：已评审过（存在 DecisionLog）则跳过，不再调用 Claude。
            if await self._decision_logs.list_by_fixture(fixture_id):
                skipped += 1
                continue
            result = await self._review.review_detailed(detailed_by_id[fixture_id])
            reviewed += 1
            reviewed_ids.append(fixture_id)
            value_bets += len(result.value_bet_ids)

        logger.info(
            "Daily picks %s: analyzed=%d qualified=%d reviewed=%d skipped=%d value_bets=%d",
            on_date.isoformat(),
            len(fixtures),
            len(scored),
            reviewed,
            skipped,
            value_bets,
        )
        return DailyRunReport(
            date=on_date.isoformat(),
            fixtures_analyzed=len(fixtures),
            fixtures_qualified=len(scored),
            fixtures_reviewed=reviewed,
            fixtures_skipped_existing=skipped,
            value_bets_created=value_bets,
            reviewed_fixture_ids=reviewed_ids,
        )


# ---------------------------------------------------------------------------
# 读取（纯读库，绝不调用 Claude）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecommendationBet:
    selection_label: str
    decimal_odds: float
    model_probability: float
    edge: float
    expected_value: float
    kelly_fraction: float
    kelly_stake: float
    currency: str
    confidence: float | None
    rationale: str | None


@dataclass(frozen=True, slots=True)
class FixtureRecommendation:
    fixture_id: UUID
    review_summary: str | None
    review: dict[str, Any] | None
    bets: list[RecommendationBet]


@dataclass(frozen=True, slots=True)
class RecommendationsView:
    date: str
    count: int
    recommendations: list[FixtureRecommendation]


class DailyRecommendationsReader:
    """读取当日已落库的推荐（ValueBet + 关联 DecisionLog）；纯读库。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        value_bets: ValueBetRepository,
        decision_logs: DecisionLogRepository,
    ) -> None:
        self._fixtures = fixtures
        self._value_bets = value_bets
        self._decision_logs = decision_logs

    async def read(self, on_date: date) -> RecommendationsView:
        start, end = _day_window(on_date)
        fixtures = await self._fixtures.list_by_kickoff_window(start, end)

        recommendations: list[FixtureRecommendation] = []
        for fixture in fixtures:
            bets = await self._value_bets.list_by_fixture(fixture.id)
            if not bets:
                continue
            logs = await self._decision_logs.list_by_fixture(fixture.id)
            log = logs[-1] if logs else None  # list_by_fixture 按 created_at 升序
            recommendations.append(
                FixtureRecommendation(
                    fixture_id=fixture.id,
                    review_summary=log.summary if log is not None else None,
                    review=log.review if log is not None else None,
                    bets=[_to_recommendation_bet(b) for b in bets],
                )
            )

        return RecommendationsView(
            date=on_date.isoformat(),
            count=len(recommendations),
            recommendations=recommendations,
        )


def _to_recommendation_bet(bet: ValueBet) -> RecommendationBet:
    stake = bet.stake
    return RecommendationBet(
        selection_label=bet.selection.label,
        decimal_odds=float(bet.odds.decimal),
        model_probability=bet.model_probability.value,
        edge=bet.edge.edge,
        expected_value=bet.edge.expected_value_per_unit,
        kelly_fraction=stake.fraction_of_bankroll if stake is not None else 0.0,
        kelly_stake=float(stake.amount.amount) if stake is not None else 0.0,
        currency=stake.amount.currency if stake is not None else "EUR",
        confidence=bet.confidence,
        rationale=bet.rationale,
    )
