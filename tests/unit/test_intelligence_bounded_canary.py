import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from scripts.run_bounded_zero_cost_canary import run_public_canary

from app.intelligence.adapters import FIVE_LEAGUES
from app.intelligence.canary import run_bounded_canary
from app.intelligence.contracts import RawPayload, SourcePolicy
from app.intelligence.http import CachedHttpClient
from app.intelligence.matching import FixtureIdentity


def _raw(code: str, matches: list[dict[str, object]]) -> RawPayload:
    league = next(item for item in FIVE_LEAGUES if item.code == code)
    return RawPayload(
        source=f"openfootball-{code}",
        source_url=league.url("2026-27"),
        captured_at=datetime(2026, 9, 20, 8, tzinfo=UTC),
        content=json.dumps({"matches": matches}).encode(),
        media_type="application/json",
        source_policy=SourcePolicy.OPEN_DATA,
    )


def test_bounded_canary_exact_match_replay_and_provenance(tmp_path: Path) -> None:
    raws = {
        "en.1": _raw(
            "en.1",
            [
                {"date": "2026-09-21", "time": "16:00", "team1": "Arsenal", "team2": "Chelsea"},
                {"date": "2026-09-22", "team1": "Liverpool", "team2": "Everton"},
            ],
        )
    }
    kickoff = datetime(2026, 9, 21, 15, tzinfo=UTC)  # London is UTC+1 in September.
    references = (
        FixtureIdentity("db-fixture", "Premier League", "England", kickoff, "Arsenal", "Chelsea"),
    )
    kwargs = {
        "raw_by_league": raws,
        "references": references,
        "output_dir": tmp_path,
        "now": datetime(2026, 9, 20, 9, tzinfo=UTC),
        "season": "2026-27",
    }
    first = run_bounded_canary(**kwargs)
    second = run_bounded_canary(**kwargs)
    assert first.selected_fixtures == 1
    assert first.inserted_observations == 1
    assert first.reason_counts["KICKOFF_TIME_UNKNOWN"] == 1
    assert second.inserted_observations == 0
    assert second.replay_duplicates == 1
    rows = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["fixture_id"] == "db-fixture"
    assert rows[0]["source_policy"] == "OPEN_DATA"
    assert rows[0]["raw_payload_hash"] == raws["en.1"].sha256
    assert rows[0]["parser_version"]


def test_bounded_canary_rejects_wrong_country_and_honors_limit(tmp_path: Path) -> None:
    raw = _raw(
        "it.1",
        [
            {"date": "2026-09-21", "time": "20:00", "team1": "Milan", "team2": "Inter"},
        ],
    )
    kickoff = datetime(2026, 9, 21, 18, tzinfo=UTC)
    wrong = FixtureIdentity("brazil", "Serie A", "Brazil", kickoff, "Milan", "Inter")
    result = run_bounded_canary(
        raw_by_league={"it.1": raw},
        references=(wrong,),
        output_dir=tmp_path,
        now=datetime(2026, 9, 20, 9, tzinfo=UTC),
        season="2026-27",
    )
    assert result.selected_fixtures == 0
    assert result.reason_counts["NO_EXACT_FIXTURE_MATCH"] == 1
    assert not (tmp_path / "observations.jsonl").read_text().strip()
    with pytest.raises(ValueError, match="1..10"):
        run_bounded_canary(
            raw_by_league={},
            references=(),
            output_dir=tmp_path,
            now=datetime(2026, 9, 20, 9, tzinfo=UTC),
            season="2026-27",
            max_fixtures=11,
        )


def test_public_canary_never_calls_paid_provider_and_fails_closed_without_references(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "raw.githubusercontent.com"
        return httpx.Response(
            200,
            json={
                "matches": [
                    {"date": "2026-09-21", "time": "16:00", "team1": "Arsenal", "team2": "Chelsea"}
                ]
            },
        )

    client = CachedHttpClient(
        tmp_path / "cache",
        request_budget=5,
        user_agent="FootballAgent-ZeroCost-Test/1.0",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 9, 20, 9, tzinfo=UTC),
    )
    try:
        result = run_public_canary(
            output_dir=tmp_path,
            references=(),
            season="2026-27",
            now=datetime(2026, 9, 20, 9, tzinfo=UTC),
            client=client,
        )
    finally:
        client.close()
    assert calls == 5
    assert result["paid_api_requests"] == 0
    assert result["selected_fixtures"] == 0
    assert result["fixture_mapping_pass"] is False
    assert result["production_cutover_ready"] is False
