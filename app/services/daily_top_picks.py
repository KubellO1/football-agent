"""每日 Top Picks：成本可控的当日推荐编排。

对当日**所有**比赛跑确定性数学分析（便宜、无 LLM、无外部 API），用准入 gate +
阈值预筛，按 EV 排序取前 N（默认 5），**仅**这最多 5 场进入 LLM 评审并落库。
读取推荐（DailyRecommendationsReader）纯读库，绝不触发 LLM。

每场比赛分析完成后，自动通过 PredictionLogger 将全部 selection 的决策数据
写入 predictions 表（BET/WATCH/NO_BET），确保 predictions 表始终是完整决策记录。

成本护栏：已有 DecisionLog 的比赛视为「已评审」，重复运行直接跳过，不再花费 LLM。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.config.whitelist import get_whitelist
from app.core.logging import get_logger
from app.services.prediction_logger import log_fixture_predictions

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.value_bet import ValueBet
    from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.reference import CompetitionRepository, TeamRepository
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
    fixtures_skipped_unsupported_competition: int = 0
    predictions_logged: int = 0  # predictions 表写入行数
    reviewed_fixture_ids: list[UUID] = field(default_factory=list)


def _day_window(on_date: date) -> tuple[datetime, datetime]:
    start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


class DailyTopPicksService:
    """跑当日全部比赛的数学分析，筛选后仅对 Top-N 调用 LLM 评审。

    每场比赛分析完成后自动调用 PredictionLogger 将决策数据写入 predictions 表。
    """

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        analysis: FixtureAnalysisService,
        review: CommitteeReviewService,
        decision_logs: DecisionLogRepository,
        teams: TeamRepository,
        competitions: CompetitionRepository,
        session: Any,  # AsyncSession for PredictionLogger
        min_ev: float,
        min_kelly: float,
        min_confidence: float,
        max_picks: int,
        model_version: str = "",
    ) -> None:
        self._fixtures = fixtures
        self._analysis = analysis
        self._review = review
        self._decision_logs = decision_logs
        self._teams = teams
        self._competitions = competitions
        self._session = session
        self._min_ev = min_ev
        self._min_kelly = min_kelly
        self._min_confidence = min_confidence
        self._max_picks = max_picks
        self._model_version = model_version

    def _qualifies(self, selection: SelectionAnalysis) -> bool:
        """gate 已批准，且 EV / Kelly / 信心均达到阈值（值得花 LLM 评审）。"""
        return (
            selection.recommended
            and selection.expected_value >= self._min_ev
            and selection.kelly_fraction >= self._min_kelly
            and selection.confidence >= self._min_confidence
        )

    async def _resolve_team_name(self, team_id: UUID) -> str:
        """按 ID 解析球队名称，失败时返回 ID 字符串表示。"""
        try:
            team = await self._teams.get(team_id)
            return team.name if team else str(team_id)
        except Exception:
            logger.warning("Failed to resolve team name for %s", team_id)
            return str(team_id)

    async def _resolve_competition_name(self, competition_id: UUID) -> str:
        """按 ID 解析赛事名称，失败时返回 ID 字符串表示。"""
        try:
            comp = await self._competitions.get(competition_id)
            return comp.name if comp else str(competition_id)
        except Exception:
            logger.warning("Failed to resolve competition name for %s", competition_id)
            return str(competition_id)

    async def run(self, on_date: date) -> DailyRunReport:
        start, end = _day_window(on_date)
        analysis_as_of = datetime.now(UTC)
        fixtures = await self._fixtures.list_by_kickoff_window(start, end)

        # --- Production whitelist: load once per run ---
        whitelist = get_whitelist()

        # 对所有比赛跑确定性数学分析；记录有合格候选的比赛及其最佳 EV。
        scored: list[tuple[UUID, float]] = []
        detailed_by_id: dict[UUID, Any] = {}
        predictions_logged = 0
        skipped_unsupported = 0

        for fixture in fixtures:
            # Check competition against production whitelist
            competition_name = await self._resolve_competition_name(fixture.competition_id)
            # Resolve league_id + country for exact-match whitelist lookup
            try:
                comp = await self._competitions.get(fixture.competition_id)
                league_id: int | None = None
                country = comp.country if comp else None
                if comp and comp.external_id:
                    with suppress(ValueError, TypeError):
                        league_id = int(comp.external_id)
            except Exception:
                league_id = None
                country = None
            if not whitelist.is_allowed(competition_name, league_id=league_id, country=country):
                logger.info(
                    "SKIPPED_UNSUPPORTED_COMPETITION fixture=%s competition=%s",
                    fixture.id,
                    competition_name,
                )
                skipped_unsupported += 1
                continue

            detailed = await self._analysis.analyze_detailed(
                fixture,
                as_of=analysis_as_of,
            )

            # --- PredictionLogger: 每场比赛分析后立即写入 predictions 表 ---
            try:
                # 解析比赛/球队名称（competition_name 已在上面解析）
                home_name = await self._resolve_team_name(fixture.home_team_id)
                away_name = await self._resolve_team_name(fixture.away_team_id)
                pred_report = await log_fixture_predictions(
                    detailed,
                    session=self._session,
                    competition_name=competition_name,
                    home_team_name=home_name,
                    away_team_name=away_name,
                    model_version=self._model_version,
                )
                predictions_logged += pred_report.inserted
            except Exception:
                logger.exception("PredictionLogger failed for fixture %s (non-fatal)", fixture.id)
            # --- /PredictionLogger ---

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
            # 成本护栏：已评审过（存在 DecisionLog）则跳过，不再调用 LLM。
            if await self._decision_logs.list_by_fixture(fixture_id):
                skipped += 1
                continue
            result = await self._review.review_detailed(detailed_by_id[fixture_id])
            reviewed += 1
            reviewed_ids.append(fixture_id)
            value_bets += len(result.value_bet_ids)

        logger.info(
            "Daily picks %s: analyzed=%d qualified=%d reviewed=%d skipped=%d "
            "skipped_unsupported=%d value_bets=%d predictions_logged=%d",
            on_date.isoformat(),
            len(fixtures),
            len(scored),
            reviewed,
            skipped,
            skipped_unsupported,
            value_bets,
            predictions_logged,
        )
        return DailyRunReport(
            date=on_date.isoformat(),
            fixtures_analyzed=len(fixtures),
            fixtures_qualified=len(scored),
            fixtures_reviewed=reviewed,
            fixtures_skipped_existing=skipped,
            fixtures_skipped_unsupported_competition=skipped_unsupported,
            value_bets_created=value_bets,
            predictions_logged=predictions_logged,
            reviewed_fixture_ids=reviewed_ids,
        )


# ---------------------------------------------------------------------------
# 读取（纯读库，绝不调用 LLM）
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
