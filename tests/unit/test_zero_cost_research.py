from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.research.zero_cost import (
    FIVE_LEAGUES,
    ResearchFixture,
    TeamMatchMetrics,
    ZeroCostExperimentRunner,
    build_temporal_samples,
    deduplicate_fixtures,
    load_research_fixtures,
    validate_dataset_manifest,
    variant_is_available,
)


def _metrics(goals: int, *, complete: bool = True) -> TeamMatchMetrics:
    return TeamMatchMetrics(
        goals=goals,
        xg=1.2 if complete else None,
        shots=11 if complete else None,
        shots_on_target=4 if complete else None,
        possession=52.0 if complete else None,
        goalkeeper_saves=3 if complete else None,
        conversion=0.1 if complete else None,
        ppda=9.5 if complete else None,
        set_piece_shots=2 if complete else None,
        header_shots=1 if complete else None,
    )


def _fixture(index: int, *, complete: bool = True) -> ResearchFixture:
    return ResearchFixture(
        fixture_id=str(index),
        source="test-open-data",
        research_only=True,
        competition="Premier League",
        season="2024/2025",
        kickoff=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
        home_team="Alpha" if index % 2 == 0 else "Beta",
        away_team="Beta" if index % 2 == 0 else "Alpha",
        home=_metrics(index % 3, complete=complete),
        away=_metrics((index + 1) % 3, complete=complete),
    )


def test_dataset_manifest_is_valid() -> None:
    path = Path("docs/zero_cost_research_dataset_manifest_v1.json")

    manifest = validate_dataset_manifest(path)

    assert manifest["new_paid_provider_budget_eur"] == 0
    assert all(
        dataset["production_allowed"] is False
        for dataset in manifest["datasets"]
        if dataset.get("research_only") is True
    )


def test_manifest_rejects_research_only_production_claim(tmp_path: Path) -> None:
    source = json.loads(
        Path("docs/zero_cost_research_dataset_manifest_v1.json").read_text(encoding="utf-8")
    )
    source["datasets"][0]["production_allowed"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="research-only"):
        validate_dataset_manifest(path)


def test_deduplication_collapses_equal_fixture() -> None:
    fixture = _fixture(1)

    unique, duplicate_count = deduplicate_fixtures([fixture, fixture])

    assert unique == (fixture,)
    assert duplicate_count == 1


def test_deduplication_rejects_conflicting_natural_key() -> None:
    fixture = _fixture(1)
    conflicting = replace(fixture, fixture_id="other", home=_metrics(9))

    with pytest.raises(ValueError, match="conflicting natural fixture key"):
        deduplicate_fixtures([fixture, conflicting])


def test_temporal_builder_uses_only_prior_same_season_rows() -> None:
    fixtures = [_fixture(index) for index in range(8)]
    future = replace(
        _fixture(99),
        fixture_id="other-season",
        season="2025/2026",
        kickoff=datetime(2023, 12, 1, tzinfo=UTC),
    )

    samples = build_temporal_samples([*reversed(fixtures), future], window=5)

    assert samples
    assert all(
        row.kickoff < sample.fixture.kickoff
        for sample in samples
        for row in (*sample.home_history, *sample.away_history)
    )
    assert all(sample.fixture.season == "2024/2025" for sample in samples)


def test_feature_contract_falls_back_without_optional_metrics() -> None:
    samples = build_temporal_samples([_fixture(index, complete=False) for index in range(8)], 5)
    sample = samples[0]

    assert variant_is_available(sample, "A") is True
    assert variant_is_available(sample, "B") is True
    assert variant_is_available(sample, "C") is False
    assert variant_is_available(sample, "D") is False
    assert variant_is_available(sample, "E") is False
    assert variant_is_available(sample, "F") is False


def test_runner_is_deterministic_and_reports_all_five_leagues() -> None:
    fixtures = [_fixture(index) for index in range(30)]
    runner = ZeroCostExperimentRunner()

    first = runner.run(fixtures).to_dict()
    second = runner.run(reversed(fixtures)).to_dict()

    assert first == second
    assert first["betting_simulation"] == "INSUFFICIENT_ODDS_DATA"
    assert set(first["windows"]["5"]["variants"]["A"]["by_league"]) == set(FIVE_LEAGUES)


def test_sparse_current_assets_are_reported_as_insufficient() -> None:
    report = ZeroCostExperimentRunner().run([_fixture(index) for index in range(5)])

    assert report.status == "INSUFFICIENT_SAMPLE"
    assert report.outputs["ZERO_COST_MODEL_FEASIBLE"] == "no"
    assert report.outputs["FIVE_LEAGUE_GENERALIZATION"] == "insufficient_data"


def test_loader_preserves_missing_xg_as_null(tmp_path: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "fixtures": [
            {
                "fixture_id": "1",
                "source": "open-test",
                "research_only": True,
                "competition": "La Liga",
                "season": "2024/2025",
                "kickoff": "2024-01-01T12:00:00+00:00",
                "home_team": "Home",
                "away_team": "Away",
                "home": {"goals": 2, "xg": None},
                "away": {"goals": 1, "xg": None},
            }
        ],
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    fixtures = load_research_fixtures(path)

    assert fixtures[0].home.goals == 2
    assert fixtures[0].home.xg is None
    assert fixtures[0].away.xg is None
