"""Fetch five public OpenFootball files for a <=10-fixture isolated Canary.

No application database or paid provider is touched.  Fixture references, when
available, must be exported independently into a local JSON file first.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.intelligence.adapters import FIVE_LEAGUES
from app.intelligence.canary import run_bounded_canary
from app.intelligence.contracts import SourcePolicy
from app.intelligence.http import CachedHttpClient
from app.intelligence.matching import FixtureIdentity

if TYPE_CHECKING:
    from app.intelligence.contracts import RawPayload


def _references(path: Path | None) -> tuple[FixtureIdentity, ...]:
    if path is None:
        return ()
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("fixture reference manifest must be a JSON list")
    return tuple(
        FixtureIdentity(
            fixture_id=str(row["fixture_id"]),
            competition=str(row["competition"]),
            country=str(row["country"]),
            kickoff=datetime.fromisoformat(row["kickoff"]),
            home_team=str(row["home_team"]),
            away_team=str(row["away_team"]),
        )
        for row in rows
    )


def run_public_canary(
    *,
    output_dir: Path,
    references: tuple[FixtureIdentity, ...],
    season: str,
    now: datetime,
    client: CachedHttpClient,
) -> dict[str, object]:
    raw_by_league: dict[str, RawPayload] = {}
    source_errors: dict[str, str] = {}
    requests_before = client.requests
    for league in FIVE_LEAGUES:
        try:
            fetched = client.get_json(
                league.url(season),
                source=f"openfootball-{league.code}",
                source_policy=SourcePolicy.OPEN_DATA,
                cache_ttl=timedelta(hours=12),
            )
            raw_by_league[league.code] = fetched.payload
        except Exception as exc:
            source_errors[league.code] = type(exc).__name__
    result = run_bounded_canary(
        raw_by_league=raw_by_league,
        references=references,
        output_dir=output_dir,
        now=now,
        season=season,
    )
    return {
        **asdict(result),
        "public_http_requests": client.requests - requests_before,
        "paid_api_requests": 0,
        "production_db_writes": 0,
        "source_errors": source_errors,
        "fixture_mapping_pass": bool(result.selected_fixtures and not source_errors),
        # This probe has no approved automated injury/lineup or bookmaker-odds path.
        "production_cutover_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--references-json", type=Path)
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--request-budget", type=int, default=5)
    args = parser.parse_args()
    client = CachedHttpClient(
        args.output_dir / "cache",
        request_budget=min(args.request_budget, 5),
        user_agent="FootballAgent-ZeroCost/1.0 (+https://github.com/KubellO1/football-agent)",
        minimum_interval_seconds=1.0,
    )
    try:
        result = run_public_canary(
            output_dir=args.output_dir,
            references=_references(args.references_json),
            season=args.season,
            now=datetime.now(UTC),
            client=client,
        )
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
