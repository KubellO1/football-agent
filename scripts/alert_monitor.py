"""
Daily alert monitor for football-agent.

Checks critical conditions and outputs structured alerts.

Usage:
    python scripts/alert_monitor.py
    python -m app.workers.scheduler_runner --command alert_monitor

Outputs:
    output/alerts_YYYY-MM-DD.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

HEARTBEAT_FILE = PROJECT_ROOT / "app" / "state" / "heartbeat.json"
OUTPUT_DIR = PROJECT_ROOT / "output"

BIG5_LEAGUE_IDS = [39, 140, 135, 61, 78]  # PL, La Liga, Serie A, Ligue 1, Bundesliga


async def check_heartbeat_stale() -> dict | None:
    """Alert if heartbeat >30min stale."""
    if not HEARTBEAT_FILE.exists():
        return {
            "alert": "Heartbeat file missing",
            "severity": "Error",
            "detail": "heartbeat.json not found — scheduler may not be running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        latest_ts = None
        for entry in data.values():
            if isinstance(entry, dict):
                end_str = entry.get("last_end", "")
                if end_str:
                    try:
                        dt = datetime.fromisoformat(end_str)
                        if latest_ts is None or dt > latest_ts:
                            latest_ts = dt
                    except (ValueError, TypeError):
                        pass

        if latest_ts is None:
            return None

        age_s = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        if age_s > 1800:
            return {
                "alert": "Heartbeat stale",
                "severity": "Error",
                "detail": f"Last heartbeat {age_s/60:.0f} min ago (>30min threshold)",
                "last_activity": latest_ts.isoformat(),
                "age_minutes": round(age_s / 60, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        return None
    except Exception as e:
        return {
            "alert": "Heartbeat read failed",
            "severity": "Error",
            "detail": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def check_scheduler_running() -> dict | None:
    """Alert if scheduler has no recent lock files."""
    lock_dir = PROJECT_ROOT / ".lock"
    if not lock_dir.exists():
        return {
            "alert": "Scheduler not running",
            "severity": "Error",
            "detail": "Lock directory missing — no scheduler tasks have ever run",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    locks = list(lock_dir.glob("*.lock"))
    if not locks:
        return {
            "alert": "Scheduler not running",
            "severity": "Error",
            "detail": "No lock files found — scheduler may be stopped",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Check most recent lock
    now = datetime.now()
    latest_mtime = max(datetime.fromtimestamp(l.stat().st_mtime) for l in locks)
    age_h = (now - latest_mtime).total_seconds() / 3600
    if age_h > 24:
        return {
            "alert": "Scheduler not running",
            "severity": "Error",
            "detail": f"Most recent lock file is {age_h:.0f}h old",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return None


async def check_postgresql() -> dict | None:
    """Alert if PostgreSQL unreachable. Uses Database.check() for a one-shot connectivity probe."""
    try:
        from app.core.container import container
        ok = await container.database.check()
        if not ok:
            return {
                "alert": "PostgreSQL connection failed",
                "severity": "Error",
                "detail": "Database.check() returned False",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        return None
    except Exception as e:
        return {
            "alert": "PostgreSQL connection failed",
            "severity": "Error",
            "detail": f"{type(e).__name__}: {e}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def check_redis() -> dict | None:
    """Alert if Redis unreachable."""
    try:
        from app.core.container import container
        container.init_resources()
        try:
            ok = await container.redis.check()
            if not ok:
                return {
                    "alert": "Redis connection failed",
                    "severity": "Error",
                    "detail": "Redis check() returned False",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            return None
        finally:
            await container.shutdown_resources()
    except Exception as e:
        return {
            "alert": "Redis connection failed",
            "severity": "Error",
            "detail": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def check_odds_coverage(session) -> dict | None:
    """Alert if odds coverage <50% for any big-5 league."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, OddsSnapshotORM,
    )

    low_coverage = []
    for lid in BIG5_LEAGUE_IDS:
        subq = (
            select(FixtureORM.id)
            .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
            .where(CompetitionORM.external_id == str(lid))
            .subquery()
        )
        total = (await session.scalar(
            select(func.count()).select_from(subq)
        )) or 0
        matched = (await session.scalar(
            select(func.count(func.distinct(OddsSnapshotORM.fixture_id)))
            .where(OddsSnapshotORM.fixture_id.in_(select(subq.c.id)))
        )) or 0
        pct = round(matched / total * 100, 1) if total > 0 else 0
        if total > 0 and pct < 50:
            low_coverage.append(f"league_id={lid} ({pct}%)")

    if low_coverage:
        alerts = []
        ts = datetime.now(timezone.utc).isoformat()

        def _pct(entry: str) -> float:
            return float(entry.split("(")[1].rstrip("%)"))

        warning_items = [e for e in low_coverage if _pct(e) < 45]
        info_items = [e for e in low_coverage if 45 <= _pct(e) < 50]

        if warning_items:
            alerts.append({
                "alert": "Odds coverage below 45%",
                "severity": "Warning",
                "detail": f"Critical low odds coverage for: {', '.join(warning_items)}",
                "leagues": warning_items,
                "timestamp": ts,
            })
        if info_items:
            alerts.append({
                "alert": "Odds coverage 45-50% (off-season variance)",
                "severity": "Info",
                "detail": f"Moderate odds coverage for: {', '.join(info_items)}. "
                          f"May be off-season variance.",
                "leagues": info_items,
                "timestamp": ts,
            })
        return alerts
    return []


