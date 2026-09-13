from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.research import information_timing
from app.research.football_data import CompetitionSpec, HistoricalOddsQuote, parse_csv
from app.research.information_timing import (
    SIGNAL_CONTRACTS,
    build_signal_rows,
    fair_closing_value,
)
from app.research.zero_cost import ResearchFixture, TeamMatchMetrics


def _fixture(index: int) -> ResearchFixture:
    return ResearchFixture(
        f"f-{index}",
        "test",
        True,
        "Premier League",
        "2025/2026",
        datetime(2025, 8, 1, tzinfo=UTC) + timedelta(days=index),
        "Alpha" if index % 2 == 0 else "Beta",
        "Beta" if index % 2 == 0 else "Alpha",
        TeamMatchMetrics(1, shots=10, shots_on_target=4, conversion=0.1),
        TeamMatchMetrics(0, shots=8, shots_on_target=2, conversion=0.0),
    )


def _quote(
    fixture_id: str, bookmaker: str, context: str, prices: tuple[float, float, float]
) -> HistoricalOddsQuote:
    return HistoricalOddsQuote(
        fixture_id,
        "football-data.co.uk",
        bookmaker,
        context,
        *prices,
        ("H", "D", "A"),
    )


def test_football_data_parser_reads_bwin_without_changing_market_contract() -> None:
    csv = b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,BWH,BWD,BWA,BWCH,BWCD,BWCA\nE0,10/08/2025,16:30,A,B,1,0,H,2.0,3.5,4.0,1.9,3.6,4.2\n"
    parsed = parse_csv(
        csv, spec=CompetitionSpec("Premier League", "E0", "Europe/London"), season="2025/2026"
    )

    assert [(item.bookmaker, item.context) for item in parsed.odds] == [
        ("Bwin", "PRE_CLOSING"),
        ("Bwin", "CLOSING"),
    ]
    assert all(item.model_feature_allowed is False for item in parsed.odds)


def test_cross_book_features_are_deterministic_and_never_contain_closing_prices() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    opening = (
        _quote(target.fixture_id, "market-average", "PRE_CLOSING", (2.0, 3.5, 4.0)),
        _quote(target.fixture_id, "Bet365", "PRE_CLOSING", (1.9, 3.6, 4.2)),
        _quote(target.fixture_id, "Bwin", "PRE_CLOSING", (2.1, 3.4, 3.9)),
    )
    changed_closing = _quote(target.fixture_id, "market-average", "CLOSING", (1.3, 6.0, 12.0))

    first = build_signal_rows(fixtures, opening, feature_set="CROSS_BOOK")
    second = build_signal_rows(fixtures, (*opening, changed_closing), feature_set="CROSS_BOOK")

    assert first[0].features == second[0].features
    assert first[0].closing_odds is None
    assert second[0].closing_odds == (1.3, 6.0, 12.0)


def test_cross_book_signal_requires_two_independent_quotes() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    odds = (
        _quote(target.fixture_id, "market-average", "PRE_CLOSING", (2.0, 3.5, 4.0)),
        _quote(target.fixture_id, "Bet365", "PRE_CLOSING", (1.9, 3.6, 4.2)),
    )

    assert build_signal_rows(fixtures, odds, feature_set="CROSS_BOOK") == ()


def test_fair_clv_removes_closing_overround() -> None:
    value = fair_closing_value(2.2, (2.0, 3.6, 4.0), 0)

    assert value == pytest.approx(0.07027027)


def test_signal_contracts_reject_unreconstructable_information() -> None:
    contracts = {item.name: item for item in SIGNAL_CONTRACTS}

    assert contracts["rest_days_congestion"].historically_reconstructable is True
    assert contracts["confirmed_lineups"].historically_reconstructable is False
    assert contracts["post_information_market_movement"].research_status == "UNAVAILABLE"


def test_future_fixtures_do_not_change_prior_rest_signal() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    odds = (_quote(target.fixture_id, "market-average", "PRE_CLOSING", (2.0, 3.5, 4.0)),)
    before = build_signal_rows(fixtures, odds, feature_set="REST")
    after = build_signal_rows(
        (*fixtures, replace(_fixture(25), home=TeamMatchMetrics(9))), odds, feature_set="REST"
    )

    assert before == after


def test_every_eligible_signal_specification_has_oos_evaluation() -> None:
    fixtures = tuple(_fixture(index) for index in range(24))
    target = fixtures[-1]
    odds = (
        _quote(target.fixture_id, "market-average", "PRE_CLOSING", (2.0, 3.5, 4.0)),
        _quote(target.fixture_id, "market-average", "CLOSING", (1.95, 3.55, 4.1)),
    )
    row = build_signal_rows(fixtures, odds, feature_set="REST")[0]
    rows = tuple(
        replace(row, fixture=replace(row.fixture, season=season))
        for season in ("2023/2024", "2024/2025", "2025/2026")
    )

    class MarketModel:
        def predict(self, candidate: object) -> tuple[float, float, float]:
            return row.market_probabilities

    feature_sets = dict.fromkeys(("REST", "CROSS_BOOK", "CROSS_BOOK_REST", "FOOTBALL", "ALL"), rows)
    fitted = dict.fromkeys(feature_sets, (None, "RAW"))
    models = {name: MarketModel() for name in feature_sets}

    result = information_timing._evaluate_oos_candidates(
        feature_sets,
        models,
        fitted,  # type: ignore[arg-type]
        selection_season="2024/2025",
        holdout_season="2025/2026",
    )

    assert set(result) == {
        "REST",
        "CROSS_BOOK",
        "CROSS_BOOK_REST",
        "FOOTBALL",
        "ALL",
    }
    assert all(evaluation["sample_size"] == 1 for evaluation in result.values())
