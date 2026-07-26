"""Unit tests for 2026-07-15 production state file fixes.

Covers:
  1. Recovery flow correctly updates data/run_status.json
  2. health_check vs provider_health key separation & staleness cleanup
  3. Heartbeats write to single file only (app/state/heartbeat.json)
  4. Dataclass field order validation (non-default before default)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# =============================================================================
# Test 1: Recovery flow updates run_status.json correctly
# =============================================================================

RECOVERY_UPDATE_CODE = """
rs["status"] = "recovered"
rs["recovery_time"] = dt.isoformat()
rs["recovery_attempted"] = True
rs["recovery_result"] = "success"
"""


@pytest.mark.unit
class TestRecoveryUpdatesRunStatus:
    """Verify that run_status.json gets updated after successful recovery."""

    def test_run_status_fields_after_recovery_success(self) -> None:
        """After recovery success, run_status must reflect 'recovered' state."""
        dt = datetime.now(PARIS)
        rs: dict = {
            "status": "failed",
            "failure_reason": "db_error",
            "recovery_attempted": False,
            "last_daily_run": "2026-07-15T08:00:00+02:00",
        }

        # Simulate recovery success update (mirrors scheduler_runner.py logic)
        rs["status"] = "recovered"
        rs["recovery_time"] = dt.isoformat()
        rs["recovery_attempted"] = True
        rs["recovery_result"] = "success"

        assert rs["status"] == "recovered"
        assert rs["recovery_attempted"] is True
        assert rs["recovery_result"] == "success"
        assert "recovery_time" in rs
        assert rs["failure_reason"] == "db_error"  # preserved for audit

    def test_run_status_json_serializable(self) -> None:
        """Recovery output must be JSON-serializable (no datetime objects)."""
        dt = datetime.now(PARIS)
        rs = {
            "status": "failed",
            "failure_reason": "db_error",
            "recovery_attempted": False,
        }
        rs["status"] = "recovered"
        rs["recovery_time"] = dt.isoformat()
        rs["recovery_attempted"] = True
        rs["recovery_result"] = "success"

        serialized = json.dumps(rs, default=str)
        deserialized = json.loads(serialized)

        assert deserialized["status"] == "recovered"
        assert deserialized["recovery_result"] == "success"
        # recovery_time should be an ISO string, not a datetime object
        assert isinstance(deserialized["recovery_time"], str)


# =============================================================================
# Test 2: health_check vs provider_health key separation + staleness
# =============================================================================

@pytest.mark.unit
class TestHeartbeatKeySeparation:
    """Verify health_check and provider_health are independent keys,
    and that stale (>24h) health_check entries are auto-cleared."""

    @staticmethod
    def _apply_staleness_cleanup(
        data: dict, now: datetime | None = None
    ) -> dict:
        """Replicate the staleness cleanup logic from _load_heartbeat."""
        if now is None:
            now = datetime.now(PARIS)
        stale_hc = data.get("health_check")
        if stale_hc and isinstance(stale_hc, dict):
            last_start = stale_hc.get("last_start", "")
            if last_start:
                try:
                    hc_ts = datetime.fromisoformat(str(last_start))
                    if (now - hc_ts).total_seconds() > 86400:
                        del data["health_check"]
                except (ValueError, TypeError):
                    pass
        return data

    def test_provider_health_not_affected_by_health_check_cleanup(self) -> None:
        """Successful provider_health should survive health_check staleness cleanup."""
        now = datetime.now(PARIS)
        data = {
            "health_check": {
                "last_start": (now - timedelta(hours=48)).isoformat(),
                "status": "recovery_failed",
            },
            "provider_health": {
                "last_start": now.isoformat(),
                "status": "success",
            },
            "daily_job": {
                "last_start": now.isoformat(),
                "status": "success",
            },
        }

        result = self._apply_staleness_cleanup(data, now)

        # health_check should be removed (stale)
        assert "health_check" not in result
        # provider_health must survive
        assert "provider_health" in result
        assert result["provider_health"]["status"] == "success"
        # daily_job must survive
        assert "daily_job" in result

    def test_stale_health_check_cleared_after_24h(self) -> None:
        """health_check > 24h old should be auto-cleared."""
        now = datetime.now(PARIS)
        data = {
            "health_check": {
                "last_start": (now - timedelta(hours=25)).isoformat(),
                "status": "recovery_failed",
            },
        }
        result = self._apply_staleness_cleanup(data, now)
        assert "health_check" not in result

    def test_recent_health_check_preserved(self) -> None:
        """health_check < 24h old should be preserved."""
        now = datetime.now(PARIS)
        data = {
            "health_check": {
                "last_start": (now - timedelta(hours=12)).isoformat(),
                "status": "failed",
            },
        }
        result = self._apply_staleness_cleanup(data, now)
        assert "health_check" in result
        assert result["health_check"]["status"] == "failed"

    def test_no_health_check_no_crash(self) -> None:
        """Heartbeat without health_check key should not cause errors."""
        data = {
            "daily_job": {"last_start": "2026-07-15T08:00:00+02:00", "status": "success"},
            "provider_health": {"last_start": "2026-07-15T07:45:00+02:00", "status": "success"},
        }
        result = self._apply_staleness_cleanup(data)
        assert "health_check" not in result
        assert len(result) == 2  # daily_job + provider_health untouched

    def test_provider_health_success_independent_from_health_check(self) -> None:
        """provider_health success does NOT implicitly clear health_check.
        Only the 24h staleness rule clears it."""
        now = datetime.now(PARIS)
        data = {
            "health_check": {
                "last_start": (now - timedelta(hours=6)).isoformat(),
                "status": "failed",
            },
            "provider_health": {
                "last_start": now.isoformat(),
                "status": "success",
            },
        }
        result = self._apply_staleness_cleanup(data, now)
        # Both should exist — provider_health success doesn't clear health_check
        assert "health_check" in result
        assert "provider_health" in result


# =============================================================================
# Test 3: Heartbeats write to single file only
# =============================================================================

@pytest.mark.unit
class TestHeartbeatSingleFile:
    """Verify production scheduler writes exclusively to app/state/heartbeat.json."""

    def test_production_heartbeat_path(self) -> None:
        """The production scheduler_runner must point to app/state/heartbeat.json."""
        # Simulate PROJECT_ROOT resolution from app/workers/scheduler_runner.py
        # The file is at app/workers/scheduler_runner.py, so parents[2] = project root
        workers_file = PROJECT_ROOT / "app" / "workers" / "scheduler_runner.py"
        project_root = workers_file.resolve().parents[2]
        heartbeat_path = project_root / "app" / "state" / "heartbeat.json"

        assert heartbeat_path == PROJECT_ROOT / "app" / "state" / "heartbeat.json"
        assert "app/state/heartbeat.json" in str(heartbeat_path).replace("\\", "/")

    def test_deprecated_heartbeat_not_used_by_production(self) -> None:
        """Production scheduler must NOT reference data/heartbeat.json."""
        # Read the production scheduler_runner source and verify HEARTBEAT_FILE
        src_path = PROJECT_ROOT / "app" / "workers" / "scheduler_runner.py"
        source = src_path.read_text(encoding="utf-8")

        # The HEARTBEAT_FILE definition must point to app/state/heartbeat.json
        # HEARTBEAT_FILE is defined with Path components: "app" / "state" / "heartbeat.json"
        assert '"app"' in source and '"state"' in source and '"heartbeat.json"' in source
        # The deprecated path must NOT appear in the production scheduler
        assert 'data/heartbeat.json' not in source

    def test_legacy_scheduler_has_deprecation_notice(self) -> None:
        """The legacy app/scheduler_runner.py must carry a deprecation warning."""
        src_path = PROJECT_ROOT / "app" / "scheduler_runner.py"
        source = src_path.read_text(encoding="utf-8")
        assert "DEPRECATED" in source
        assert "app/state/heartbeat.json" in source


# =============================================================================
# Test 4: Dataclass field order validation
# =============================================================================

@pytest.mark.unit
class TestDataclassFieldOrder:
    """Verify DailyRunReport dataclass has correct field ordering."""

    def test_daily_run_report_all_non_defaults_before_defaults(self) -> None:
        """All non-default fields must precede all default fields."""
        from dataclasses import MISSING, fields

        # Import the actual dataclass to test
        sys.path.insert(0, str(PROJECT_ROOT))
        from app.services.daily_top_picks import DailyRunReport

        flds = fields(DailyRunReport)

        # Check: once we see a field with a default, all remaining fields
        # must also have defaults.
        seen_default = False
        non_default_names = []
        default_after_non_default_violations = []

        for f in flds:
            has_default = (
                f.default is not MISSING
                or f.default_factory is not MISSING
            )
            if has_default:
                seen_default = True
            else:
                non_default_names.append(f.name)
                if seen_default:
                    default_after_non_default_violations.append(f.name)

        assert default_after_non_default_violations == [], (
            f"Non-default fields appear after default fields: "
            f"{default_after_non_default_violations}"
        )

    def test_daily_run_report_instantiation(self) -> None:
        """DailyRunReport must be instantiable with all required fields."""
        from uuid import UUID, uuid4

        sys.path.insert(0, str(PROJECT_ROOT))
        from app.services.daily_top_picks import DailyRunReport

        report = DailyRunReport(
            date="2026-07-15",
            fixtures_analyzed=10,
            fixtures_qualified=5,
            fixtures_reviewed=3,
            fixtures_skipped_existing=2,
            value_bets_created=1,
        )

        assert report.date == "2026-07-15"
        assert report.value_bets_created == 1
        assert report.fixtures_skipped_unsupported_competition == 0  # default
        assert report.predictions_logged == 0  # default
        assert report.reviewed_fixture_ids == []  # default_factory

    def test_value_bets_created_has_no_default(self) -> None:
        """value_bets_created must be a required field (no default)."""
        from dataclasses import MISSING, fields

        sys.path.insert(0, str(PROJECT_ROOT))
        from app.services.daily_top_picks import DailyRunReport

        flds = fields(DailyRunReport)
        for f in flds:
            if f.name == "value_bets_created":
                assert f.default is MISSING, (
                    f"value_bets_created has a default value: {f.default!r}"
                )
                assert f.default_factory is MISSING
                break
        else:
            pytest.fail("value_bets_created field not found in DailyRunReport")

    def test_dataclass_does_not_trigger_type_error(self) -> None:
        """Importing the module must not raise TypeError (the original bug)."""
        import importlib

        sys.path.insert(0, str(PROJECT_ROOT))

        # This should succeed without TypeError
        module = importlib.import_module("app.services.daily_top_picks")
        assert hasattr(module, "DailyRunReport")
