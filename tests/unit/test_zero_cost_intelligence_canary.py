import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from scripts.run_zero_cost_intelligence_canary import run_canary

from app.intelligence.http import CachedHttpClient

FIXTURES = Path(__file__).parents[1] / "fixtures" / "intelligence"


def test_canary_is_cached_and_idempotent(tmp_path: Path) -> None:
    openfootball = (FIXTURES / "openfootball.json").read_bytes()
    weather = (FIXTURES / "met_no.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = weather if request.url.host == "api.met.no" else openfootball
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    now = datetime(2026, 9, 13, 12, tzinfo=UTC)
    client = CachedHttpClient(
        tmp_path / "cache",
        request_budget=6,
        user_agent="FootballAgent-ZeroCost-Test/1.0",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: now,
    )
    try:
        first = run_canary(output_dir=tmp_path, client=client, season="2026-27", now=now)
        second = run_canary(output_dir=tmp_path, client=client, season="2026-27", now=now)
    finally:
        client.close()

    assert first.external_requests == 6
    assert first.observations_inserted == 16
    assert first.duplicate_observations == 0
    assert second.external_requests == 0
    assert second.cache_hits == 6
    assert second.observations_inserted == 0
    assert second.duplicate_observations == 16
    rows = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text().splitlines()]
    assert len(rows) == 16
    assert all(row["raw_payload_hash"] for row in rows)
    assert all(row["parser_version"] for row in rows)
    assert all(row["source_policy"] for row in rows)
