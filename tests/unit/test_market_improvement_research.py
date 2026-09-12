from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.research.football_data import HistoricalOddsQuote
from app.research.market_improvement import (
    ModelRow,
    build_model_rows,
    fit_isotonic,
    fit_platt,
    remove_overround,
)
from app.research.zero_cost import ResearchFixture, TeamMatchMetrics


def _fixture(index: int, season: str = "2025/2026") -> ResearchFixture:
    kickoff = datetime(2025, 8, 1, tzinfo=UTC) + timedelta(days=index)
    home_goals = 1 + index % 2
    away_goals = index % 2
    home = TeamMatchMetrics(
        home_goals,
        shots=12,
        shots_on_target=5,
        conversion=home_goals / 12,
    )
    away = TeamMatchMetrics(
        away_goals,
        shots=8,
        shots_on_target=3,
        conversion=away_goals / 8,
    )
    return ResearchFixture(
        f"fixture-{index}",
        "test",
        True,
        "Premier League",
        season,
        kickoff,
        "Alpha" if index % 2 == 0 else "Beta",
        "Beta" if index % 2 == 0 else "Alpha",
        home,
        away,
    )


def _quote(
    fixture_id: str,
    context: str,
    prices: tuple[float, float, float],
) -> HistoricalOddsQuote:
    return HistoricalOddsQuote(
        fixture_id,
        "football-data.co.uk",
        "market-average",
        context,
        *prices,
        ("H", "D", "A"),
    )


def test_market_baseline_removes_overround() -> None:
    probabilities = remove_overround((2.0, 3.6, 4.0))

    assert sum(probabilities) == pytest.approx(1.0)
    assert probabilities == pytest.approx((0.486486, 0.270270, 0.243243), abs=1e-6)


@pytest.mark.parametrize(
    "prices",
    ((1.0, 3.0, 4.0), (2.0, float("nan"), 4.0), (2.0, 3.0)),
)
def test_market_baseline_rejects_invalid_prices(prices: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="three finite decimal prices"):
        remove_overround(prices)


def test_model_rows_use_pre_closing_prices_and_keep_closing_for_evaluation_only() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    odds = (
        _quote(target.fixture_id, "PRE_CLOSING", (2.0, 3.5, 4.0)),
        _quote(target.fixture_id, "CLOSING", (1.5, 4.5, 7.0)),
    )

    rows = build_model_rows(fixtures, odds)

    assert len(rows) == 1
    assert rows[0].offered_odds == (2.0, 3.5, 4.0)
    assert rows[0].closing_odds == (1.5, 4.5, 7.0)
    assert rows[0].market_probabilities == pytest.approx(remove_overround((2.0, 3.5, 4.0)))


def test_native_market_and_calibrators_return_probability_simplex() -> None:
    rows = tuple(
        ModelRow(
            fixture=replace(_fixture(index), fixture_id=f"cal-{index}"),
            features=(0.0,) * 17,
            market_probabilities=(0.50, 0.28, 0.22),
            offered_odds=(2.0, 3.6, 4.5),
            closing_odds=None,
            actual_index=index % 3,
        )
        for index in range(12)
    )
    probabilities = tuple(row.market_probabilities for row in rows)
    actual = tuple(row.actual_index for row in rows)

    for calibrated in (
        fit_platt(probabilities, actual).apply((0.55, 0.25, 0.20)),
        fit_isotonic(probabilities, actual).apply((0.55, 0.25, 0.20)),
    ):
        assert sum(calibrated) == pytest.approx(1.0)
        assert all(0 <= value <= 1 for value in calibrated)


def test_build_model_rows_is_deterministic_and_leakage_safe() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    odds = (_quote(target.fixture_id, "PRE_CLOSING", (2.0, 3.5, 4.0)),)

    first = build_model_rows(fixtures, odds)
    changed_future = (*fixtures, replace(_fixture(25), home=TeamMatchMetrics(9)))
    second = build_model_rows(changed_future, odds)

    assert first == second
