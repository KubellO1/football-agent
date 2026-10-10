"""Normalize existing API-Football availability and lineup DTOs as evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    SourcePolicy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from app.providers.schemas.fixture_lineup import ProviderFixtureLineupBatch
    from app.providers.schemas.player_availability import ProviderAvailabilityBatch
    from app.team_intelligence.matching import IdentityResolver


class MappingFailure(ValueError):
    pass


class ApiFootballTeamIntelligenceAdapter:
    parser_version = "api-football-team-intelligence-v1"

    def availability(
        self,
        batch: ProviderAvailabilityBatch,
        *,
        resolver: IdentityResolver,
        capture_window: CaptureWindow,
        raw_payload_hash: str,
        source_url: str,
    ) -> tuple[Observation, ...]:
        fixture_id = _required_resolution(
            resolver.resolve(
                entity_type="fixture",
                source_name=batch.source,
                external_id=batch.fixture_external_id,
            ),
            entity="fixture",
        )
        observations: list[Observation] = []
        for record in batch.records:
            team_id = _required_resolution(
                resolver.resolve(
                    entity_type="team",
                    source_name=batch.source,
                    external_id=record.team_external_id,
                ),
                entity="team",
            )
            player_id = _required_resolution(
                resolver.resolve(
                    entity_type="player",
                    source_name=batch.source,
                    external_id=record.player_external_id,
                    name=record.player_name,
                ),
                entity="player",
            )
            data_type = _availability_type(record.raw_status, record.reason)
            normalized: dict[str, object] = {
                "availability_status": record.raw_status,
                "reason": record.reason,
                "expected_return": (
                    record.expected_return.isoformat() if record.expected_return else None
                ),
                "response_complete": batch.response_complete,
            }
            observations.append(
                self._observation(
                    fixture_id=fixture_id,
                    team_id=team_id,
                    player_id=player_id,
                    source_name=batch.source,
                    source_url=source_url,
                    captured_at=batch.captured_at,
                    published_at=record.source_updated_at,
                    data_type=data_type,
                    capture_window=capture_window,
                    original_text=record.reason or record.raw_status,
                    normalized=normalized,
                    raw_payload_hash=raw_payload_hash,
                    provider_fixture_id=batch.fixture_external_id,
                    provider_team_id=record.team_external_id,
                    provider_player_id=record.player_external_id,
                )
            )
        return tuple(observations)

    def lineups(
        self,
        batch: ProviderFixtureLineupBatch,
        *,
        resolver: IdentityResolver,
        capture_window: CaptureWindow,
        raw_payload_hash: str,
        source_url: str,
    ) -> tuple[Observation, ...]:
        fixture_id = _required_resolution(
            resolver.resolve(
                entity_type="fixture",
                source_name=batch.source,
                external_id=batch.fixture_external_id,
            ),
            entity="fixture",
        )
        observations: list[Observation] = []
        for lineup in batch.lineups:
            team_id = _required_resolution(
                resolver.resolve(
                    entity_type="team",
                    source_name=batch.source,
                    external_id=lineup.team_external_id,
                ),
                entity="team",
            )
            starting = [
                {
                    "player_external_id": player.player_external_id,
                    "name": player.player_name,
                    "position": player.raw_position,
                    "number": player.shirt_number,
                    "grid": player.grid_position,
                }
                for player in lineup.starting
            ]
            substitutes = [
                {
                    "player_external_id": player.player_external_id,
                    "name": player.player_name,
                    "position": player.raw_position,
                    "number": player.shirt_number,
                }
                for player in lineup.substitutes
            ]
            common = {
                "response_complete": batch.response_complete,
                "formation": lineup.formation,
            }
            observations.extend(
                (
                    self._observation(
                        fixture_id=fixture_id,
                        team_id=team_id,
                        player_id=None,
                        source_name=batch.source,
                        source_url=source_url,
                        captured_at=batch.captured_at,
                        published_at=None,
                        data_type=DataType.CONFIRMED_LINEUP,
                        capture_window=capture_window,
                        original_text="Confirmed starting XI",
                        normalized={**common, "players": starting},
                        raw_payload_hash=raw_payload_hash,
                        provider_fixture_id=batch.fixture_external_id,
                        provider_team_id=lineup.team_external_id,
                    ),
                    self._observation(
                        fixture_id=fixture_id,
                        team_id=team_id,
                        player_id=None,
                        source_name=batch.source,
                        source_url=source_url,
                        captured_at=batch.captured_at,
                        published_at=None,
                        data_type=DataType.SUBSTITUTES,
                        capture_window=capture_window,
                        original_text="Confirmed substitutes",
                        normalized={**common, "players": substitutes},
                        raw_payload_hash=raw_payload_hash,
                        provider_fixture_id=batch.fixture_external_id,
                        provider_team_id=lineup.team_external_id,
                    ),
                    self._observation(
                        fixture_id=fixture_id,
                        team_id=team_id,
                        player_id=None,
                        source_name=batch.source,
                        source_url=source_url,
                        captured_at=batch.captured_at,
                        published_at=None,
                        data_type=DataType.FORMATION,
                        capture_window=capture_window,
                        original_text=lineup.formation or "Formation unavailable",
                        normalized=common,
                        raw_payload_hash=raw_payload_hash,
                        provider_fixture_id=batch.fixture_external_id,
                        provider_team_id=lineup.team_external_id,
                    ),
                )
            )
        return tuple(observations)

    def _observation(
        self,
        *,
        fixture_id: str,
        team_id: str,
        player_id: str | None,
        source_name: str,
        source_url: str,
        captured_at: datetime,
        published_at: datetime | None,
        data_type: DataType,
        capture_window: CaptureWindow,
        original_text: str,
        normalized: Mapping[str, object],
        raw_payload_hash: str,
        provider_fixture_id: str,
        provider_team_id: str,
        provider_player_id: str | None = None,
    ) -> Observation:
        return Observation(
            team_id=team_id,
            player_id=player_id,
            fixture_id=fixture_id,
            source_name=source_name,
            source_url=source_url,
            published_at=published_at,
            captured_at=captured_at,
            original_title=None,
            original_text=original_text,
            original_quote=None,
            language="en",
            parser_version=self.parser_version,
            raw_payload_hash=raw_payload_hash,
            data_type=data_type,
            evidence_level=EvidenceLevel.UNVERIFIED,
            source_policy=SourcePolicy.EXISTING_LICENSED_API,
            capture_window=capture_window,
            normalized=normalized,
            provider_team_id=provider_team_id,
            provider_player_id=provider_player_id,
            provider_fixture_id=provider_fixture_id,
        )


def _availability_type(raw_status: str, reason: str | None) -> DataType:
    combined = f"{raw_status} {reason or ''}".casefold()
    if "suspend" in combined:
        return DataType.SUSPENSION
    if "illness" in combined or "sick" in combined:
        return DataType.ILLNESS
    return DataType.INJURY


def _required_resolution(result: object, *, entity: str) -> str:
    canonical_id = getattr(result, "canonical_id", None)
    reason_code = getattr(result, "reason_code", "UNKNOWN")
    if not isinstance(canonical_id, str) or not canonical_id:
        raise MappingFailure(f"{entity} mapping failed: {reason_code}")
    return canonical_id
