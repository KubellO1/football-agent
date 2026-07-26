"""
Daily production health dashboard for football-agent.

Checks 10 components and outputs overall status: Healthy / Warning / Error.

Usage:
    python scripts/health_check.py                    # standalone, writes report
    python -m app.workers.scheduler_runner --command health_check  # via scheduler

Outputs:
    output/health_check_YYYY-MM-DD.json   — structured status
    output/health_check_YYYY-MM-DD.md     — human-readable report
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

HEARTBEAT_FILE = PROJECT_ROOT / "app" / "state" / "heartbeat.json"
LOCK_DIR = PROJECT_ROOT / ".lock"
OUTPUT_DIR = PROJECT_ROOT / "output"
BACKUP_DIR = PROJECT_ROOT / "backups"
DASHBOARD_PATTERN = "dashboard_*.html"

# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

async def check_heartbeat() -> dict:
    """Check heartbeat.json freshness. Warning if >30 min, Error if >2h or missing."""
    if not HEARTBEAT_FILE.exists():
        return {"status": "Error", "detail": "heartbeat.json not found", "component": "Heartbeat"}

    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        # Find most recent last_end across all tasks
        latest = None
        latest_key = ""
        for key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            end_str = entry.get("last_end", "")
            if not end_str:
                continue
            try:
                dt = datetime.fromisoformat(end_str)
                if latest is None or dt > latest:
                    latest = dt
                    latest_key = key
            except (ValueError, TypeError):
                continue

        if latest is None:
            return {"status": "Warning", "detail": "No last_end timestamps found in heartbeat",
                    "component": "Heartbeat", "entries": list(data.keys())}

        age_s = (datetime.now(timezone.utc) - latest).total_seconds()
        if age_s > 7200:
            return {"status": "Error",
                    "detail": f"Stale: last activity {age_s/3600:.1f}h ago (task: {latest_key})",
                    "component": "Heartbeat", "last_task": latest_key,
                    "last_time": latest.isoformat(), "age_seconds": round(age_s)}
        elif age_s > 1800:
            return {"status": "Warning",
                    "detail": f"Aging: last activity {age_s/60:.0f}min ago (task: {latest_key})",
                    "component": "Heartbeat", "last_task": latest_key,
                    "last_time": latest.isoformat(), "age_seconds": round(age_s)}
        return {"status": "OK", "detail": f"Active: {age_s/60:.0f}min ago ({latest_key})",
                "component": "Heartbeat", "last_task": latest_key, "age_seconds": round(age_s)}
    except Exception as e:
        return {"status": "Error", "detail": f"Heartbeat read failed: {e}", "component": "Heartbeat"}


async def check_scheduler() -> dict:
    """Check that scheduler tasks have recent lock files (implies Task Scheduler running)."""
    if not LOCK_DIR.exists():
        return {"status": "Warning", "detail": "Lock directory missing — scheduler may not have run yet",
                "component": "Scheduler"}

    locks = list(LOCK_DIR.glob("*.lock"))
    if not locks:
        return {"status": "Warning", "detail": "No lock files — no scheduler tasks detected",
                "component": "Scheduler"}

    now = datetime.now()
    active = []
    stale = []
    for lock in locks:
        mtime = datetime.fromtimestamp(lock.stat().st_mtime)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h < 24:
            active.append((lock.stem, round(age_h, 1)))
        else:
            stale.append((lock.stem, round(age_h, 1)))

    if not active and stale:
        return {"status": "Error",
                "detail": f"No active task in 24h. Stale locks: {[s[0] for s in stale]}",
                "component": "Scheduler", "stale_locks": len(stale)}
    if stale:
        return {"status": "Warning",
                "detail": f"{len(active)} active tasks, {len(stale)} stale locks",
                "component": "Scheduler", "active_tasks": active, "stale_locks": [s[0] for s in stale]}

    return {"status": "OK", "detail": f"{len(active)} active task(s) in last 24h",
            "component": "Scheduler", "active_tasks": active}


async def check_postgresql() -> dict:
    """Check PostgreSQL connectivity via asyncpg."""
    try:
        from sqlalchemy import text
        from app.core.container import container

        container.init_resources()
        try:
            async with container.database.session() as session:
                await session.execute(text("SELECT 1"))
                return {"status": "OK", "detail": "PostgreSQL connected and responsive",
                        "component": "PostgreSQL"}
        finally:
            await container.shutdown_resources()
    except Exception as e:
        return {"status": "Error", "detail": f"PostgreSQL connection failed: {e}",
                "component": "PostgreSQL"}


async def check_redis() -> dict:
    """Check Redis connectivity via PING."""
    try:
        from app.core.container import container
        container.init_resources()
        try:
            redis = container.redis
            ok = await redis.check()
            if ok:
                return {"status": "OK", "detail": "Redis PING successful",
                        "component": "Redis"}
            return {"status": "Warning", "detail": "Redis PING returned falsy",
                    "component": "Redis"}
        finally:
            await container.shutdown_resources()
    except Exception as e:
        return {"status": "Error", "detail": f"Redis connection failed: {e}",
                "component": "Redis"}


async def check_api_football() -> dict:
    """Check API-Football status endpoint."""
    try:
        import aiohttp
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.api_football_key:
            return {"status": "Warning", "detail": "API key not configured",
                    "component": "API-Football"}

        headers = {"x-apisports-key": settings.api_football_key}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{settings.api_football_base_url}/status",
                                   headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    current = data.get("response", {}).get("requests", {}).get("current", "N/A")
                    limit = data.get("response", {}).get("requests", {}).get("limit_day", "N/A")
                    return {"status": "OK",
                            "detail": f"API-Football OK (used: {current} / limit: {limit})",
                            "component": "API-Football", "quota_used": current,
                            "quota_limit": limit}
                return {"status": "Warning", "detail": f"Status HTTP {resp.status}",
                        "component": "API-Football", "http_status": resp.status}
    except Exception as e:
        return {"status": "Error", "detail": f"API-Football unreachable: {e}",
                "component": "API-Football"}


async def check_odds_api() -> dict:
    """Check The Odds API status endpoint."""
    try:
        import aiohttp
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.odds_api_key:
            return {"status": "Warning", "detail": "API key not configured",
                    "component": "Odds API"}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{settings.odds_api_base_url}/sports/",
                params={"apiKey": settings.odds_api_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                remaining = resp.headers.get("x-requests-remaining", "N/A")
                used = resp.headers.get("x-requests-used", "N/A")
                if resp.status == 200:
                    data = await resp.json()
                    return {"status": "OK",
                            "detail": f"Odds API OK (remaining: {remaining}, used: {used})",
                            "component": "Odds API", "sports_count": len(data),
                            "quota_remaining": remaining}
                elif resp.status in (401, 403):
                    return {"status": "Error", "detail": f"Odds API auth failed (HTTP {resp.status})",
                            "component": "Odds API", "http_status": resp.status}
                return {"status": "Warning",
                        "detail": f"Odds API HTTP {resp.status} (remaining: {remaining})",
                        "component": "Odds API", "http_status": resp.status}
    except aiohttp.ClientConnectorError as e:
        return {"status": "Error",
                "detail": f"Odds API: connection refused (DNS/TCP) — {e}",
                "component": "Odds API", "error_type": "ClientConnectorError"}
    except aiohttp.ServerTimeoutError:
        return {"status": "Error",
                "detail": "Odds API: request timed out (server no response in 10s)",
                "component": "Odds API", "error_type": "ServerTimeoutError"}
    except TimeoutError:
        return {"status": "Error",
                "detail": "Odds API: connection timed out",
                "component": "Odds API", "error_type": "TimeoutError"}
    except aiohttp.ClientSSLError as e:
        return {"status": "Error",
                "detail": f"Odds API: TLS/SSL error — {e}",
                "component": "Odds API", "error_type": "ClientSSLError"}
    except aiohttp.ClientError as e:
        return {"status": "Error",
                "detail": f"Odds API: client error — {type(e).__name__}: {e}",
                "component": "Odds API", "error_type": type(e).__name__}
    except Exception as e:
        return {"status": "Error",
                "detail": f"Odds API: unexpected error — {type(e).__name__}: {e}",
                "component": "Odds API", "error_type": type(e).__name__}


async def check_weather_api() -> dict:
    """Check WeatherAPI connectivity."""
    try:
        import aiohttp
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.weatherapi_key:
            return {"status": "Warning", "detail": "API key not configured",
                    "component": "Weather API"}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.weatherapi.com/v1/current.json",
                params={"key": settings.weatherapi_key, "q": "London"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return {"status": "OK", "detail": "Weather API OK",
                            "component": "Weather API"}
                return {"status": "Warning", "detail": f"Weather API HTTP {resp.status}",
                        "component": "Weather API", "http_status": resp.status}
    except Exception as e:
        return {"status": "Warning", "detail": f"Weather API unreachable: {e}",
                "component": "Weather API"}


async def check_dashboard() -> dict:
    """Check that dashboard HTML exists and is non-empty for today."""
    from datetime import date
    today = date.today().isoformat()
    dashboard_path = OUTPUT_DIR / f"dashboard_{today}.html"

    if dashboard_path.exists():
        size = dashboard_path.stat().st_size
        if size > 1024:
            return {"status": "OK",
                    "detail": f"Dashboard exists: {dashboard_path.name} ({size:,} bytes)",
                    "component": "Dashboard", "path": str(dashboard_path), "size": size}
        return {"status": "Warning",
                "detail": f"Dashboard exists but small: {size} bytes",
                "component": "Dashboard", "path": str(dashboard_path)}
    # Check for any recent dashboard
    pattern = OUTPUT_DIR / "dashboard_*.html"
    matches = sorted(OUTPUT_DIR.glob("dashboard_*.html"), reverse=True)
    if matches:
        latest_dt = datetime.fromtimestamp(matches[0].stat().st_mtime).isoformat()
        return {"status": "Warning",
                "detail": f"No dashboard for today. Latest: {matches[0].name}",
                "component": "Dashboard", "latest": str(matches[0]),
                "latest_time": latest_dt}
    return {"status": "Warning", "detail": "No dashboard HTML found in output/",
            "component": "Dashboard"}


async def check_backup() -> dict:
    """Check most recent backup file age."""
    if not BACKUP_DIR.exists():
        return {"status": "Warning", "detail": "Backup directory not found",
                "component": "Backup"}

    backups = sorted(BACKUP_DIR.glob("football_agent_backup_*.sql"), reverse=True)
    if not backups:
        return {"status": "Warning", "detail": "No backup files found",
                "component": "Backup"}

    latest = backups[0]
    age_h = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds() / 3600
    size_mb = round(latest.stat().st_size / (1024 * 1024), 2)

    if age_h > 48:
        return {"status": "Error", "detail": f"Latest backup is {age_h:.0f}h old",
                "component": "Backup", "latest_file": str(latest), "age_hours": round(age_h, 1),
                "size_mb": size_mb, "total_backups": len(backups)}
    if age_h > 36:
        return {"status": "Warning", "detail": f"Latest backup is {age_h:.0f}h old",
                "component": "Backup", "latest_file": str(latest), "age_hours": round(age_h, 1),
                "size_mb": size_mb, "total_backups": len(backups)}
    return {"status": "OK", "detail": f"Latest backup: {age_h:.1f}h ago",
            "component": "Backup", "latest_file": str(latest), "age_hours": round(age_h, 1),
            "size_mb": size_mb, "total_backups": len(backups)}


async def check_last_successful_run() -> dict:
    """Check the most recent daily_job success from heartbeat."""
    if not HEARTBEAT_FILE.exists():
        return {"status": "Error", "detail": "No heartbeat data",
                "component": "Last Successful Run"}

    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        daily = data.get("daily_job", {})
        if not daily:
            return {"status": "Warning", "detail": "daily_job never recorded",
                    "component": "Last Successful Run"}

        status = daily.get("status", "unknown")
        last_start = daily.get("last_start", "unknown")
        if status == "success":
            age_h = None
            if last_start and last_start != "unknown":
                try:
                    dt = datetime.fromisoformat(last_start)
                    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass
            if age_h and age_h > 36:
                return {"status": "Warning",
                        "detail": f"Last success {age_h:.0f}h ago", "component": "Last Successful Run",
                        "last_success": last_start, "age_hours": round(age_h, 1)}
            return {"status": "OK",
                    "detail": f"Last daily_job success: {last_start}", "component": "Last Successful Run",
                    "last_success": last_start, "age_hours": round(age_h, 1) if age_h else None}

        error = daily.get("error", "")[:200]
        return {"status": "Error",
                "detail": f"daily_job last status: {status}",
                "component": "Last Successful Run",
                "last_start": last_start, "error_snippet": error}
    except Exception as e:
        return {"status": "Error", "detail": f"Heartbeat parse failed: {e}",
                "component": "Last Successful Run"}


# ---------------------------------------------------------------------------
# Pipeline Duration Statistics
# ---------------------------------------------------------------------------

PIPELINE_HISTORY_FILE = OUTPUT_DIR / "pipeline_duration_history.json"
PIPELINES_TO_MONITOR = [
    "daily_job",
    "fixture_sync",
    "odds_sync",
    "analysis",
    "dashboard_refresh",
    "settlement",
]


def _load_pipeline_history() -> dict:
    """Load persisted pipeline duration history. Returns {pipeline_name: [dur1, dur2, ...]}."""
    if not PIPELINE_HISTORY_FILE.exists():
        return {}
    try:
        raw = json.loads(PIPELINE_HISTORY_FILE.read_text(encoding="utf-8"))
        by_pipeline = raw.get("history", {})
        # Ensure all values are lists
        return {k: v for k, v in by_pipeline.items() if isinstance(v, list)}
    except (json.JSONDecodeError, KeyError):
        return {}


def _save_pipeline_history(history: dict) -> None:
    """Persist pipeline duration history (latest N runs per pipeline)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Trim to last 30 records per pipeline
    trimmed = {k: v[-30:] for k, v in history.items()}
    PIPELINE_HISTORY_FILE.write_text(
        json.dumps({"history": trimmed, "updated": datetime.now(timezone.utc).isoformat()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _compute_latest_durations_from_heartbeat() -> dict:
    """Extract per-pipeline latest duration from heartbeat.json.

    Returns {pipeline_name: float | None} where float is duration in seconds.
    """
    if not HEARTBEAT_FILE.exists():
        return {}

    durations = {}
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        for name in PIPELINES_TO_MONITOR:
            entry = data.get(name, {})
            if not isinstance(entry, dict):
                durations[name] = None
                continue
            start_str = entry.get("last_start", "")
            end_str = entry.get("last_end", "")
            if not start_str or not end_str:
                durations[name] = None
                continue
            try:
                start_dt = datetime.fromisoformat(start_str)
                end_dt = datetime.fromisoformat(end_str)
                dur = (end_dt - start_dt).total_seconds()
                durations[name] = max(dur, 0.01)  # at least 0.01s
            except (ValueError, TypeError):
                durations[name] = None
    except Exception:
        pass

    return durations


async def check_pipeline_durations() -> dict:
    """Check pipeline execution durations against historical averages.

    Reads latest durations from heartbeat.json, compares against rolling average
    from pipeline_duration_history.json.

    Returns a component entry with per-pipeline breakdown.
    """
    # Load latest durations from heartbeat
    latest = _compute_latest_durations_from_heartbeat()

    # Load history and update it
    history = _load_pipeline_history()
    for name, dur in latest.items():
        if dur is not None:
            if name not in history:
                history[name] = []
            history[name].append(round(dur, 2))

    # Save updated history (trims to 30 records)
    _save_pipeline_history(history)

    # Build per-pipeline status
    pipeline_details = []
    overall_status = "OK"

    for name in PIPELINES_TO_MONITOR:
        latest_dur = latest.get(name)
        if latest_dur is None:
            pipeline_details.append({
                "pipeline": name,
                "latest_duration_s": None,
                "avg_duration_s": None,
                "status": "N/A",
                "detail": f"{name}: no heartbeat data",
            })
            continue

        past = history.get(name, [])
        # Average of all past runs excluding the one we just added
        if len(past) > 1:
            avg = round(sum(past[:-1]) / (len(past) - 1), 2)
        elif len(past) == 1:
            avg = None  # Only one data point, no baseline
        else:
            avg = None

        # Determine status
        if avg is not None and avg > 0 and latest_dur > avg * 2:
            status = "SLOW"
            if overall_status == "OK":
                overall_status = "Warning"
        else:
            status = "OK"

        detail_parts = [f"{latest_dur:.1f}s"]
        if avg is not None:
            detail_parts.append(f"avg: {avg:.1f}s")
        else:
            detail_parts.append("avg: N/A")

        if status == "SLOW":
            ratio = latest_dur / avg if avg and avg > 0 else 0
            detail_parts.append(f"(>2x avg, ratio={ratio:.1f}x)")

        pipeline_details.append({
            "pipeline": name,
            "latest_duration_s": round(latest_dur, 2),
            "avg_duration_s": avg,
            "status": status,
            "detail": " | ".join(detail_parts),
        })

    # Generate a concise detail string for the component row
    slow_count = sum(1 for p in pipeline_details if p["status"] == "SLOW")
    na_count = sum(1 for p in pipeline_details if p["status"] == "N/A")
    ok_count = sum(1 for p in pipeline_details if p["status"] == "OK")
    detail = f"{ok_count} OK, {slow_count} SLOW, {na_count} N/A across {len(PIPELINES_TO_MONITOR)} pipelines"

    return {
        "status": overall_status,
        "detail": detail,
        "component": "Pipeline Durations",
        "pipelines": pipeline_details,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

COMPONENTS = [
    ("Heartbeat", check_heartbeat),
    ("Scheduler", check_scheduler),
    ("PostgreSQL", check_postgresql),
    ("Redis", check_redis),
    ("API-Football", check_api_football),
    ("Odds API", check_odds_api),
    ("Weather API", check_weather_api),
    ("Dashboard", check_dashboard),
    ("Backup", check_backup),
    ("Last Successful Run", check_last_successful_run),
    ("Pipeline Durations", check_pipeline_durations),
]


async def run(log=None) -> dict:
    """Run all health checks and return structured report."""
    now = datetime.now(timezone.utc)
    tasks = [(name, coro()) for name, coro in COMPONENTS]
    results = []
    for name, task in zip([n for n, _ in tasks], asyncio.as_completed([t for _, t in tasks])):
        result = await task
        results.append(result)
        if log:
            log.info("  %s: %s — %s", name, result["status"], result["detail"])

    # Re-sort to canonical order
    name_order = {name: i for i, (name, _) in enumerate(COMPONENTS)}
    results.sort(key=lambda r: name_order.get(r["component"], 999))

    # Determine overall status
    statuses = [r["status"] for r in results]
    ok_count = statuses.count("OK")
    warn_count = statuses.count("Warning")
    err_count = statuses.count("Error")

    if err_count > 0:
        overall = "Error"
    elif warn_count > 0:
        overall = "Warning"
    else:
        overall = "Healthy"

    report = {
        "timestamp": now.isoformat(),
        "overall_status": overall,
        "summary": f"{ok_count} OK, {warn_count} Warning, {err_count} Error",
        "components": results,
    }
    return report


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_reports(report: dict) -> tuple[Path, Path]:
    """Write JSON and Markdown reports. Returns (json_path, md_path)."""
    from datetime import date
    today = date.today().isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"health_check_{today}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    md_path = OUTPUT_DIR / f"health_check_{today}.md"
    lines = []
    lines.append(f"# Production Health Report — {today}")
    lines.append("")
    lines.append(f"**Overall Status**: {report['overall_status']} "
                 f"({report['summary']})")
    lines.append("")
    lines.append("| # | Component | Status | Detail |")
    lines.append("|---|-----------|--------|--------|")

    for i, comp in enumerate(report["components"], 1):
        status_icon = {"OK": "✅", "Warning": "⚠️", "Error": "❌"}.get(comp["status"], "?")
        lines.append(
            f"| {i} | {comp['component']} | {status_icon} {comp['status']} | {comp['detail']} |"
        )

    lines.append("")
    lines.append(f"*Report generated: {report['timestamp']}*")

    md_path.write_text("\n".join(lines) + _build_pipeline_duration_md_section(report), encoding="utf-8")
    return json_path, md_path


def _build_pipeline_duration_md_section(report: dict) -> str:
    """Build pipeline duration Markdown section if pipeline data exists."""
    for comp in report.get("components", []):
        if comp["component"] == "Pipeline Durations" and "pipelines" in comp:
            lines = []
            lines.append("")
            lines.append("## Pipeline Execution Durations")
            lines.append("")
            lines.append("| Pipeline | Latest (s) | Avg (s) | Status |")
            lines.append("|----------|-----------|---------|--------|")
            for p in comp["pipelines"]:
                latest = f"{p['latest_duration_s']:.1f}" if p["latest_duration_s"] is not None else "N/A"
                avg = f"{p['avg_duration_s']:.1f}" if p["avg_duration_s"] is not None else "N/A"
                icon = "✅" if p["status"] == "OK" else ("⚠️" if p["status"] == "SLOW" else "—")
                lines.append(f"| {p['pipeline']} | {latest} | {avg} | {icon} {p['status']} |")
            return "\n".join(lines)
    return ""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """Standalone: run all checks and write reports."""
    report = asyncio.run(run())
    json_path, md_path = write_reports(report)

    print(f"Overall: {report['overall_status']} ({report['summary']})")
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    for comp in report["components"]:
        print(f"  [{comp['status']:7s}] {comp['component']:<22s} {comp['detail']}")

    # Exit 0 as long as the report was written — overall_status is informational only.
    # Non-zero reserved for unrecoverable failures (unhandled exception, disk write error).


if __name__ == "__main__":
    main()
