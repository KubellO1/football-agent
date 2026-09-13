import json
from datetime import UTC, datetime, timedelta

from app.intelligence.collection import (
    ContentAddressedArchive,
    JsonlObservationStore,
    SourceHealthTracker,
    is_stale,
)
from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    RawPayload,
    SourcePolicy,
)
from app.intelligence.matching import FixtureIdentity, FixtureMatcher


def _observation(*, captured_at: datetime) -> Observation:
    return Observation(
        fixture_id="fixture-1",
        source="source",
        source_url="https://example.test/data",
        published_at=None,
        captured_at=captured_at,
        raw_payload_hash="a" * 64,
        parser_version="v1",
        data_type=DataType.FIXTURE,
        source_policy=SourcePolicy.OPEN_DATA,
        capture_window=CaptureWindow.DAILY,
        payload={"home": "A", "away": "B"},
    )


def test_content_archive_and_store_are_idempotent(tmp_path) -> None:
    raw = RawPayload(
        source="open",
        source_url="https://example.test/data",
        captured_at=datetime(2026, 9, 13, tzinfo=UTC),
        content=b'{"ok":true}',
        media_type="application/json",
        source_policy=SourcePolicy.OPEN_DATA,
    )
    archive = ContentAddressedArchive(tmp_path / "raw")
    first, second = archive.archive(raw), archive.archive(raw)
    assert first.created is True and second.created is False
    store = JsonlObservationStore(tmp_path / "observations.jsonl")
    observation = _observation(captured_at=raw.captured_at)
    assert store.append([observation]).inserted == 1
    repeated = store.append([observation])
    assert (repeated.inserted, repeated.duplicates) == (0, 1)


def test_content_archive_deduplicates_same_hash_across_media_types(tmp_path) -> None:
    captured_at = datetime(2026, 9, 13, tzinfo=UTC)
    common = {
        "source": "open",
        "source_url": "https://example.test/data",
        "captured_at": captured_at,
        "content": b'{"ok":true}',
        "source_policy": SourcePolicy.OPEN_DATA,
    }
    archive = ContentAddressedArchive(tmp_path / "raw")
    first = archive.archive(RawPayload(media_type="text/plain", **common))
    second = archive.archive(RawPayload(media_type="application/json", **common))
    assert first.created is True
    assert second.created is False
    assert second.payload_path == first.payload_path


def test_observation_contains_mandatory_provenance() -> None:
    payload = _observation(captured_at=datetime(2026, 9, 13, tzinfo=UTC)).to_dict()
    mandatory = {
        "fixture_id",
        "source",
        "source_url",
        "published_at",
        "captured_at",
        "raw_payload_hash",
        "parser_version",
        "data_type",
        "source_policy",
    }
    assert mandatory <= payload.keys()
    json.dumps(payload)


def test_health_and_staleness_are_auditable() -> None:
    now = datetime(2026, 9, 14, 1, tzinfo=UTC)
    tracker = SourceHealthTracker()
    tracker.record("open", success=True, empty=False, captured_at=now - timedelta(hours=1))
    tracker.record("open", success=False, empty=False, captured_at=now, error_code="HTTP_503")
    health = tracker.snapshot("open")
    assert (health.attempts, health.successes, health.failures) == (2, 1, 1)
    assert health.last_error_code == "HTTP_503"
    assert is_stale(_observation(captured_at=now - timedelta(hours=25)), now=now)


def test_fixture_matcher_is_conservative() -> None:
    kickoff = datetime(2026, 9, 20, 14, tzinfo=UTC)
    stored = FixtureIdentity("fixture-1", "Ligue 1", kickoff, "Paris SG", "Lille")
    candidate = FixtureIdentity("source-1", "Ligue 1", kickoff, "Paris SG", "Lille")
    assert FixtureMatcher().match(candidate, (stored,)).fixture_id == "fixture-1"
    assert (
        FixtureMatcher().match(candidate, (stored, stored)).reason_code == "AMBIGUOUS_FIXTURE_MATCH"
    )
    alias = FixtureIdentity("source-2", "Ligue 1", kickoff, "PSG", "Lille")
    assert FixtureMatcher().match(alias, (stored,)).reason_code == "NO_EXACT_FIXTURE_MATCH"