async def check_api_football() -> dict | None:
    """Alert if API-Football unreachable."""
    try:
        import aiohttp
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.api_football_key:
            return {
                "alert": "API-Football not configured",
                "severity": "Warning",
                "detail": "api_football_key is empty",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{settings.api_football_base_url}/status",
                headers={"x-apisports-key": settings.api_football_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {
                        "alert": "API-Football unavailable",
                        "severity": "Warning",
                        "detail": f"HTTP {resp.status}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
        return None
    except (ImportError, ModuleNotFoundError):
        return {
            "alert": "aiohttp not installed",
            "severity": "Warning",
            "detail": "aiohttp package is missing. Run: pip install aiohttp",
            "error_type": "ImportError",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except aiohttp.ClientConnectorError as e:
        return {
            "alert": "API-Football unreachable",
            "severity": "Warning",
            "detail": f"ClientConnectorError: {e}",
            "error_type": "ClientConnectorError",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except aiohttp.ServerTimeoutError:
        return {
            "alert": "API-Football unreachable",
            "severity": "Warning",
            "detail": "Server timeout (no response in 10s)",
            "error_type": "ServerTimeoutError",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except TimeoutError:
        return {
            "alert": "API-Football unreachable",
            "severity": "Warning",
            "detail": "Connection timed out",
            "error_type": "TimeoutError",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except aiohttp.ClientError as e:
        return {
            "alert": "API-Football unreachable",
            "severity": "Warning",
            "detail": f"ClientError: {type(e).__name__}: {e}",
            "error_type": type(e).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "alert": "API-Football unreachable",
            "severity": "Warning",
            "detail": f"Unexpected: {type(e).__name__}: {e}",
            "error_type": type(e).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def check_no_predictions_3d(session) -> dict | None:
    """Alert if 0 predictions in last 3 days."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import PredictionORM

    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    count = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.created_at >= three_days_ago)
    )) or 0

    if count == 0:
        return {
            "alert": "No predictions in 3 days",
            "severity": "Warning",
            "detail": "Zero predictions generated in the last 72 hours. "
                      "Check fixture ingestion and odds availability.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return None


async def check_no_value_bets_7d(session) -> dict | None:
    """Alert if 0 value bets in last 7 days during active season.

    Requires sufficient data volume before classifying as active season:
      - >=20 upcoming big-5 league fixtures in next 7 days
      - >=10 predictions in last 7 days
      - >=10 gate evaluations in last 7 days
    Otherwise outputs Info (insufficient sample) instead of Warning.
    """
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        ValueBetORM, PredictionORM, FixtureORM, CompetitionORM,
    )

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    count = (await session.scalar(
        select(func.count(ValueBetORM.id))
        .where(ValueBetORM.created_at >= seven_days_ago)
    )) or 0

    if count == 0:
        # Data volume thresholds — require enough data to classify as active season
        seven_days_ahead = datetime.now(timezone.utc) + timedelta(days=7)
        upcoming_fixtures = (await session.scalar(
            select(func.count(FixtureORM.id))
            .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
            .where(CompetitionORM.external_id.in_([str(l) for l in BIG5_LEAGUE_IDS]))
            .where(FixtureORM.kickoff.between(
                datetime.now(timezone.utc),
                seven_days_ahead,
            ))
        )) or 0

        recent_predictions = (await session.scalar(
            select(func.count(PredictionORM.id))
            .where(PredictionORM.created_at >= seven_days_ago)
        )) or 0

        gate_evaluations = (await session.scalar(
            select(func.count(PredictionORM.id))
            .where(PredictionORM.created_at >= seven_days_ago)
            .where(PredictionORM.final_decision.isnot(None))
        )) or 0

        if upcoming_fixtures >= 20 and recent_predictions >= 10 and gate_evaluations >= 10:
            return {
                "alert": "No value bets in 7 days (active season)",
                "severity": "Warning",
                "detail": f"Zero value bets in 7 days while {upcoming_fixtures} upcoming fixtures, "
                          f"{recent_predictions} predictions, and {gate_evaluations} gate evaluations "
                          f"exist for big-5 leagues. Check Gate thresholds and data quality.",
                "upcoming_fixtures": upcoming_fixtures,
                "predictions": recent_predictions,
                "gate_evaluations": gate_evaluations,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return {
                "alert": "No value bets in 7 days (insufficient sample)",
                "severity": "Info",
                "detail": f"Insufficient active-season sample to evaluate no-value-bet condition. "
                          f"Upcoming fixtures: {upcoming_fixtures} (need >=20), "
                          f"predictions: {recent_predictions} (need >=10), "
                          f"gate evaluations: {gate_evaluations} (need >=10).",
                "upcoming_fixtures": upcoming_fixtures,
                "predictions": recent_predictions,
                "gate_evaluations": gate_evaluations,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    return None


# ---------------------------------------------------------------------------
# Pipeline Duration Alerts
# ---------------------------------------------------------------------------

PIPELINE_DURATION_THRESHOLDS: dict[str, dict] = {
    "daily_job":        {"threshold_s": 300, "level": "Warning"},
    "fixture_sync":     {"threshold_s": 120, "level": "Warning"},
    "odds_sync":        {"threshold_s": 120, "level": "Warning"},
    "analysis":         {"threshold_s": 180, "level": "Warning"},
    "dashboard_refresh":{"threshold_s": 60,  "level": "Warning"},
    "settlement":       {"threshold_s": 60,  "level": "Warning"},
}


def _get_pipeline_durations_from_heartbeat() -> list[dict]:
    """Extract pipeline durations from heartbeat.json and check against thresholds.

    Returns a list of alert dicts for any pipeline exceeding its threshold.
    """
    if not HEARTBEAT_FILE.exists():
        return []

    alerts = []
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        now_ts = datetime.now(timezone.utc)

        for name, config in PIPELINE_DURATION_THRESHOLDS.items():
            entry = data.get(name, {})
            if not isinstance(entry, dict):
                continue

            start_str = entry.get("last_start", "")
            end_str = entry.get("last_end", "")
            if not start_str or not end_str:
                continue

            try:
                start_dt = datetime.fromisoformat(start_str)
                end_dt = datetime.fromisoformat(end_str)
                duration = (end_dt - start_dt).total_seconds()
            except (ValueError, TypeError):
                continue

            threshold = config["threshold_s"]
            if duration > threshold:
                alerts.append({
                    "alert": f"Pipeline {name} exceeded duration threshold",
                    "severity": config["level"],
                    "detail": (
                        f"{name}: {duration:.1f}s (threshold: {threshold}s, "
                        f"start: {start_str})"
                    ),
                    "pipeline_name": name,
                    "duration_seconds": round(duration, 1),
                    "threshold_seconds": threshold,
                    "timestamp": now_ts.isoformat(),
                })
    except Exception:
        pass

    return alerts


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run(log=None) -> dict:
    """Run all alert checks. Returns dict with alerts list."""
    from app.core.container import container

    alerts = []

    # Non-DB checks (can run in parallel)
    non_db_results = await asyncio.gather(
        check_heartbeat_stale(),
        check_scheduler_running(),
        check_api_football(),
        check_redis(),
        return_exceptions=True,
    )

    for result in non_db_results:
        if isinstance(result, Exception):
            alerts.append({
                "alert": "Check exception",
                "severity": "Error",
                "detail": str(result),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif result is not None:
            alerts.append(result)
            if log:
                log.warning("  [%s] %s: %s", result["severity"], result["alert"], result["detail"])

    # Pipeline duration alerts (synchronous, reads from heartbeat.json)
    pipeline_alerts = _get_pipeline_durations_from_heartbeat()
    for pa in pipeline_alerts:
        alerts.append(pa)
        if log:
            log.warning("  [%s] %s: %s", pa["severity"], pa["alert"], pa["detail"])

    # DB-dependent checks — run sequentially with independent sessions
    # to avoid SQLAlchemy "concurrent operations are not permitted" errors
    container.init_resources()
    try:
        # PostgreSQL connectivity probe (uses Database.check(), no session needed)
        pg_result = await check_postgresql()
        if pg_result:
            alerts.append(pg_result)
            if log:
                log.warning("  [%s] %s: %s", pg_result["severity"], pg_result["alert"], pg_result["detail"])

        # Business queries — each gets its own short-lived session
        async with container.database.session() as session:
            coverage_alerts = await check_odds_coverage(session)
            if coverage_alerts:
                alerts.extend(coverage_alerts)
                for cov in coverage_alerts:
                    if log:
                        log.warning("  [%s] %s: %s", cov["severity"], cov["alert"], cov["detail"])

        async with container.database.session() as session:
            predictions = await check_no_predictions_3d(session)
            if predictions:
                alerts.append(predictions)
                if log:
                    log.warning("  [%s] %s: %s", predictions["severity"], predictions["alert"], predictions["detail"])

        async with container.database.session() as session:
            value_bets = await check_no_value_bets_7d(session)
            if value_bets:
                alerts.append(value_bets)
                if log:
                    log.warning("  [%s] %s: %s", value_bets["severity"], value_bets["alert"], value_bets["detail"])
    finally:
        await container.shutdown_resources()

    # --- Deduplication: same alert type only once per day ---
    today_str = date.today().isoformat()
    dedup_keys: set[str] = set()
    existing_file = OUTPUT_DIR / f"alerts_{today_str}.json"
    if existing_file.exists():
        try:
            existing = json.loads(existing_file.read_text(encoding="utf-8"))
            for a in existing.get("alerts", []):
                key = a.get("alert", "")
                if a.get("error_type"):
                    key += "::" + a["error_type"]
                dedup_keys.add(key)
        except Exception:
            pass

    deduped_alerts = []
    for a in alerts:
        key = a.get("alert", "")
        if a.get("error_type"):
            key += "::" + a["error_type"]
        if key not in dedup_keys:
            dedup_keys.add(key)
            deduped_alerts.append(a)

    alerts = deduped_alerts

    now = datetime.now(timezone.utc)
    errors = sum(1 for a in alerts if a["severity"] == "Error")
    warnings = sum(1 for a in alerts if a["severity"] == "Warning")
    infos = sum(1 for a in alerts if a["severity"] == "Info")

    report = {
        "timestamp": now.isoformat(),
        "date": date.today().isoformat(),
        "alerts_count": len(alerts),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "alerts": alerts,
    }

    if log:
        log.info("Alert check complete: %d alert(s) (%d errors, %d warnings, %d infos)",
                 len(alerts), errors, warnings, infos)
    return report


def write_alerts(report: dict) -> Path:
    """Write alerts JSON report."""
    today = report["date"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"alerts_{today}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    return json_path


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(run())
    json_path = write_alerts(report)

    print(f"Alerts: {report['alerts_count']} ({report['errors']} errors, {report['warnings']} warnings)")
    print(f"Written: {json_path}")
    for alert in report["alerts"]:
        icon = {"Error": "❌", "Warning": "⚠️"}.get(alert["severity"], "?")
        print(f"  {icon} [{alert['severity']}] {alert['alert']}: {alert['detail'][:120]}")


if __name__ == "__main__":
    main()
