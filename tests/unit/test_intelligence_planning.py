from datetime import UTC, datetime, timedelta

from app.intelligence.contracts import CaptureWindow
from app.intelligence.planning import due_capture_window


def test_all_capture_windows() -> None:
    kickoff = datetime(2026, 9, 14, 18, tzinfo=UTC)
    expectations = {
        timedelta(hours=30): CaptureWindow.DAILY,
        timedelta(hours=20): CaptureWindow.T24H,
        timedelta(hours=5): CaptureWindow.T6H,
        timedelta(minutes=82): CaptureWindow.T90,
        timedelta(minutes=47): CaptureWindow.T60,
        timedelta(minutes=18): CaptureWindow.T30,
    }
    for remaining, expected in expectations.items():
        assert due_capture_window(kickoff=kickoff, now=kickoff - remaining) is expected


def test_only_latest_relevant_window_is_returned() -> None:
    kickoff = datetime(2026, 9, 14, 18, tzinfo=UTC)
    result = due_capture_window(
        kickoff=kickoff,
        now=kickoff - timedelta(minutes=18),
        completed={CaptureWindow.T90, CaptureWindow.T60},
    )
    assert result is CaptureWindow.T30


def test_completed_and_past_match_behavior() -> None:
    kickoff = datetime(2026, 9, 14, 18, tzinfo=UTC)
    assert (
        due_capture_window(kickoff=kickoff, now=kickoff + timedelta(hours=2), match_completed=True)
        is CaptureWindow.POST_MATCH
    )
    assert (
        due_capture_window(
            kickoff=kickoff,
            now=kickoff + timedelta(hours=2),
            completed={CaptureWindow.POST_MATCH},
            match_completed=True,
        )
        is None
    )
