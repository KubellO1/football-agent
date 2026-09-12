from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.research.football_data import (
    CompetitionSpec,
    FootballDataCsvAdapter,
    HistoricalOddsQuote,
    LongitudinalDataset,
    canonical_team_key,
    parse_csv,
)
from app.research.longitudinal import evaluate_market_targets, run_longitudinal_validation
from app.research.zero_cost import Prediction

if TYPE_CHECKING:
    from pathlib import Path

CSV = b"""Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,AvgH,AvgD,AvgA,AvgCH,AvgCD,AvgCA,B365H,B365D,B365A,B365CH,B365CD,B365CA\nE0,10/08/2025,16:30,Alpha FC,Beta FC,2,1,H,1,0,H,12,8,5,3,10,11,6,4,2,3,0,0,1.90,3.40,4.20,1.85,3.50,4.40,1.88,3.30,4.10,1.83,3.40,4.30\nE0,17/08/2025,14:00,Gamma FC,Delta FC,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n"""
SPEC = CompetitionSpec("Premier League", "E0", "Europe/London")


def test_csv_parser_preserves_real_fields_and_null_xg() -> None:
    parsed = parse_csv(CSV, spec=SPEC, season="2025/2026")

    assert len(parsed.fixtures) == 1
    assert parsed.rejected_rows == 1
    fixture = parsed.fixtures[0]
    assert (fixture.home.goals, fixture.away.goals) == (2, 1)
    assert (fixture.home.shots, fixture.away.shots) == (12, 8)
    assert (fixture.home.shots_on_target, fixture.away.shots_on_target) == (5, 3)
    assert fixture.home.conversion == 2 / 12
    assert fixture.home.xg is None
    assert fixture.away.xg is None
    assert fixture.odds is None


def test_historical_odds_are_isolated_from_model_features() -> None:
    parsed = parse_csv(CSV, spec=SPEC, season="2025/2026")

    closing = [quote for quote in parsed.odds if quote.context == "CLOSING"]
    assert {quote.bookmaker for quote in closing} == {"market-average", "Bet365"}
    assert all(quote.captured_at is None for quote in parsed.odds)
    assert all(quote.model_feature_allowed is False for quote in parsed.odds)
    assert all(quote.target_evaluation_allowed is True for quote in parsed.odds)


def test_adapter_cache_checksum_and_idempotency(tmp_path: Path) -> None:
    requests: list[str] = []

    def fetcher(url: str) -> bytes:
        requests.append(url)
        return CSV

    adapter = FootballDataCsvAdapter(tmp_path, fetcher=fetcher, minimum_request_interval_seconds=0)
    content, first = adapter._get("2526", "E0")
    cached, second = adapter._get("2526", "E0")

    assert content == cached == CSV
    assert len(requests) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.sha256 == second.sha256


def test_canonical_team_key_is_stable_without_alias_guessing() -> None:
    assert canonical_team_key("Ligue 1", "Paris Saint-Germain") == ("ligue-1:paris-saint-germain")
    assert canonical_team_key("La Liga", " Celta  Vigo ") == "la-liga:celta-vigo"


def test_duplicate_csv_rows_collapse_without_conflict(tmp_path: Path) -> None:
    duplicate = CSV + CSV.decode().splitlines()[1].encode() + b"\n"

    def fetcher(url: str) -> bytes:
        return duplicate

    adapter = FootballDataCsvAdapter(tmp_path, fetcher=fetcher, minimum_request_interval_seconds=0)
    dataset = adapter.acquire(["2526"])

    assert len(dataset.fixtures) == 5
    assert dataset.duplicate_count == 5


def test_market_backtest_uses_closing_odds_as_target_only() -> None:
    prediction = Prediction(
        "fixture-1",
        "Premier League",
        parse_csv(CSV, spec=SPEC, season="2025/2026").fixtures[0].kickoff,
        "A",
        5,
        0.60,
        0.22,
        0.18,
        "home",
    )
    quote = HistoricalOddsQuote(
        "fixture-1",
        "football-data.co.uk",
        "market-average",
        "CLOSING",
        2.0,
        3.4,
        4.1,
        ("AvgCH", "AvgCD", "AvgCA"),
    )

    result = evaluate_market_targets((prediction,), {"fixture-1": quote})

    assert result.status == "RESEARCH_ONLY"
    assert result.signal_count == 1
    assert result.unit_stake_roi == 1.0


