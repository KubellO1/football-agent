"""每日推荐（Top Picks）端点。

- POST /recommendations/today/run：跑当日批处理（数学分析全部比赛 → gate+阈值预筛
  → 按 EV 取 Top-N → 仅这几场调用 Claude 评审并落库）。这是唯一会花费 Claude 的入口。
- GET  /recommendations/today：读取已落库的当日推荐，**绝不**调用 Claude。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DailyRecommendationsReaderDep, DailyTopPicksServiceDep
from app.core.exceptions import ExternalServiceError
from app.schemas.recommendations import (
    DailyRunResponse,
    FixtureRecommendationOut,
    RecommendationBetOut,
    RecommendationsTodayResponse,
)
from app.services.daily_top_picks import RecommendationsView

router = APIRouter(tags=["recommendations"])


@router.post("/recommendations/today/run", response_model=DailyRunResponse)
async def run_today(
    picks: DailyTopPicksServiceDep,
    on_date: date | None = None,
) -> DailyRunResponse:
    """跑当日 Top Picks 批处理。仅对通过 gate+阈值的前 N 场调用 Claude（成本可控）。"""
    target = on_date or datetime.now(UTC).date()
    try:
        report = await picks.run(target)
    except ExternalServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return DailyRunResponse(
        date=report.date,
        fixtures_analyzed=report.fixtures_analyzed,
        fixtures_qualified=report.fixtures_qualified,
        fixtures_reviewed=report.fixtures_reviewed,
        fixtures_skipped_existing=report.fixtures_skipped_existing,
        value_bets_created=report.value_bets_created,
        reviewed_fixture_ids=report.reviewed_fixture_ids,
    )


@router.get("/recommendations/today", response_model=RecommendationsTodayResponse)
async def recommendations_today(
    reader: DailyRecommendationsReaderDep,
    on_date: date | None = None,
) -> RecommendationsTodayResponse:
    """读取当日已落库的推荐（含 Claude 评审摘要）。纯读库，不触发任何 Claude 调用。"""
    target = on_date or datetime.now(UTC).date()
    view: RecommendationsView = await reader.read(target)
    return RecommendationsTodayResponse(
        date=view.date,
        count=view.count,
        recommendations=[
            FixtureRecommendationOut(
                fixture_id=rec.fixture_id,
                review_summary=rec.review_summary,
                review=rec.review,
                bets=[
                    RecommendationBetOut(
                        selection_label=b.selection_label,
                        decimal_odds=b.decimal_odds,
                        model_probability=b.model_probability,
                        edge=b.edge,
                        expected_value=b.expected_value,
                        kelly_fraction=b.kelly_fraction,
                        kelly_stake=b.kelly_stake,
                        currency=b.currency,
                        confidence=b.confidence,
                        rationale=b.rationale,
                    )
                    for b in rec.bets
                ],
            )
            for rec in view.recommendations
        ],
    )
