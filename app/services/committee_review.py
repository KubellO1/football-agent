"""AI 评审委员会编排（工作流第 5 步：LLM Review → Final Decision）。

在**不改动**确定性数学管线的前提下，叠加一层专家评审：

    FixtureAnalysisService（Poisson/Elo/EV/Kelly + gate，权威）
        → 组装证据包 → CommitteeReviewer（LLM，仅解释与批判）
        → 落库：被 gate 批准的推荐写入 ValueBet；整场评审写入 DecisionLog

红线：LLM 不得改动任何数值，也不能否决 gate。是否落 ValueBet 完全由确定性
gate 决定；LLM 的不同意见（disagreements）只作留痕，写进 DecisionLog。
可复现性：DecisionLog 存档模型版本、提示词版本、完整输入证据与结构化评审产出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.entities.decision_log import DecisionLog
from app.models.entities.value_bet import ValueBet
from app.prompts.committee_review import PROMPT_VERSION
from app.schemas.committee_review import (
    CommitteeReviewContext,
    MarketMovementContext,
    SelectionContext,
    TeamFormContext,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.agents.interfaces import CommitteeReviewer
    from app.models.entities.fixture import Fixture
    from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
    from app.repositories.interfaces.value_bet_repository import ValueBetRepository
    from app.schemas.committee_review import CommitteeReview
    from app.services.fixture_analysis import (
        DetailedAnalysis,
        FixtureAnalysisResult,
        FixtureAnalysisService,
        ReviewedSelection,
    )
    from app.services.verified_market_movement import VerifiedMarketMovementService

logger = get_logger(__name__)

EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "committee-review-context/v1"


@dataclass(frozen=True, slots=True)
class CommitteeReviewResult:
    """评审 + 落库的最终产出。"""

    fixture_id: UUID
    analysis: FixtureAnalysisResult
    review: CommitteeReview | None = None
    decision_log_id: UUID | None = None
    value_bet_ids: list[UUID] = field(default_factory=list)
    message: str | None = None


class CommitteeReviewService:
    """把确定性分析交给 LLM 评审并落库（数值不变，异议留痕）。"""

    def __init__(
        self,
        *,
        analysis: FixtureAnalysisService,
        reviewer: CommitteeReviewer,
        decision_logs: DecisionLogRepository,
        value_bets: ValueBetRepository,
        model_version: str,
        market_movements: VerifiedMarketMovementService | None = None,
        movement_lookback: timedelta = timedelta(hours=24),
    ) -> None:
        if movement_lookback <= timedelta(0):
            raise ValueError("movement_lookback must be positive")
        self._analysis = analysis
        self._reviewer = reviewer
        self._decision_logs = decision_logs
        self._value_bets = value_bets
        self._model_version = model_version
        self._market_movements = market_movements
        self._movement_lookback = movement_lookback

    async def review(
        self,
        fixture: Fixture,
        *,
        as_of: datetime | None = None,
    ) -> CommitteeReviewResult:
        """跑确定性分析后交给 LLM 评审并落库。"""
        detailed = await self._analysis.analyze_detailed(fixture, as_of=as_of)
        return await self.review_detailed(detailed)

    async def review_detailed(self, detailed: DetailedAnalysis) -> CommitteeReviewResult:
        """基于**已算好**的确定性分析做评审并落库（供每日批处理复用，避免重复计算）。"""
        fixture = detailed.fixture

        # 无法建模或没有候选（无赔率）→ 无可评审内容，不调用 LLM、不落库。
        if detailed.model_input is None or not detailed.reviewed:
            return CommitteeReviewResult(
                fixture_id=fixture.id,
                analysis=detailed.result,
                message=detailed.result.message,
            )

        context = await self._build_context(detailed)
        review = await self._reviewer.review(context)

        value_bet_ids = await self._persist_value_bets(fixture, detailed, review)
        decision_log = await self._persist_decision_log(fixture, detailed, context, review)

        logger.info(
            "Committee review for fixture %s: %d value bets, decision_log=%s",
            fixture.id,
            len(value_bet_ids),
            decision_log.id,
        )
        return CommitteeReviewResult(
            fixture_id=fixture.id,
            analysis=detailed.result,
            review=review,
            decision_log_id=decision_log.id,
            value_bet_ids=value_bet_ids,
        )

    # --- 证据包 ------------------------------------------------------------

    async def _build_context(self, detailed: DetailedAnalysis) -> CommitteeReviewContext:
        fixture = detailed.fixture
        model_input = detailed.model_input
        assert model_input is not None  # 调用点已保证
        result = detailed.result
        movement_contexts, movement_issues = await self._build_movement_context(detailed)

        def _form(side: str, stats) -> TeamFormContext:  # type: ignore[no-untyped-def]
            return TeamFormContext(
                side=side,
                matches_played=stats.matches_played,
                wins=stats.wins,
                draws=stats.draws,
                losses=stats.losses,
                goals_for=stats.goals_for,
                goals_against=stats.goals_against,
            )

        selections = [
            SelectionContext(
                selection_label=s.selection_label,
                decimal_odds=s.decimal_odds,
                model_probability=s.model_probability,
                implied_probability=s.implied_probability,
                edge=s.edge,
                expected_value=s.expected_value,
                kelly_fraction=s.kelly_fraction,
                kelly_stake=s.kelly_stake,
                currency=s.currency,
                recommended=s.recommended,
                model_confidence=s.confidence,
                gate_reasons=s.reasons,
            )
            for s in result.selections
        ]

        return CommitteeReviewContext(
            fixture_summary=f"主队({fixture.home_team_id}) vs 客队({fixture.away_team_id})",
            competition=str(fixture.competition_id),
            kickoff_iso=fixture.kickoff.isoformat(),
            probabilities=result.probabilities,
            expected_goals_home=result.expected_goals_home,
            expected_goals_away=result.expected_goals_away,
            elo_home=model_input.home_elo,
            elo_away=model_input.away_elo,
            league_baseline_rate=model_input.league.rate_per_team_match,
            league_baseline_metric=model_input.league.metric.value,
            home_form=_form("home", model_input.home_stats),
            away_form=_form("away", model_input.away_stats),
            selections=selections,
            market_movement_opening_as_of=(
                (detailed.analysis_as_of - self._movement_lookback).isoformat()
                if self._market_movements is not None
                else None
            ),
            market_movement_current_as_of=(
                detailed.analysis_as_of.isoformat() if self._market_movements is not None else None
            ),
            market_movements=movement_contexts,
            market_movement_issues=movement_issues,
        )

    async def _build_movement_context(
        self,
        detailed: DetailedAnalysis,
    ) -> tuple[list[MarketMovementContext], list[str]]:
        if self._market_movements is None:
            return [], []

        result = await self._market_movements.compare(
            detailed.fixture.id,
            opening_as_of=detailed.analysis_as_of - self._movement_lookback,
            current_as_of=detailed.analysis_as_of,
        )
        if not result.accepted:
            issues = [
                (
                    f"{issue.stage.value}:{issue.reason.value}"
                    if issue.selection_code is None
                    else f"{issue.stage.value}:{issue.reason.value}:{issue.selection_code}"
                )
                for issue in result.issues
            ]
            return [], issues

        contexts = [
            MarketMovementContext(
                selection_label=item.selection.label,
                opening_captured_at=item.opening_quote.captured_at.isoformat(),
                current_captured_at=item.current_quote.captured_at.isoformat(),
                opening_snapshot_ids=list(item.opening_quote.contributing_snapshot_ids),
                opening_bookmaker_ids=list(item.opening_quote.contributing_bookmaker_ids),
                current_snapshot_ids=list(item.current_quote.contributing_snapshot_ids),
                current_bookmaker_ids=list(item.current_quote.contributing_bookmaker_ids),
                opening_consensus_odds=float(item.movement.opening.decimal),
                current_consensus_odds=float(item.movement.current.decimal),
                decimal_delta=item.movement.decimal_delta,
                implied_probability_shift=item.movement.implied_probability_shift,
                direction=item.movement.direction.value,
                opening_snapshot_count=len(item.opening_quote.contributing_snapshot_ids),
                opening_bookmaker_count=len(item.opening_quote.contributing_bookmaker_ids),
                current_snapshot_count=len(item.current_quote.contributing_snapshot_ids),
                current_bookmaker_count=len(item.current_quote.contributing_bookmaker_ids),
            )
            for item in result.movements
        ]
        return contexts, []

    # --- 落库 --------------------------------------------------------------

    async def _persist_value_bets(
        self, fixture: Fixture, detailed: DetailedAnalysis, review: CommitteeReview
    ) -> list[UUID]:
        rationale_by_label = {sr.selection_label: sr.explanation for sr in review.selection_reviews}
        ids: list[UUID] = []
        for reviewed in detailed.reviewed:
            if not reviewed.decision.approved:  # 只落 gate 批准的推荐
                continue
            candidate = reviewed.candidate
            label = candidate.selection.label
            value_bet = ValueBet(
                fixture_id=fixture.id,
                selection=candidate.selection,
                odds=candidate.odds,
                bookmaker_id=candidate.bookmaker_id,
                model_probability=candidate.model_probability,
                edge=candidate.edge,
                stake=candidate.stake,
                # 数值来自模型（信心=综合评分/100）；文字理由来自 LLM。
                confidence=candidate.decision_score.value / 100.0,
                rationale=rationale_by_label.get(label, review.betting_recommendation_explanation),
            )
            saved = await self._value_bets.add(value_bet)
            ids.append(saved.id)
        return ids

    async def _persist_decision_log(
        self,
        fixture: Fixture,
        detailed: DetailedAnalysis,
        context: CommitteeReviewContext,
        review: CommitteeReview,
    ) -> DecisionLog:
        disagreements = self._collect_disagreements(detailed.reviewed, review)
        log = DecisionLog(
            fixture_id=fixture.id,
            summary=review.executive_summary,
            supporting_evidence=list(review.key_strengths),
            risks=list(review.key_risks),
            rejected_alternatives=[review.why_market_may_be_wrong, *disagreements],
            change_conditions=[],
            model_version=self._model_version,
            prompt_version=PROMPT_VERSION,
            review=review.model_dump(mode="json"),
            evidence_snapshot={
                "schema_version": EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
                "analysis_as_of": detailed.analysis_as_of.isoformat(),
                "context": context.model_dump(mode="json"),
            },
        )
        return await self._decision_logs.add(log)

    @staticmethod
    def _collect_disagreements(
        reviewed: list[ReviewedSelection], review: CommitteeReview
    ) -> list[str]:
        """合并 LLM 自述分歧与「评审立场 vs gate 结论」的确定性冲突（仅留痕）。"""
        notes = list(review.disagreements)
        by_label = {sr.selection_label: sr for sr in review.selection_reviews}
        for rs in reviewed:
            label = rs.candidate.selection.label
            sr = by_label.get(label)
            if sr is not None and not sr.agrees_with_model:
                gate = "推荐" if rs.decision.approved else "不推荐"
                notes.append(f"{label}：评审不认同 gate（立场={sr.stance.value}，gate={gate}）")
        return notes
