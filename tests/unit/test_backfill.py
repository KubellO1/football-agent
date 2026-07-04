"""回填纯逻辑单元测试：日期区间迭代与断点续跑起点。"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.backfill import completed_pairs, date_range, resume_start


@pytest.mark.unit
def test_date_range_inclusive() -> None:
    days = list(date_range(date(2026, 1, 1), date(2026, 1, 3)))
    assert days == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


@pytest.mark.unit
def test_date_range_single_day() -> None:
    assert list(date_range(date(2026, 1, 1), date(2026, 1, 1))) == [date(2026, 1, 1)]


@pytest.mark.unit
def test_date_range_empty_when_reversed() -> None:
    assert list(date_range(date(2026, 1, 3), date(2026, 1, 1))) == []


@pytest.mark.unit
def test_resume_start_no_checkpoint() -> None:
    assert resume_start(date(2026, 1, 1), date(2026, 1, 5), None) == date(2026, 1, 1)


@pytest.mark.unit
def test_resume_start_matching_checkpoint_continues_next_day() -> None:
    cp = {"from": "2026-01-01", "to": "2026-01-05", "last_completed": "2026-01-03"}
    assert resume_start(date(2026, 1, 1), date(2026, 1, 5), cp) == date(2026, 1, 4)


@pytest.mark.unit
def test_resume_start_ignores_checkpoint_of_different_range() -> None:
    cp = {"from": "2020-01-01", "to": "2020-12-31", "last_completed": "2020-06-01"}
    assert resume_start(date(2026, 1, 1), date(2026, 1, 5), cp) == date(2026, 1, 1)


@pytest.mark.unit
def test_resume_start_past_end_yields_empty_range() -> None:
    cp = {"from": "2026-01-01", "to": "2026-01-05", "last_completed": "2026-01-05"}
    start = resume_start(date(2026, 1, 1), date(2026, 1, 5), cp)
    assert start == date(2026, 1, 6)
    assert list(date_range(start, date(2026, 1, 5))) == []


@pytest.mark.unit
def test_completed_pairs_none() -> None:
    assert completed_pairs([39, 140], [2024, 2025], None) == set()


@pytest.mark.unit
def test_completed_pairs_matching_config() -> None:
    cp = {"leagues": [39, 140], "seasons": [2024, 2025], "completed": ["39:2024", "39:2025"]}
    assert completed_pairs([39, 140], [2024, 2025], cp) == {"39:2024", "39:2025"}


@pytest.mark.unit
def test_completed_pairs_ignored_on_config_mismatch() -> None:
    cp = {"leagues": [39], "seasons": [2024], "completed": ["39:2024"]}
    assert completed_pairs([39, 140], [2024, 2025], cp) == set()
