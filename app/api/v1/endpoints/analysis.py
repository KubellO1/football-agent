"""比赛分析端点（DB 驱动，纯数学，无外部 API）。

从数据库读取比赛与赔率，用既有的 Poisson/Elo/Kelly/Value 模型产出概率、EV、
Kelly 下注与推荐判定；不访问任何外部数据源，也不调用 Claude。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import FixtureAnalysisServiceDep, FixtureRepositoryDep
from app.schemas.analysis import (
    FixtureAnalysisResponse,
    ProbabilitiesOut,
    SelectionAnalysisOut,
)
from app.services.fixture_analysis import FixtureAnalysisResult

router = APIRouter(tags=["analysis"])


def _to_response(result: FixtureAnalysisResult) -> FixtureAnalysisResponse:
    probabilities: ProbabilitiesOut | None = None
    if result.probabilities:
        probabilities = ProbabilitiesOut(
            home=result.probabilities.get("home", 0.0),
            draw=result.probabilities.get("draw", 0.0),
            away=result.probabilities.get("away", 0.0),
        )
    return FixtureAnalysisResponse(
        fixture_id=result.fixture_id,
        probabilities=probabilities,
        expected_goals_home=result.expected_goals_home,
        expected_goals_away=result.expected_goals_away,
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
            for s in result.selections
        ],
        data_completeness=result.data_completeness,
        message=result.message,
    )


@router.post("/fixtures/{fixture_id}/analyze", response_model=FixtureAnalysisResponse)
async def analyze_fixture(
    fixture_id: UUID,
    fixtures: FixtureRepositoryDep,
    analysis: FixtureAnalysisServiceDep,
) -> FixtureAnalysisResponse:
    """分析一场已入库的比赛：概率、EV、Kelly 下注、推荐、信心与解释。

    数据全部来自数据库（此前 sync 同步的比赛与赔率）；本端点不访问外部 API，
    也不做 LLM 评审。历史数据不足时返回带 message 的说明、selections 为空。
    """
    fixture = await fixtures.get(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="比赛不存在")

    result = await analysis.analyze(fixture)
    return _to_response(result)
