"""调度运行器文件状态辅助函数的单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from app.workers import scheduler_runner

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_load_heartbeat_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    heartbeat_file = tmp_path / "heartbeat.json"
    heartbeat_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(scheduler_runner, "HEARTBEAT_FILE", heartbeat_file)

    assert scheduler_runner._load_heartbeat() == {}


@pytest.mark.unit
def test_load_heartbeat_removes_only_stale_health_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    heartbeat_file = tmp_path / "heartbeat.json"
    now = datetime.now(scheduler_runner.PARIS)
    heartbeat_file.write_text(
        json.dumps(
            {
                "health_check": {
                    "last_start": (now - timedelta(hours=25)).isoformat(),
                    "status": "failed",
                },
                "provider_health": {
                    "last_start": now.isoformat(),
                    "status": "success",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_runner, "HEARTBEAT_FILE", heartbeat_file)

    result = scheduler_runner._load_heartbeat()

    assert "health_check" not in result
    assert result["provider_health"] == {
        "last_start": now.isoformat(),
        "status": "success",
    }
    assert json.loads(heartbeat_file.read_text(encoding="utf-8")) == result


@pytest.mark.unit
def test_release_lock_ignores_missing_file(tmp_path: Path) -> None:
    scheduler_runner.release_lock(str(tmp_path / "missing.lock"))


@pytest.mark.unit
def test_append_run_timeline_keeps_only_object_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    timeline_file = tmp_path / "run_timeline.json"
    timeline_file.write_text(
        json.dumps([{"task": "existing"}, "invalid", 7]),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_runner, "RUN_TIMELINE_FILE", timeline_file)

    scheduler_runner._append_run_timeline(
        "daily_job",
        "success",
        details="completed",
        duration_s=1.2345,
    )

    stored = json.loads(timeline_file.read_text(encoding="utf-8"))
    assert stored[0] == {"task": "existing"}
    assert stored[1] == {
        "time": stored[1]["time"],
        "task": "daily_job",
        "status": "success",
        "duration_s": 1.234,
        "details": "completed",
    }
    assert len(stored) == 2
