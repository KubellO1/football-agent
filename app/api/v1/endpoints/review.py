"""AI 评审委员会端点。

在确定性数学分析之上叠加 LLM 评审：产出解释/批判，落库 ValueBet（由 gate 决定）
与 DecisionLog（每次评审一条，含可复现性元数据）。LLM 不改动任何数值。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CommitteeReviewServiceDep, FixtureRepositoryDep
from app.core.exceptions import ExternalServiceError
from app.schemas.analysis import ProbabilitiesOut, SelectionAnalysisOut
from app.schemas.committee_review import CommitteeReviewResponse
from app.services.committee_review import CommitteeReviewResult

router = APIRouter(tags=["review"])


def _to_response(result: CommitteeReviewResult) -> CommitteeReviewResponse:
    probabilities: ProbabilitiesOut | None = None
    if result.analysis.probabilities:
        p = result.analysis.probabilities
        probabilities = ProbabilitiesOut(
            home=p.get("home", 0.0), draw=p.get("draw", 0.0), away=p.get("away", 0.0)
        )
    return CommitteeReviewResponse(
        fixture_id=result.fixture_id,
        message=result.message,
        probabilities=probabilities,
        selections=[
            SelectionAnalysisOut(
                code=s.code,
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
                confidence=s.confidence,
                reasons=s.reasons,
                explanation=s.explanation,
            )
            for s in result.analysis.selections
        ],
        review=result.review,
        decision_log_id=result.decision_log_id,
        value_bet_ids=result.value_bet_ids,
    )


@router.post("/fixtures/{fixture_id}/review", response_model=CommitteeReviewResponse)
async def review_fixture(
    fixture_id: UUID,
    fixtures: FixtureRepositoryDep,
    review: CommitteeReviewServiceDep,
) -> CommitteeReviewResponse:
    """对一场已入库比赛做数学分析 + LLM 专家评审，并落库最终决策。

    数据来自数据库；数学模型给出全部数值，LLM 只解释与批判、不改数值，其异议
    仅记录进 DecisionLog。历史数据不足 / 无赔率时返回说明且不调用 LLM、不落库。
    """
    fixture = await fixtures.get(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="比赛不存在")

    try:
        result = await review.review(fixture)
    except ExternalServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _to_response(result)
