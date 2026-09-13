"""Point-in-time 采集窗口规划。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.intelligence.contracts import CaptureWindow, utc_datetime

if TYPE_CHECKING:
    from collections.abc import Collection


def due_capture_window(
    *,
    kickoff: datetime,
    now: datetime,
    completed: Collection[CaptureWindow] = (),
    match_completed: bool = False,
) -> CaptureWindow | None:
    """返回当前最新且尚未完成的采集窗口。"""

    remaining = utc_datetime(kickoff, field="kickoff") - utc_datetime(now, field="now")
    if match_completed:
        return None if CaptureWindow.POST_MATCH in completed else CaptureWindow.POST_MATCH
    if remaining < timedelta(0):
        return None
    ordered = (
        (timedelta(minutes=30), CaptureWindow.T30),
        (timedelta(minutes=60), CaptureWindow.T60),
        (timedelta(minutes=90), CaptureWindow.T90),
        (timedelta(hours=6), CaptureWindow.T6H),
        (timedelta(hours=24), CaptureWindow.T24H),
    )
    for threshold, window in ordered:
        if remaining <= threshold and window not in completed:
            return window
    return CaptureWindow.DAILY if CaptureWindow.DAILY not in completed else None