def test_longitudinal_wrapper_does_not_modify_baseline_contract() -> None:
    fixture = parse_csv(CSV, spec=SPEC, season="2025/2026").fixtures[0]
    fixtures = tuple(
        replace(
            fixture,
            fixture_id=f"fixture-{index}",
            kickoff=fixture.kickoff.replace(day=min(index + 1, 28)),
            home_team="Alpha FC" if index % 2 == 0 else "Beta FC",
            away_team="Beta FC" if index % 2 == 0 else "Alpha FC",
        )
        for index in range(28)
    )

    result = run_longitudinal_validation(fixtures, (), holdout_season="2025/2026")

    assert result["baseline_runner_modified"] is False
    assert result["production_candidate"] is False
    assert result["no_xg_ablation"]["xg_required"] is False


def test_dataset_summary_exposes_rolling_metric_and_odds_coverage() -> None:
    parsed = parse_csv(CSV, spec=SPEC, season="2025/2026")
    dataset = LongitudinalDataset(
        parsed.fixtures,
        parsed.odds,
        parsed.supplemental,
        (),
        0,
        parsed.rejected_rows,
    )

    summary = dataset.summary()

    assert summary["fixture_count"] == 1
    assert summary["team_season_count"] == 2
    assert summary["rolling_history_ready_team_seasons"] == {"5": 0, "10": 0, "20": 0}
    assert summary["metric_observation_counts"] == {
        "xg": 0,
        "shots": 2,
        "shots_on_target": 2,
        "possession": 0,
        "goalkeeper_saves": 0,
        "conversion": 2,
    }
    assert summary["odds_context_counts"] == {"PRE_CLOSING": 2, "CLOSING": 2}
    assert summary["odds_bookmaker_counts"] == {"market-average": 2, "Bet365": 2}


def test_model_selection_uses_previous_season_not_holdout() -> None:
    fixture = parse_csv(CSV, spec=SPEC, season="2024/2025").fixtures[0]
    fixtures = tuple(
        replace(
            fixture,
            fixture_id=f"selection-{index}",
            kickoff=fixture.kickoff.replace(day=min(index + 1, 28)),
            home_team="Alpha FC" if index % 2 == 0 else "Beta FC",
            away_team="Beta FC" if index % 2 == 0 else "Alpha FC",
        )
        for index in range(28)
    ) + tuple(
        replace(
            fixture,
            fixture_id=f"holdout-{index}",
            season="2025/2026",
            kickoff=fixture.kickoff.replace(year=2026, day=min(index + 1, 28)),
            home_team="Alpha FC" if index % 2 == 0 else "Beta FC",
            away_team="Beta FC" if index % 2 == 0 else "Alpha FC",
        )
        for index in range(28)
    )

    result = run_longitudinal_validation(fixtures, (), holdout_season="2025/2026")

    assert result["selection_season"] == "2024/2025"
    assert result["status"] == "PASS"
    assert result["selected_holdout_evaluation"]["sample_size"] > 0


def test_no_xg_ablation_evaluates_shots_and_sot_without_xg() -> None:
    fixture = parse_csv(CSV, spec=SPEC, season="2024/2025").fixtures[0]
    fixtures = tuple(
        replace(
            fixture,
            fixture_id=f"selection-{index}",
            kickoff=fixture.kickoff.replace(day=min(index + 1, 28)),
            home_team="Alpha FC" if index % 2 == 0 else "Beta FC",
            away_team="Beta FC" if index % 2 == 0 else "Alpha FC",
        )
        for index in range(28)
    ) + tuple(
        replace(
            fixture,
            fixture_id=f"holdout-{index}",
            season="2025/2026",
            kickoff=fixture.kickoff.replace(year=2026, day=min(index + 1, 28)),
            home_team="Alpha FC" if index % 2 == 0 else "Beta FC",
            away_team="Beta FC" if index % 2 == 0 else "Alpha FC",
        )
        for index in range(28)
    )

    result = run_longitudinal_validation(
        fixtures,
        (),
        holdout_season="2025/2026",
        windows=(5,),
    )

    ablation = result["no_xg_ablation"]
    assert ablation["status"] == "PASS"
    assert ablation["selection_season"] == "2024/2025"
    assert ablation["holdout_season"] == "2025/2026"
    assert ablation["F"]["evaluation"]["sample_size"] > 0
    for variant in ("A", "B", "C", "D", "E"):
        evaluation = ablation["variants"]["5"]["variants"][variant]
        assert evaluation["selection_evaluation"]["sample_size"] > 0
        assert evaluation["holdout_evaluation"]["sample_size"] > 0
