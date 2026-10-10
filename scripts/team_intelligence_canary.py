"""Run a production-isolated API-Football team-intelligence Canary."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.providers.api_football_rate_limit import ApiFootballRequestLimiter
from app.providers.impl.api_football_fixture_lineup_provider import (
    ApiFootballFixtureLineupProvider,
)
from app.providers.impl.api_football_player_availability_provider import (
    ApiFootballPlayerAvailabilityProvider,
)
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.team_intelligence.api_client import ApiFootballEvidenceClient
from app.team_intelligence.api_football import ApiFootballTeamIntelligenceAdapter
from app.team_intelligence.conflicts import resolve_conflicts
from app.team_intelligence.contracts import CaptureWindow, Observation, canonical_json
from app.team_intelligence.matching import IdentityResolver, canonical_name
from app.team_intelligence.storage import ContentAddressedArchive, ObservationStore

if TYPE_CHECKING:
    from app.providers.schemas.fixtures import ProviderFixture

LEAGUES = {
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
    "Ligue 1": 61,
}
NAMESPACE = uuid.UUID("bd47a543-2fd8-4ca3-9730-64f7632a7cf4")


def _canonical_id(kind: str, *parts: object) -> str:
    value = ":".join((kind, *(canonical_name(str(part)) for part in parts)))
    return str(uuid.uuid5(NAMESPACE, value))


async def run(output: Path) -> dict[str, Any]:
    settings = Settings()
    key = settings.api_football_key
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY_CONFIGURED=no")
    limiter = ApiFootballRequestLimiter(
        min_interval_seconds=max(settings.api_football_min_request_interval_seconds, 2.0),
        per_minute_budget=min(settings.api_football_per_minute_request_budget, 20),
        daily_budget=min(settings.api_football_daily_request_budget, 100),
        run_budget=25,
        circuit_cooldown_seconds=settings.api_football_circuit_cooldown_seconds,
    )
    client = ApiFootballEvidenceClient(
        api_key=key,
        base_url=settings.api_football_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=min(settings.provider_max_retries, 2),
        limiter=limiter,
    )
    archive = ContentAddressedArchive(output / "raw")
    store = ObservationStore(output / "observations")
    resolver = IdentityResolver()
    adapter = ApiFootballTeamIntelligenceAdapter()
    observations: list[Observation] = []
    fixtures: list[tuple[str, ProviderFixture]] = []
    mapping_failures: list[str] = []
    source_errors: list[str] = []
    now = datetime.now(UTC)
    paris_today = now.astimezone(ZoneInfo("Europe/Paris")).date()
    try:
        for league_name, league_id in LEAGUES.items():
            raw = await client.get(
                "/fixtures",
                params={
                    "league": league_id,
                    "season": paris_today.year,
                    "from": paris_today.isoformat(),
                    "to": (paris_today + timedelta(days=14)).isoformat(),
                    "timezone": "UTC",
                },
            )
            archive.archive(raw)
            payload = json.loads(raw.content)
            parsed = [
                ApiFootballProvider._parse_fixture(item)
                for item in payload["response"]
                if isinstance(item, dict)
            ]
            upcoming = sorted(
                (
                    fixture
                    for fixture in parsed
                    if fixture.status in {"NS", "TBD"} and fixture.kickoff > now
                ),
                key=lambda fixture: fixture.kickoff,
            )
            fixtures.extend((league_name, fixture) for fixture in upcoming[:2])

        fixtures = fixtures[:10]
        for league_name, fixture in fixtures:
            fixture_id = _canonical_id(
                "fixture",
                league_name,
                fixture.kickoff.isoformat(),
                fixture.home.name,
                fixture.away.name,
            )
            resolver.register(
                entity_type="fixture",
                canonical_id=fixture_id,
                source_name="api-football",
                external_id=fixture.provider_id,
            )
            for team in (fixture.home, fixture.away):
                resolver.register(
                    entity_type="team",
                    canonical_id=_canonical_id("team", team.name),
                    source_name="api-football",
                    external_id=team.provider_id,
                    names=(team.name,),
                )

        for _, fixture in fixtures:
            for endpoint in ("injuries", "lineups"):
                path = "/injuries" if endpoint == "injuries" else "/fixtures/lineups"
                try:
                    raw = await client.get(path, params={"fixture": fixture.provider_id})
                    archive.archive(raw)
                    payload = json.loads(raw.content)
                    if endpoint == "injuries":
                        batch = ApiFootballPlayerAvailabilityProvider._parse_payload(
                            payload,
                            fixture_external_id=fixture.provider_id,
                            captured_at=raw.captured_at,
                        )
                        for record in batch.records:
                            team_match = resolver.resolve(
                                entity_type="team",
                                source_name="api-football",
                                external_id=record.team_external_id,
                            )
                            if team_match.canonical_id is None:
                                mapping_failures.append(
                                    "team:" f"{record.team_external_id}:{team_match.reason_code}"
                                )
                                continue
                            resolver.register(
                                entity_type="player",
                                canonical_id=_canonical_id(
                                    "player",
                                    team_match.canonical_id,
                                    record.player_name or record.player_external_id,
                                ),
                                source_name="api-football",
                                external_id=record.player_external_id,
                                names=(record.player_name,) if record.player_name else (),
                            )
                        observations.extend(
                            adapter.availability(
                                batch,
                                resolver=resolver,
                                capture_window=CaptureWindow.DAILY,
                                raw_payload_hash=raw.sha256,
                                source_url=raw.source_url,
                            )
                        )
                    else:
                        lineup_batch = ApiFootballFixtureLineupProvider._parse_payload(
                            payload,
                            fixture_external_id=fixture.provider_id,
                            captured_at=raw.captured_at,
                        )
                        observations.extend(
                            adapter.lineups(
                                lineup_batch,
                                resolver=resolver,
                                capture_window=CaptureWindow.DAILY,
                                raw_payload_hash=raw.sha256,
                                source_url=raw.source_url,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - Canary records redacted source failures
                    source_errors.append(f"{endpoint}:{fixture.provider_id}:{type(exc).__name__}")
    finally:
        await client.close()

    first = store.append(observations)
    replay = store.append(observations)
    conflicts = resolve_conflicts(observations)
    by_type = Counter(observation.data_type for observation in observations)
    summary: dict[str, Any] = {
        "task_id": "TASK-20261010-085",
        "captured_at": datetime.now(UTC).isoformat(),
        "fixtures": [
            {
                "provider_fixture_id": fixture.provider_id,
                "competition": league,
                "kickoff": fixture.kickoff.isoformat(),
                "home": fixture.home.name,
                "away": fixture.away.name,
            }
            for league, fixture in fixtures
        ],
        "leagues_covered": sorted({league for league, _ in fixtures}),
        "http_requests": client.requests,
        "observations": len(observations),
        "observations_by_type": dict(sorted(by_type.items())),
        "inserted": first.inserted,
        "replay_inserted": replay.inserted,
        "duplicates_on_replay": replay.duplicates,
        "mapping_failures": mapping_failures,
        "source_errors": source_errors,
        "conflicts": sum(item.conflict for item in conflicts),
        "observation_manifest_hash": hashlib.sha256(
            canonical_json(
                {"ids": sorted(observation.observation_id for observation in observations)}
            )
        ).hexdigest(),
        "production_db_reads": 0,
        "production_db_writes": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/codex/TASK-20261010-085_canary"),
    )
    args = parser.parse_args()
    summary = asyncio.run(run(args.output))
    print(
        json.dumps(
            {
                "fixtures": len(summary["fixtures"]),
                "leagues": len(summary["leagues_covered"]),
                "requests": summary["http_requests"],
                "observations": summary["observations"],
                "replay_inserted": summary["replay_inserted"],
                "mapping_failures": len(summary["mapping_failures"]),
                "source_errors": len(summary["source_errors"]),
                "production_db_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
