"""调度器纯逻辑单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.workers.scheduler import next_run_at, parse_schedule_time


@pytest.mark.unit
def test_parse_schedule_time() -> None:
    assert parse_schedule_time("06:00") == (6, 0)
    assert parse_schedule_time("23:59") == (23, 59)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["24:00", "06:60", "abc", "6"])
def test_parse_schedule_time_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule_time(bad)


@pytest.mark.unit
def test_next_run_today_when_time_still_ahead() -> None:
    now = datetime(2026, 7, 2, 5, 0, tzinfo=UTC)
    assert next_run_at(now, 6, 0) == datetime(2026, 7, 2, 6, 0, tzinfo=UTC)


@pytest.mark.unit
def test_next_run_tomorrow_when_time_passed() -> None:
    now = datetime(2026, 7, 2, 7, 0, tzinfo=UTC)
    assert next_run_at(now, 6, 0) == datetime(2026, 7, 3, 6, 0, tzinfo=UTC)


@pytest.mark.unit
def test_next_run_tomorrow_when_exactly_now() -> None:
    # 恰好等于目标时刻也应顺延到明天（严格晚于 now）
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    assert next_run_at(now, 6, 0) == datetime(2026, 7, 3, 6, 0, tzinfo=UTC)
