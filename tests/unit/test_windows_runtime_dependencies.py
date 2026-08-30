"""Windows runtime dependency contracts."""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import version
from zoneinfo import ZoneInfo


def test_europe_paris_timezone_and_scheduler_runner_are_importable() -> None:
    timezone = ZoneInfo("Europe/Paris")
    scheduler_runner = importlib.import_module("app.workers.scheduler_runner")

    assert timezone.key == "Europe/Paris"
    assert scheduler_runner.PARIS.key == "Europe/Paris"


def test_windows_runtime_installs_declared_tzdata_distribution() -> None:
    if sys.platform == "win32":
        assert version("tzdata")
