"""AI 评审委员会编排（工作流第 5 步：Claude Review → Final Decision）。

在**不改动**确定性数学管线的前提下，叠加一层专家评审：

    FixtureAnalysisService（Poisson/Elo/EV/Kelly + gate，权威）
        → 组装证据包 → CommitteeReviewer（Claude，仅解释与批判）
        → 落库：被 gate 批准的推荐写入 ValueBet；整场评审写入 DecisionLog

红线：Claude 不得改动任何数值，也不能否决 gate。是否落 ValueBet 完全由确定性
gate 决定；Claude 的不同意见（disagreements）只作留痕，写进 DecisionLog。
可复现性：DecisionLog 存档模型版本、提示词版本与评审的完整结构化产出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.agents.interfaces import CommitteeReviewer
from app.core.logging import get_logger
from app.models.entities.decision_log import DecisionLog
from app.models.entities.fixture import Fixture
from app.models.entities.value_bet import ValueBet
from app.prompts.committee_review import PROMPT_VERSION
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.schemas.committee_review import (
    CommitteeReview,
    CommitteeReviewContext,
    SelectionContext,
    TeamFormContext,
)
from app.services.fixture_analysis import (
    DetailedAnalysis,
    FixtureAnalysisResult,
    FixtureAnalysisService,
    ReviewedSelection,
)

logger = get_logger(__name__)


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
    """把确定性分析交给 Claude 评审并落库（数值不变，异议留痕）。"""

    def __init__(
        self,
        *,
        analysis: FixtureAnalysisService,
        reviewer: CommitteeReviewer,
        decision_logs: DecisionLogRepository,
        value_bets: ValueBetRepository,
        model_version: str,
    ) -> None:
        self._analysis = analysis
        self._reviewer = reviewer
        self._decision_logs = decision_logs
        self._value_bets = value_bets
        self._model_version = model_version

    async def review(self, fixture: Fixture) -> CommitteeReviewResult:
        detailed = await self._analysis.analyze_detailed(fixture)

        # 无法建模或没有候选（无赔率）→ 无可评审内容，不调用 Claude、不落库。
        if detailed.model_input is None or not detailed.reviewed:
            return CommitteeReviewResult(
                fixture_id=fixture.id,
                analysis=detailed.result,
                message=detailed.result.message,
            )

        context = self._build_context(detailed)
        review = await self._reviewer.review(context)

        value_bet_ids = await self._persist_value_bets(fixture, detailed, review)
        decision_log = await self._persist_decision_log(fixture, detailed, review)

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

    @staticmethod
    def _build_context(detailed: DetailedAnalysis) -> CommitteeReviewContext:
        fixture = detailed.fixture
        model_input = detailed.model_input
        assert model_input is not None  # 调用点已保证
        result = detailed.result

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
            league_goals_per_game=model_input.league.goals_per_game,
            home_form=_form("home", model_input.home_stats),
            away_form=_form("away", model_input.away_stats),
            selections=selections,
        )

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
                # 数值来自模型（信心=综合评分/100）；文字理由来自 Claude。
                confidence=candidate.decision_score.value / 100.0,
                rationale=rationale_by_label.get(label, review.betting_recommendation_explanation),
            )
            saved = await self._value_bets.add(value_bet)
            ids.append(saved.id)
        return ids

    async def _persist_decision_log(
        self, fixture: Fixture, detailed: DetailedAnalysis, review: CommitteeReview
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
        )
        return await self._decision_logs.add(log)

    @staticmethod
    def _collect_disagreements(
        reviewed: list[ReviewedSelection], review: CommitteeReview
    ) -> list[str]:
        """合并 Claude 自述分歧与「评审立场 vs gate 结论」的确定性冲突（仅留痕）。"""
        notes = list(review.disagreements)
        by_label = {sr.selection_label: sr for sr in review.selection_reviews}
        for rs in reviewed:
            label = rs.candidate.selection.label
            sr = by_label.get(label)
            if sr is not None and not sr.agrees_with_model:
                gate = "推荐" if rs.decision.approved else "不推荐"
                notes.append(f"{label}：评审不认同 gate（立场={sr.stance.value}，gate={gate}）")
        return notes
