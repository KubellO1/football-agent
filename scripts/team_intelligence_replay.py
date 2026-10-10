"""Offline replay of a saved team-intelligence Canary raw archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from app.providers.impl.api_football_fixture_lineup_provider import (
    ApiFootballFixtureLineupProvider,
)
from app.providers.impl.api_football_player_availability_provider import (
    ApiFootballPlayerAvailabilityProvider,
)
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.team_intelligence.api_football import ApiFootballTeamIntelligenceAdapter
from app.team_intelligence.conflicts import resolve_conflicts
from app.team_intelligence.contracts import (
    CaptureWindow,
    Observation,
    RawPayload,
    SourcePolicy,
    canonical_json,
)
from app.team_intelligence.matching import IdentityResolver, canonical_name
from app.team_intelligence.storage import ObservationStore

if TYPE_CHECKING:
    from app.providers.schemas.fixtures import ProviderFixture

NAMESPACE = uuid.UUID("bd47a543-2fd8-4ca3-9730-64f7632a7cf4")


def _canonical_id(kind: str, *parts: object) -> str:
    value = ":".join((kind, *(canonical_name(str(part)) for part in parts)))
    return str(uuid.uuid5(NAMESPACE, value))


def replay(output: Path) -> dict[str, Any]:
    original = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    fixture_targets = {str(item["provider_fixture_id"]) for item in original.get("fixtures", [])}
    payloads = _load_payloads(output / "raw" / "api-football")
    fixture_records: dict[str, tuple[str, ProviderFixture]] = {}
    for raw in payloads:
        if urlparse(raw.source_url).path != "/fixtures":
            continue
        document = json.loads(raw.content)
        for item in document["response"]:
            fixture = ApiFootballProvider._parse_fixture(item)
            if fixture.provider_id in fixture_targets:
                fixture_records[fixture.provider_id] = (fixture.league or "Unknown", fixture)

    resolver = IdentityResolver()
    for league, fixture_value in fixture_records.values():
        fixture = fixture_value
        fixture_id = _canonical_id(
            "fixture",
            league,
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

    adapter = ApiFootballTeamIntelligenceAdapter()
    observations: list[Observation] = []
    failures: list[str] = []
    for raw in payloads:
        parsed_url = urlparse(raw.source_url)
        query = parse_qs(parsed_url.query)
        fixture_values = query.get("fixture")
        if not fixture_values:
            continue
        provider_fixture_id = fixture_values[0]
        if provider_fixture_id not in fixture_targets:
            continue
        document = json.loads(raw.content)
        try:
            if parsed_url.path == "/injuries":
                batch = ApiFootballPlayerAvailabilityProvider._parse_payload(
                    document,
                    fixture_external_id=provider_fixture_id,
                    captured_at=raw.captured_at,
                )
                for record in batch.records:
                    team_match = resolver.resolve(
                        entity_type="team",
                        source_name="api-football",
                        external_id=record.team_external_id,
                    )
                    if team_match.canonical_id is None:
                        raise ValueError(f"team mapping failed: {team_match.reason_code}")
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
            elif parsed_url.path == "/fixtures/lineups":
                lineup_batch = ApiFootballFixtureLineupProvider._parse_payload(
                    document,
                    fixture_external_id=provider_fixture_id,
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
        except Exception as exc:  # noqa: BLE001 - redacted offline replay diagnostics
            failures.append(f"{parsed_url.path}:{provider_fixture_id}:{type(exc).__name__}")

    store = ObservationStore(output / "observations")
    first = store.append(observations)
    second = store.append(observations)
    conflicts = resolve_conflicts(observations)
    by_type = Counter(observation.data_type for observation in observations)
    summary: dict[str, Any] = {
        "task_id": "TASK-20261010-085",
        "replayed_at": datetime.now().astimezone().isoformat(),
        "raw_http_requests_replayed": len(payloads),
        "external_requests_during_replay": 0,
        "fixtures": len(fixture_records),
        "leagues": sorted({league for league, _ in fixture_records.values()}),
        "observations": len(observations),
        "observations_by_type": dict(sorted(by_type.items())),
        "first_pass_inserted": first.inserted,
        "first_pass_duplicates": first.duplicates,
        "second_pass_inserted": second.inserted,
        "second_pass_duplicates": second.duplicates,
        "mapping_or_parse_failures": failures,
        "conflicts": sum(item.conflict for item in conflicts),
        "manifest_hash": hashlib.sha256(
            canonical_json(
                {
                    "observation_ids": sorted(
                        observation.observation_id for observation in observations
                    )
                }
            )
        ).hexdigest(),
        "production_db_reads": 0,
        "production_db_writes": 0,
    }
    (output / "replay_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _load_payloads(root: Path) -> tuple[RawPayload, ...]:
    payloads: list[RawPayload] = []
    seen_receipts: set[tuple[str, str]] = set()
    for receipt_path in sorted((root / "receipts").glob("**/*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        identity = (str(receipt["source_url"]), str(receipt["raw_payload_hash"]))
        if identity in seen_receipts:
            continue
        seen_receipts.add(identity)
        digest = identity[1]
        candidates = tuple((root / "payloads" / digest[:2]).glob(f"{digest}.*"))
        if len(candidates) != 1:
            raise ValueError(f"raw payload lookup is ambiguous for {digest}")
        payloads.append(
            RawPayload(
                source_name=str(receipt["source_name"]),
                source_url=str(receipt["source_url"]),
                captured_at=datetime.fromisoformat(str(receipt["captured_at"])),
                content=candidates[0].read_bytes(),
                media_type=str(receipt["media_type"]),
                source_policy=SourcePolicy(str(receipt["source_policy"])),
                response_headers=receipt.get("response_headers") or {},
            )
        )
    return tuple(payloads)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/codex/TASK-20261010-085_canary"),
    )
    args = parser.parse_args()
    summary = replay(args.output)
    print(
        json.dumps(
            {
                "fixtures": summary["fixtures"],
                "leagues": len(summary["leagues"]),
                "observations": summary["observations"],
                "second_pass_inserted": summary["second_pass_inserted"],
                "failures": len(summary["mapping_or_parse_failures"]),
                "external_requests": 0,
                "production_db_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
