"""No-network contract tests against the providers' documented REST envelope."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.impl.pinnacle_free_normalization import parse_prematch_board

CAPTURED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _board(**overrides: object) -> bytes:
    event: dict[str, object] = {
        "event_id": 123456,
        "sport_id": 1,
        "league_name": "Spain - La Liga",
        "home": "Alaves",
        "away": "Villarreal",
        "starts": "2026-09-22T19:30:00Z",
        "event_type": "prematch",
        "last": 1780000000,
        "periods": {"num_0": {"money_line": {"home": 2.45, "draw": 3.3, "away": 2.9}}},
    }
    event.update(overrides)
    return json.dumps({"last": 100, "events": [event]}).encode()


@pytest.mark.parametrize("source", ["pinnwire", "pinnodds"])
def test_documented_1x2_contract_and_single_bookmaker(source: str) -> None:
    result = parse_prematch_board(_board(), source=source, captured_at=CAPTURED_AT)  # type: ignore[arg-type]
    assert result.rejected == ()
    assert [(q.selection, q.decimal_odds) for q in result.quotes] == [
        ("home", Decimal("2.45")),
        ("draw", Decimal("3.3")),
        ("away", Decimal("2.9")),
    ]
    assert {q.bookmaker_key for q in result.quotes} == {"pinnacle"}
    assert {q.source for q in result.quotes} == {source}
    assert {q.provider_event_id for q in result.quotes} == {"123456"}
    assert {q.captured_at for q in result.quotes} == {CAPTURED_AT}
    assert {q.source_timestamp for q in result.quotes} == {None}
    assert len({q.raw_payload_hash for q in result.quotes}) == 1
    assert all(len(q.raw_payload_hash) == 64 for q in result.quotes)


def test_two_feeds_do_not_create_two_bookmaker_identities() -> None:
    first = parse_prematch_board(_board(), source="pinnwire", captured_at=CAPTURED_AT)
    second = parse_prematch_board(_board(), source="pinnodds", captured_at=CAPTURED_AT)
    assert {q.source for q in (*first.quotes, *second.quotes)} == {"pinnwire", "pinnodds"}
    assert {q.bookmaker_key for q in (*first.quotes, *second.quotes)} == {"pinnacle"}


def test_replay_is_deterministic() -> None:
    raw = _board()
    a = parse_prematch_board(raw, source="pinnwire", captured_at=CAPTURED_AT)
    b = parse_prematch_board(raw, source="pinnwire", captured_at=CAPTURED_AT)
    assert a == b


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"sport_id": 2}, "NOT_PREMATCH_SOCCER"),
        ({"event_type": "live"}, "NOT_PREMATCH_SOCCER"),
        ({"event_id": None}, "MISSING_EVENT_IDENTITY"),
        ({"starts": "not-a-time"}, "INVALID_KICKOFF"),
        ({"periods": {"num_1": {"money_line": {"home": 2}}}}, "NO_FULL_TIME_1X2"),
        ({"periods": {"num_0": {"money_line": {"home": 2, "away": 3}}}}, "INCOMPLETE_1X2"),
        (
            {"periods": {"num_0": {"money_line": {"home": 0, "draw": 3, "away": 2}}}},
            "INCOMPLETE_1X2",
        ),
    ],
)
def test_unsafe_or_incomplete_events_are_rejected(
    overrides: dict[str, object], reason: str
) -> None:
    result = parse_prematch_board(_board(**overrides), source="pinnwire", captured_at=CAPTURED_AT)
    assert result.quotes == ()
    assert result.rejected[0].reason_code == reason


def test_naive_capture_time_fails_closed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_prematch_board(_board(), source="pinnwire", captured_at=datetime(2026, 9, 20, 12, 0))
