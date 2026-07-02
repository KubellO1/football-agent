"""EnsembleMatchModel 单元测试。

验证：链路能产出 1X2 概率与候选、非 1X2 报价被跳过、候选各字段落在合法范围、
数值口径（概率和≈1、edge 与 ValueEdge 一致），以及 Elo 信号的透传与期望得分。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.entities.fixture import Fixture
from app.models.value_objects.decision import DataCompleteness, DecisionScore, EvidenceLevel
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.statistics import TeamStatistics
from app.services.modeling import MarketQuote, ModelInput
from app.services.models.ensemble import EnsembleMatchModel
from app.services.models.lambda_estimator import LeagueAverages


def _stats(*, gf: int, ga: int) -> TeamStatistics:
    return TeamStatistics(
        matches_played=10,
        wins=6,
        draws=2,
        losses=2,
        goals_for=gf,
        goals_against=ga,
        xg_for=float(gf),
        xg_against=float(ga),
    )


def _model_input(
    quotes: list[MarketQuote],
    *,
    home_elo: float | None = None,
    away_elo: float | None = None,
) -> ModelInput:
    fixture = Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc),
    )
    return ModelInput(
        fixture=fixture,
        home_stats=_stats(gf=20, ga=10),  # 强主队
        away_stats=_stats(gf=10, ga=20),  # 弱客队
        league=LeagueAverages(goals_per_game=1.4),
        quotes=quotes,
        bankroll=Money(Decimal("1000")),
        data_completeness=DataCompleteness(95.0),
        evidence_level=EvidenceLevel.B,
        home_elo=home_elo,
        away_elo=away_elo,
    )


@pytest.mark.unit
async def test_produces_1x2_probabilities_and_candidate() -> None:
    quotes = [MarketQuote(Selection(MarketType.MATCH_RESULT, "home"), Odds(Decimal("2.0")))]
    output = await EnsembleMatchModel().analyze(_model_input(quotes))

    total = sum(p.value for p in output.outcome_probabilities.values())
    assert total == pytest.approx(1.0, abs=1e-9)
    assert output.expected_goals is not None

    assert len(output.candidates) == 1
    candidate = output.candidates[0]
    assert candidate.selection.code == "home"
    assert 0.0 <= candidate.model_probability.value <= 1.0
    assert isinstance(candidate.decision_score, DecisionScore)
    assert 0.0 <= candidate.decision_score.value <= 100.0
    assert candidate.edge.edge == pytest.approx(
        candidate.model_probability.value * 2.0 - 1.0
    )


@pytest.mark.unit
async def test_non_1x2_quote_is_skipped() -> None:
    quotes = [
        MarketQuote(Selection(MarketType.MATCH_RESULT, "home"), Odds(Decimal("2.0"))),
        MarketQuote(Selection(MarketType.OVER_UNDER, "over", line=2.5), Odds(Decimal("1.9"))),
    ]
    output = await EnsembleMatchModel().analyze(_model_input(quotes))
    assert len(output.candidates) == 1
    assert output.candidates[0].selection.market is MarketType.MATCH_RESULT


@pytest.mark.unit
async def test_no_quotes_yields_no_candidates() -> None:
    output = await EnsembleMatchModel().analyze(_model_input([]))
    assert output.candidates == []
    assert output.outcome_probabilities
    assert output.expected_goals is not None


@pytest.mark.unit
async def test_elo_ratings_passthrough_and_expected() -> None:
    quotes = [MarketQuote(Selection(MarketType.MATCH_RESULT, "home"), Odds(Decimal("2.0")))]
    output = await EnsembleMatchModel().analyze(
        _model_input(quotes, home_elo=1900.0, away_elo=1500.0)
    )
    assert output.elo_home == 1900.0
    assert output.elo_away == 1500.0
    # 强主队（评分领先）→ Elo 期望得分 > 0.5
    assert output.elo_expected_home is not None
    assert output.elo_expected_home > 0.5


@pytest.mark.unit
async def test_elo_absent_yields_none() -> None:
    quotes = [MarketQuote(Selection(MarketType.MATCH_RESULT, "home"), Odds(Decimal("2.0")))]
    output = await EnsembleMatchModel().analyze(_model_input(quotes))
    assert output.elo_home is None
    assert output.elo_away is None
    assert output.elo_expected_home is None
