from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.team_intelligence.conflicts import resolve_conflicts
from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    RawPayload,
    SourcePolicy,
)
from app.team_intelligence.storage import ContentAddressedArchive, ObservationStore, is_stale

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _observation(
    *,
    evidence: EvidenceLevel = EvidenceLevel.UNVERIFIED,
    source: str = "api-football",
    status: str = "missing",
    payload_hash: str | None = None,
) -> Observation:
    return Observation(
        team_id="team-1",
        player_id="player-1",
        fixture_id="fixture-1",
        source_name=source,
        source_url="https://example.test/news/1",
        published_at=NOW - timedelta(minutes=10),
        captured_at=NOW,
        original_title="Availability update",
        original_text="Player unavailable",
        original_quote=None,
        language="en",
        parser_version="test-v1",
        raw_payload_hash=payload_hash or hashlib.sha256(status.encode()).hexdigest(),
        data_type=DataType.INJURY,
        evidence_level=evidence,
        source_policy=SourcePolicy.EXISTING_LICENSED_API,
        capture_window=CaptureWindow.T60,
        normalized={"status": status},
        provider_team_id="100",
        provider_player_id="200",
        provider_fixture_id="300",
    )


def test_observation_identity_is_replay_stable_but_window_specific() -> None:
    first = _observation()
    replay = _observation()
    later_window = replace(first, capture_window=CaptureWindow.T30)

    assert first.observation_id == replay.observation_id
    assert first.observation_id != later_window.observation_id


def test_append_only_store_rejects_replay_without_overwrite(tmp_path) -> None:
    store = ObservationStore(tmp_path / "observations")
    observation = _observation()

    assert store.append((observation,)).inserted == 1
    replay = store.append((observation,))

    assert replay.inserted == 0
    assert replay.duplicates == 1
    assert store.count() == 1


def test_content_archive_preserves_payload_and_distinct_capture_receipts(tmp_path) -> None:
    archive = ContentAddressedArchive(tmp_path / "raw")
    first = RawPayload(
        source_name="official-source",
        source_url="https://example.test/news",
        captured_at=NOW,
        content=b'{"status":"available"}',
        media_type="application/json",
        source_policy=SourcePolicy.PUBLIC_WEB_AUTOMATION_ALLOWED,
    )
    second = RawPayload(
        source_name=first.source_name,
        source_url=first.source_url,
        captured_at=NOW + timedelta(minutes=30),
        content=first.content,
        media_type=first.media_type,
        source_policy=first.source_policy,
    )

    one = archive.archive(first)
    two = archive.archive(second)

    assert one.payload_created is True
    assert two.payload_created is False
    assert one.receipt_created is True
    assert two.receipt_created is True
    assert one.receipt_path != two.receipt_path


def test_conflicting_evidence_is_preserved_and_official_medical_wins() -> None:
    provider = _observation(status="missing")
    official = _observation(
        evidence=EvidenceLevel.OFFICIAL_MEDICAL_REPORT,
        source="club-medical",
        status="returned-to-training",
    )

    (resolution,) = resolve_conflicts((provider, official))

    assert resolution.conflict is True
    assert set(resolution.observation_ids) == {
        provider.observation_id,
        official.observation_id,
    }
    assert resolution.preferred_observation_id == official.observation_id


def test_stale_detection_uses_data_type_contract() -> None:
    observation = _observation()
    assert is_stale(observation, now=NOW + timedelta(hours=5)) is False
    assert is_stale(observation, now=NOW + timedelta(hours=7)) is True
