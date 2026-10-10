"""Strict natural capture-window planning without backdating."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.team_intelligence.contracts import CaptureWindow, utc_datetime

CHECKPOINTS: tuple[tuple[CaptureWindow, timedelta, timedelta], ...] = (
    (CaptureWindow.T24H, timedelta(hours=24), timedelta(minutes=30)),
    (CaptureWindow.T6H, timedelta(hours=6), timedelta(minutes=15)),
    (CaptureWindow.T90, timedelta(minutes=90), timedelta(minutes=10)),
    (CaptureWindow.T60, timedelta(minutes=60), timedelta(minutes=10)),
    (CaptureWindow.T30, timedelta(minutes=30), timedelta(minutes=5)),
    (CaptureWindow.T15, timedelta(minutes=15), timedelta(minutes=5)),
)


def natural_capture_window(*, kickoff: datetime, now: datetime) -> CaptureWindow | None:
    remaining = utc_datetime(kickoff, field_name="kickoff") - utc_datetime(now, field_name="now")
    if remaining < timedelta(0):
        return None
    for window, target, tolerance in CHECKPOINTS:
        if abs(remaining - target) <= tolerance:
            return window
    return None


def collection_windows() -> tuple[CaptureWindow, ...]:
    return (
        CaptureWindow.DAILY,
        CaptureWindow.T24H,
        CaptureWindow.T6H,
        CaptureWindow.T90,
        CaptureWindow.T60,
        CaptureWindow.T30,
        CaptureWindow.T15,
        CaptureWindow.POST_MATCH,
    )
