"""比赛分析端点。"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AnalysisPipelineDep, FixtureRepositoryDep
from app.models.entities.fixture import Fixture
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.statistics import TeamStatistics
from app.schemas.analysis import AnalyzeRequest, AnalyzeResponse, TeamStatsInput, ValueBetOut
from app.services.analysis_pipeline import AnalysisResult
from app.services.modeling import MarketQuote, ModelInput
from app.services.models.lambda_estimator import LeagueAverages

router = APIRouter(tags=["analysis"])


def _to_team_stats(stats: TeamStatsInput) -> TeamStatistics:
    return TeamStatistics(
        matches_played=stats.matches_played,
        wins=stats.wins,
        draws=stats.draws,
        losses=stats.losses,
        goals_for=stats.goals_for,
        goals_against=stats.goals_against,
        xg_for=stats.xg_for,
        xg_against=stats.xg_against,
    )


def _build_model_input(fixture: Fixture, request: AnalyzeRequest) -> ModelInput:
    quotes = [
        MarketQuote(
            selection=Selection(market=MarketType(q.market), code=q.code, line=q.line),
            odds=Odds(Decimal(str(q.odds_decimal))),
            bookmaker_id=q.bookmaker_id,
        )
        for q in request.quotes
    ]
    return ModelInput(
        fixture=fixture,
        home_stats=_to_team_stats(request.home_stats),
        away_stats=_to_team_stats(request.away_stats),
        league=LeagueAverages(goals_per_game=request.league_goals_per_game),
        quotes=quotes,
        bankroll=Money(Decimal(str(request.bankroll)), request.currency),
        data_completeness=DataCompleteness(request.data_completeness),
        evidence_level=EvidenceLevel(request.evidence_level),
        home_elo=request.home_elo,
        away_elo=request.away_elo,
    )


def _to_response(result: AnalysisResult) -> AnalyzeResponse:
    selected = [
        ValueBetOut(
            selection_label=bet.selection.label,
            decimal_odds=float(bet.odds.decimal),
            model_probability=bet.model_probability.value,
            edge=bet.edge.edge,
            stake_fraction=bet.stake.fraction_of_bankroll if bet.stake is not None else None,
            confidence=bet.confidence,
            rationale=bet.rationale,
        )
        for bet in result.selected
    ]
    reasoning = result.reasoning
    return AnalyzeResponse(
        message=result.message,
        selected=selected,
        chief_summary=reasoning.chief_summary if reasoning is not None else None,
        key_factors=reasoning.key_factors if reasoning is not None else [],
    )


@router.post("/fixtures/{fixture_id}/analyze", response_model=AnalyzeResponse)
async def analyze_fixture(
    fixture_id: UUID,
    request: AnalyzeRequest,
    fixtures: FixtureRepositoryDep,
    pipeline: AnalysisPipelineDep,
) -> AnalyzeResponse:
    """分析一场比赛并给出价值投注推荐（或说明今日无价值）。"""
    fixture = await fixtures.get(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="比赛不存在")

    try:
        model_input = _build_model_input(fixture, request)
    except (ValueError, KeyError) as exc:
        # 领域值对象/枚举校验失败 → 请求数据非法
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    result = await pipeline.analyze(model_input)
    return _to_response(result)
