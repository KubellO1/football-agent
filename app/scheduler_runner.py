"""Windows Task Scheduler production runner — 8 task types.

⚠️ DEPRECATED (2026-07-15): This module has been superseded by app/workers/scheduler_runner.py.
All Windows Task Scheduler tasks now invoke app.workers.scheduler_runner.py.
Heartbeat writes go to app/state/heartbeat.json (not data/heartbeat.json).
Do NOT schedule new tasks against this module. Migrate any remaining callers.

Usage: python -m app.scheduler_runner --task <TASK_ID> --run-id <ID> --trigger-source scheduler

Task IDs:
    health_check          ProviderHealthCheck — verify all providers report status
    daily_run             DailyProductionRun — full production pipeline
    recovery_check        ProductionRecoveryCheck — verify heartbeat, retry daily_run if needed
    pre_kickoff           PreKickoffValidation — refresh odds/lineups/injuries/weather for T-90/T-30 fixtures
    settlement_fallback   SettlementFallback — settle eligible paper bets (idempotent)
    daily_report          DailyPerformanceReport — generate daily performance report
    weekly_report         WeeklyPerformanceReport — generate weekly performance report
    dashboard_refresh     DashboardRefresh — regenerate full dashboard HTML from DB
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone as tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = PROJECT_ROOT / ".lock"
HEARTBEAT_FILE = PROJECT_ROOT / "app" / "state" / "heartbeat.json"
LOG_DIR = PROJECT_ROOT / "app" / "state" / "logs"

PARIS = ZoneInfo("Europe/Paris")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_LOG_FILES = {
    "health_check": "health_check.log",
    "daily_run": "daily_run.log",
    "recovery_check": "recovery_check.log",
    "pre_kickoff": "pre_kickoff.log",
    "settlement_fallback": "settlement_fallback.log",
    "daily_report": "daily_report.log",
    "weekly_report": "weekly_report.log",
    "dashboard_refresh": "dashboard_refresh.log",
}

_loggers: dict[str, logging.Logger] = {}


def _get_logger(task_name: str) -> logging.Logger:
    if task_name in _loggers:
        return _loggers[task_name]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / _LOG_FILES.get(task_name, f"{task_name}.log")
    handler = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    handler.setFormatter(_LOG_FORMAT)
    logger = logging.getLogger(f"scheduler.{task_name}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_LOG_FORMAT)
    logger.addHandler(stream)
    _loggers[task_name] = logger
    return logger


# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def _load_heartbeat() -> dict:
    if HEARTBEAT_FILE.exists():
        try:
            return json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_heartbeat(data: dict) -> None:
    HEARTBEAT_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )


def write_heartbeat(
    task_name: str,
    start_time: str,
    end_time: str,
    status: str,
    error: str | None = None,
    *,
    trigger_source: str = "manual",
    run_id: str = "",
    fixtures_count: int = 0,
    odds_count: int = 0,
    predictions_count: int = 0,
    bet_count: int = 0,
    settlements_count: int = 0,
    providers_checked: int = 0,
    providers_failed: int = 0,
) -> None:
    heartbeat = _load_heartbeat()
    record: dict = {
        "trigger_source": trigger_source,
        "task_name": task_name,
        "last_start": start_time,
        "last_end": end_time,
        "status": status,
        "run_id": run_id,
        "error": error,
    }
    if fixtures_count or odds_count or predictions_count:
        record["fixtures_count"] = fixtures_count
        record["odds_count"] = odds_count
        record["predictions_count"] = predictions_count
        record["bet_count"] = bet_count
        record["settlements_count"] = settlements_count
    if providers_checked:
        record["providers_checked"] = providers_checked
        record["providers_failed"] = providers_failed
    heartbeat[task_name] = record
    _save_heartbeat(heartbeat)


# ---------------------------------------------------------------------------
# File Lock with stale recovery
# ---------------------------------------------------------------------------
_EXPECTED_DURATION: dict[str, int] = {
    "health_check": 600,
    "daily_run": 1800,
    "recovery_check": 900,
    "pre_kickoff": 300,
    "settlement_fallback": 120,
    "daily_report": 300,
    "weekly_report": 600,
    "dashboard_refresh": 300,
}
_DEFAULT_EXPECTED = 600


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stale_ttl(task_name: str) -> int:
    return min(2 * _EXPECTED_DURATION.get(task_name, _DEFAULT_EXPECTED), 3600)


def acquire_lock(task_name: str) -> str | None:
    _ensure_dirs()
    lock_path = LOCK_DIR / f"{task_name}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        pid = os.getpid()
        os.write(fd, f"{pid}\n{datetime.now(PARIS).isoformat()}\n".encode())
        os.close(fd)
        return str(lock_path)
    except FileExistsError:
        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            lines = content.splitlines()
            old_pid = int(lines[0]) if lines else -1
            old_time_str = lines[1] if len(lines) > 1 else ""
        except (ValueError, OSError):
            old_pid = -1
            old_time_str = ""

        if old_pid > 0 and _is_pid_alive(old_pid):
            return None

        stale = False
        if old_time_str:
            try:
                old_time = datetime.fromisoformat(old_time_str)
                age = (datetime.now(PARIS) - old_time).total_seconds()
                if age > _stale_ttl(task_name):
                    stale = True
            except (ValueError, TypeError):
                stale = True
        else:
            stale = True

        if stale:
            log = _get_logger(task_name)
            log.warning("Breaking stale lock for %s (pid=%s), re-acquiring.", task_name, old_pid)
            release_lock(str(lock_path))
            return acquire_lock(task_name)
        return None


def release_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except OSError:
        pass


# ===================================================================
# Task Implementations
# ===================================================================

PROVIDER_HEALTH_FILE = PROJECT_ROOT / "data" / "provider_health.json"
RUN_TIMELINE_FILE = PROJECT_ROOT / "data" / "run_timeline.json"
ROI_METRICS_FILE = PROJECT_ROOT / "data" / "roi_metrics.json"


def _load_provider_health() -> dict:
    if PROVIDER_HEALTH_FILE.exists():
        try:
            return json.loads(PROVIDER_HEALTH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_provider_health(data: dict) -> None:
    PROVIDER_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDER_HEALTH_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )


def _load_timeline() -> list[dict]:
    """Load run timeline from data/run_timeline.json."""
    if RUN_TIMELINE_FILE.exists():
        try:
            return json.loads(RUN_TIMELINE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_timeline(data: list[dict]) -> None:
    """Save run timeline to data/run_timeline.json."""
    RUN_TIMELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_TIMELINE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )


def _append_timeline_entry(
    task: str, status: str, duration_s: float, details: str,
    time_str: str | None = None,
) -> None:
    """Append a single entry to the run timeline. Keeps last 200 entries."""
    if time_str is None:
        time_str = datetime.now(PARIS).strftime("%H:%M")
    timeline = _load_timeline()
    timeline.append({
        "time": time_str,
        "task": task,
        "status": status,
        "duration_s": duration_s,
        "details": details,
    })
    if len(timeline) > 200:
        timeline = timeline[-200:]
    _save_timeline(timeline)


def _load_roi_metrics() -> dict:
    """Load ROI metrics from data/roi_metrics.json."""
    if ROI_METRICS_FILE.exists():
        try:
            return json.loads(ROI_METRICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_roi_metrics(data: dict) -> None:
    """Save ROI metrics to data/roi_metrics.json."""
    ROI_METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROI_METRICS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )


async def _run_health_check(log: logging.Logger) -> None:
    """Verify connectivity + auth for all providers. Record detailed metrics per provider.
    Write data/provider_health.json and generate health report."""
    from app.config.settings import Settings
    from app.core.container import Container

    settings = Settings()
    now = datetime.now(tz.utc)
    month_key = now.strftime("%Y-%m")

    # Load persistent health data
    health = _load_provider_health()

    # 迁移旧 Claude 健康指标，保留历史 uptime 与调用计数。
    if "openai" not in health and "claude" in health:
        health["openai"] = health.pop("claude")

    # Reset calls_this_month if month changed
    for pname, pdata in health.items():
        if isinstance(pdata, dict) and pdata.get("_month_key") != month_key:
            pdata["calls_this_month"] = 0
            pdata["_month_key"] = month_key

    provider_names = [
        "api_football", "odds_api", "weather_api",
        "postgresql", "redis", "openai",
    ]

    # Ensure all providers exist in health dict
    for pname in provider_names:
        if pname not in health:
            health[pname] = {
                "uptime": 100.0,
                "response_time_ms": 0,
                "quota_remaining": 0,
                "quota_reset": "",
                "calls_today": 0,
                "calls_this_month": 0,
                "success_count": 0,
                "failure_count": 0,
                "_month_key": month_key,
            }

    results: dict[str, str] = {}
    checked = 0
    failed = 0

    # ── API-Football ──
    if settings.api_football_key:
        checked += 1
        t0 = datetime.now()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    "https://v3.football.api-sports.io/status",
                    headers={"x-apisports-key": settings.api_football_key},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
                    quota_remaining = data.get("response", {}).get("requests", {}).get("remaining", 0)
                    p = health["api_football"]
                    p["response_time_ms"] = elapsed_ms
                    p["quota_remaining"] = quota_remaining
                    p["calls_today"] += 1
                    p["calls_this_month"] += 1
                    p["success_count"] += 1
                    total = p["success_count"] + p["failure_count"]
                    p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
                    results["API-Football"] = f"HTTP {resp.status} | {data.get('response',{}).get('subscription','?')}"
        except Exception as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["api_football"]
            p["response_time_ms"] = elapsed_ms
            p["failure_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
            results["API-Football"] = f"FAIL: {e}"
            failed += 1
    else:
        results["API-Football"] = "SKIP: no key"

    # ── The Odds API ──
    if settings.odds_api_key:
        checked += 1
        t0 = datetime.now()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"https://api.the-odds-api.com/v4/sports/upcoming/?apiKey={settings.odds_api_key}",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
                    remaining_header = resp.headers.get("x-requests-remaining", "?")
                    try:
                        quota_remaining = int(remaining_header)
                    except (ValueError, TypeError):
                        quota_remaining = 0
                    p = health["odds_api"]
                    p["response_time_ms"] = elapsed_ms
                    p["quota_remaining"] = quota_remaining
                    p["calls_today"] += 1
                    p["calls_this_month"] += 1
                    p["success_count"] += 1
                    total = p["success_count"] + p["failure_count"]
                    p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
                    results["The Odds API"] = f"HTTP {resp.status} | {remaining_header} remaining"
        except Exception as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["odds_api"]
            p["response_time_ms"] = elapsed_ms
            p["failure_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
            results["The Odds API"] = f"FAIL: {e}"
            failed += 1
    else:
        results["The Odds API"] = "SKIP: no key"

    # ── WeatherAPI ──
    if settings.weatherapi_key:
        checked += 1
        t0 = datetime.now()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"http://api.weatherapi.com/v1/current.json?key={settings.weatherapi_key}&q=Paris",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
                    p = health["weather_api"]
                    p["response_time_ms"] = elapsed_ms
                    p["calls_today"] += 1
                    p["calls_this_month"] += 1
                    p["success_count"] += 1
                    total = p["success_count"] + p["failure_count"]
                    p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
                    results["WeatherAPI"] = f"HTTP {resp.status} | {data.get('current',{}).get('temp_c','?')}C"
        except Exception as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["weather_api"]
            p["response_time_ms"] = elapsed_ms
            p["failure_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
            results["WeatherAPI"] = f"FAIL: {e}"
            failed += 1
    else:
        results["WeatherAPI"] = "SKIP: no key"

    # ── Sportmonks (DEPRECATED: 2026-07-17) ──
    results["Sportmonks"] = "DEPRECATED — removed from production 2026-07-17"

    # ── PostgreSQL ──
    container = Container()
    t0 = datetime.now()
    try:
        container.init_resources()
        async with container.database.session() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["postgresql"]
            p["response_time_ms"] = elapsed_ms
            p["success_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
            results["PostgreSQL"] = "OK — connected"
            checked += 1
    except Exception as e:
        elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
        p = health["postgresql"]
        p["response_time_ms"] = elapsed_ms
        p["failure_count"] += 1
        total = p["success_count"] + p["failure_count"]
        p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
        results["PostgreSQL"] = f"FAIL: {e}"
        failed += 1
    finally:
        await container.shutdown_resources()

    # ── Redis ──
    t0 = datetime.now()
    try:
        import redis.asyncio as redis
        r = redis.Redis(host=settings.redis_host or "localhost", port=settings.redis_port or 6379, db=0)
        await r.ping()
        await r.aclose()
        elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
        p = health["redis"]
        p["response_time_ms"] = elapsed_ms
        p["success_count"] += 1
        total = p["success_count"] + p["failure_count"]
        p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
        results["Redis"] = "OK — PONG"
        checked += 1
    except Exception as e:
        elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
        p = health["redis"]
        p["response_time_ms"] = elapsed_ms
        p["failure_count"] += 1
        total = p["success_count"] + p["failure_count"]
        p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
        results["Redis"] = f"FAIL: {e}"
        failed += 1

    # ── OpenAI ──
    if settings.openai_api_key:
        checked += 1
        t0 = datetime.now()
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=15.0)
            try:
                resp = await client.responses.create(
                    model=settings.openai_model,
                    input="Reply with OK.",
                    reasoning={"effort": "none"},
                    max_output_tokens=8,
                    store=False,
                )
            finally:
                await client.close()
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["openai"]
            p["response_time_ms"] = elapsed_ms
            p["calls_today"] += 1
            p["calls_this_month"] += 1
            p["success_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 100.0
            results["OpenAI"] = f"OK — {resp.model}"
        except Exception as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            p = health["openai"]
            p["response_time_ms"] = elapsed_ms
            p["failure_count"] += 1
            total = p["success_count"] + p["failure_count"]
            p["uptime"] = round(p["success_count"] / total * 100, 1) if total > 0 else 0.0
            results["OpenAI"] = f"FAIL: {e}"
            failed += 1
    else:
        results["OpenAI"] = "SKIP: no key"

    # Set quota_reset: next midnight UTC
    next_reset = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat() + "Z"
    for pname in provider_names:
        if health[pname].get("quota_reset", "") == "":
            health[pname]["quota_reset"] = next_reset

    # ── Enhancement 1: Append 30-day history record for each provider ──
    today_str = datetime.now(PARIS).strftime("%Y-%m-%d")
    for pname in provider_names:
        p = health[pname]
        if "history" not in p:
            p["history"] = []
        record = {
            "date": today_str,
            "uptime": p.get("uptime", 0),
            "calls": p.get("calls_today", 0),
            "avg_response_ms": p.get("response_time_ms", 0),
        }
        # Remove any existing record for today (idempotent)
        p["history"] = [h for h in p["history"] if h.get("date") != today_str]
        p["history"].append(record)
        # Rolling 30-day window
        p["history"] = p["history"][-30:]

    # ── Enhancement 2: Response time baseline vs 7-day average ──
    for pname in provider_names:
        p = health[pname]
        history = p.get("history", [])
        current_ms = p.get("response_time_ms", 0)
        # Compute 7-day baseline from history (exclude today)
        past_7 = [h for h in history if h.get("date") != today_str]
        if past_7:
            past_7 = past_7[-7:]
            baseline_avg = sum(h.get("avg_response_ms", 0) for h in past_7) / len(past_7)
            p["resp_baseline_avg_ms"] = round(baseline_avg, 1)
            p["resp_baseline_range"] = [
                min(h.get("avg_response_ms", 0) for h in past_7),
                max(h.get("avg_response_ms", 0) for h in past_7),
            ]
        else:
            p["resp_baseline_avg_ms"] = current_ms
            p["resp_baseline_range"] = [current_ms, current_ms]

        if current_ms > 0 and len(past_7) > 0 and baseline_avg > 0:
            if current_ms > 5 * baseline_avg:
                p["resp_alert"] = "CRITICAL"
                log.warning("Provider %s response CRITICAL: %d ms vs baseline %.1f ms",
                            pname, current_ms, baseline_avg)
            elif current_ms > 2 * baseline_avg:
                p["resp_alert"] = "WARNING"
                log.warning("Provider %s response WARNING: %d ms vs baseline %.1f ms",
                            pname, current_ms, baseline_avg)
            else:
                p["resp_alert"] = "OK"
        else:
            p["resp_alert"] = "OK"

    # Save persistent health data
    _save_provider_health(health)

    log.info("Provider Health Check: %d/%d passed | %s",
             checked - failed, checked,
             " | ".join(f"{k}={v}" for k, v in results.items()))

    # Write timestamped health report
    now_str = datetime.now(PARIS).strftime("%Y%m%d_%H%M%S")
    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"provider_health_{now_str}.json"
    report_path.write_text(json.dumps({
        "timestamp": datetime.now(PARIS).isoformat(),
        "providers_checked": checked,
        "providers_failed": failed,
        "results": results,
    }, indent=2), encoding="utf-8")


async def _run_daily_run(log: logging.Logger, *, failure_reason: str = "", failed_provider: str = "") -> None:
    """Full production pipeline: fixtures → odds → picks → settlement → performance."""
    from app.core.container import Container
    from app.workers.daily_job import run_daily_job

    # Initialize run_status on start
    run_status = _load_run_status()
    run_status["last_daily_run"] = datetime.now(PARIS).isoformat()
    run_status["status"] = "running"
    run_status["failure_reason"] = failure_reason
    run_status["failed_provider"] = failed_provider
    run_status["recovery_attempted"] = run_status.get("recovery_attempted", False)
    run_status["recovery_result"] = None
    _save_run_status(run_status)

    container = Container()
    container.init_resources()
    try:
        today = date.today()
        report = await run_daily_job(container, today)
        log.info(
            "Daily run complete: fixtures(c=%d u=%d) odds(m=%d s=%d) picks(a=%d v=%d) "
            "settle(c=%d s=%d pl=%s) perf(b=%d wr=%s pl=%s)",
            report.fixtures.fixtures_created if report.fixtures else 0,
            report.fixtures.fixtures_updated if report.fixtures else 0,
            report.odds.events_matched if report.odds else 0,
            report.odds.snapshots_created if report.odds else 0,
            report.picks.fixtures_analyzed,
            report.picks.value_bets_created,
            report.settlement.fixtures_checked if report.settlement else 0,
            report.settlement.bets_settled if report.settlement else 0,
            report.settlement.total_pl if report.settlement else 0,
            report.performance.total_bets if report.performance else 0,
            f"{report.performance.win_rate:.2%}" if (report.performance and report.performance.win_rate) else "N/A",
            report.performance.total_pl if report.performance else 0,
        )
        # Success — update run_status
        run_status["status"] = "success"
        run_status["failure_reason"] = ""
        run_status["failed_provider"] = ""
        _save_run_status(run_status)
    except Exception:
        run_status["status"] = "failed"
        _save_run_status(run_status)
        raise
    finally:
        await container.shutdown_resources()


RUN_STATUS_FILE = PROJECT_ROOT / "data" / "run_status.json"


def _load_run_status() -> dict:
    if RUN_STATUS_FILE.exists():
        try:
            return json.loads(RUN_STATUS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_run_status(data: dict) -> None:
    RUN_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_STATUS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )


async def _run_recovery_check(log: logging.Logger) -> None:
    """Smart recovery — check heartbeat, classify failure reason, decide RETRY or SKIP."""
    heartbeat = _load_heartbeat()
    daily = heartbeat.get("daily_run", {})
    today_str = datetime.now(PARIS).strftime("%Y-%m-%d")
    last_start = str(daily.get("last_start", ""))

    if today_str in last_start and daily.get("status") == "success":
        log.info("Recovery check: daily_run already succeeded at %s", last_start)
        return

    recovery_self = heartbeat.get("recovery_check", {})
    recovery_last = str(recovery_self.get("last_start", ""))
    if today_str in recovery_last:
        log.info("Recovery check: already triggered today, skipping.")
        return

    # Determine failure reason
    run_status = _load_run_status()
    failure_reason = run_status.get("failure_reason", "unknown")
    failed_provider = run_status.get("failed_provider", "")
    recovery_attempted = run_status.get("recovery_attempted", False)

    # Classification logic
    SKIP_REASONS = {"api_error", "no_fixtures"}
    RETRY_REASONS = {"db_error", "redis_error", "timeout", "unknown"}

    if failure_reason in SKIP_REASONS:
        decision = "SKIP"
        log.warning(
            "Recovery check: failure_reason=%s provider=%s → %s (persistent issue, skip)",
            failure_reason, failed_provider, decision,
        )
    elif failure_reason in RETRY_REASONS:
        if recovery_attempted:
            decision = "SKIP"
            log.warning(
                "Recovery check: failure_reason=%s already retried → %s (no more retries)",
                failure_reason, decision,
            )
        else:
            decision = "RETRY"
            log.warning(
                "Recovery check: failure_reason=%s provider=%s → %s (transient, retrying)",
                failure_reason, failed_provider, decision,
            )
    else:
        if recovery_attempted:
            decision = "SKIP"
        else:
            decision = "RETRY"

    # Update run_status
    run_status["recovery_attempted"] = True
    run_status["recovery_decision"] = decision
    run_status["recovery_time"] = datetime.now(PARIS).isoformat()
    _save_run_status(run_status)

    if decision == "RETRY":
        log.info("Recovery check: triggering recovery daily_run...")
        try:
            await _run_daily_run(log)
            run_status["recovery_result"] = "success"
            _save_run_status(run_status)
            log.info("Recovery run complete.")
        except Exception as exc:
            run_status["recovery_result"] = f"failed: {exc}"
            _save_run_status(run_status)
            log.error("Recovery run failed: %s", exc)
            # Placeholder notification
            log.info("[NOTIFY] Recovery failed — send Telegram/email notification here.")
    else:
        log.info("Recovery check: decision=%s, no retry triggered.", decision)
        # Placeholder notification for persistent failures
        if failure_reason not in {"no_fixtures"}:
            log.info("[NOTIFY] Daily run skipped due to %s — notify admin.", failure_reason)


async def _run_pre_kickoff(log: logging.Logger) -> None:
    """Refresh odds/lineups/injuries/weather for fixtures T-90 and T-30 from kickoff."""
    from app.core.container import Container
    from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
    from app.repositories.sqlalchemy.reference_repositories import (
        SqlAlchemyCompetitionRepository, SqlAlchemyTeamRepository,
    )
    from app.config.whitelist import get_whitelist
    from app.repositories.sqlalchemy.decision_log_repository import SqlAlchemyDecisionLogRepository
    from app.services.fixture_analysis import FixtureAnalysisService
    from app.services.committee_review import CommitteeReviewService
    from app.services.prediction_logger import log_fixture_predictions

    TRIGGER_LOG = PROJECT_ROOT / "app" / "state" / "pre_kickoff_triggers.json"

    def load_triggers() -> dict:
        if TRIGGER_LOG.exists():
            try:
                return json.loads(TRIGGER_LOG.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_triggers(data: dict) -> None:
        TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER_LOG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    container = Container()
    container.init_resources()
    try:
        async with container.database.session() as session:
            now = datetime.now(tz.utc)
            window_end = now + timedelta(minutes=100)
            fixtures = await SqlAlchemyFixtureRepository(session).list_by_kickoff_window(now, window_end)

            triggers = load_triggers()
            whitelist = get_whitelist()
            now_ts = now.isoformat()
            processed = 0
            skipped_unsupported = 0

            for fixture in fixtures:
                minutes_to_ko = (fixture.kickoff - now).total_seconds() / 60
                window = None
                if 85 <= minutes_to_ko <= 95:
                    window = "T-90"
                elif 25 <= minutes_to_ko <= 35:
                    window = "T-30"
                if window is None:
                    continue

                fixture_key = f"{fixture.id}:{window}"
                if fixture_key in triggers:
                    continue

                triggers[fixture_key] = now_ts
                processed += 1

                try:
                    comp = await SqlAlchemyCompetitionRepository(session).get(fixture.competition_id)
                    comp_name = comp.name if comp else str(fixture.competition_id)
                    # Resolve league_id + country for exact-match whitelist
                    league_id: int | None = None
                    country = comp.country if comp else None
                    if comp and comp.external_id:
                        try:
                            league_id = int(comp.external_id)
                        except (ValueError, TypeError):
                            pass
                    if not whitelist.is_allowed(comp_name, league_id=league_id, country=country):
                        skipped_unsupported += 1
                        continue

                    home = await SqlAlchemyTeamRepository(session).get(fixture.home_team_id)
                    away = await SqlAlchemyTeamRepository(session).get(fixture.away_team_id)
                    home_name = home.name if home else str(fixture.home_team_id)
                    away_name = away.name if away else str(fixture.away_team_id)

                    analysis = FixtureAnalysisService()
                    detailed = await analysis.analyze_detailed(fixture)

                    await log_fixture_predictions(
                        detailed, session=session,
                        competition_name=comp_name, home_team_name=home_name,
                        away_team_name=away_name, model_version="pre_kickoff",
                    )

                    qualifying = [
                        s for s in detailed.result.selections
                        if s.expected_value > 0.03 and s.confidence > 0.5
                    ]
                    if qualifying:
                        dec_log = SqlAlchemyDecisionLogRepository(session)
                        existing = await dec_log.list_by_fixture(fixture.id)
                        if not existing:
                            review = CommitteeReviewService(session=session)
                            result = await review.review_detailed(detailed)
                            log.info("Pre-kickoff reviewed fixture %s: %d value bets",
                                     fixture.id, len(result.value_bet_ids))
                except Exception as exc:
                    save_triggers(triggers)
                    log.warning("Pre-kickoff error fixture=%s: %s", fixture.id, exc)

            save_triggers(triggers)
            log.info("Pre-kickoff: %d processed, %d skipped (unsupported)", processed, skipped_unsupported)

            # Trigger dashboard refresh if T-90 or T-30 was hit
            if processed > 0:
                log.info("Pre-kickoff: %d T-90/T-30 fixtures hit, triggering dashboard refresh.", processed)
                try:
                    await _run_dashboard_refresh(log)
                except Exception as dex:
                    log.warning("Pre-kickoff dashboard refresh failed: %s", dex)
    finally:
        await container.shutdown_resources()


async def _run_settlement_fallback(log: logging.Logger) -> None:
    """Settle all eligible paper bets — idempotent."""
    from app.core.container import Container
    from app.core.service_factory import build_settlement_service

    container = Container()
    container.init_resources()
    try:
        async with container.database.session() as session:
            svc = build_settlement_service(container, session)
            report = await svc.settle_all()
            log.info("Settlement: checked=%d eligible=%d settled=%d skipped=%d pl=%s",
                     report.fixtures_checked, report.bets_eligible,
                     report.bets_settled, report.bets_skipped, report.total_pl)
    finally:
        await container.shutdown_resources()

    # Post-match dashboard refresh
    try:
        log.info("SettlementFallback: triggering post-match dashboard refresh.")
        await _run_dashboard_refresh(log)
    except Exception as dex:
        log.warning("Post-match dashboard refresh failed: %s", dex)

    # ── Enhancement 4: Recalculate ROI after settlement ──
    try:
        await _compute_and_save_roi(log)
    except Exception as rex:
        log.warning("ROI refresh after settlement failed: %s", rex)


async def _run_daily_report(log: logging.Logger) -> None:
    """Generate daily performance report (Markdown)."""
    from app.core.container import Container

    container = Container()
    container.init_resources()
    try:
        async with container.database.session() as session:
            from sqlalchemy import text

            today_start = datetime.now(PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

            # Fixture count
            fixture_result = await session.execute(
                text("SELECT COUNT(*) FROM fixtures WHERE kickoff BETWEEN :s AND :e"),
                {"s": today_start, "e": today_end},
            )
            fixture_count = fixture_result.scalar() or 0

            # Predictions
            pred_result = await session.execute(
                text("SELECT COUNT(*) FROM predictions WHERE created_at BETWEEN :s AND :e"),
                {"s": today_start, "e": today_end},
            )
            pred_count = pred_result.scalar() or 0

            # Value bets
            bet_result = await session.execute(
                text("SELECT COUNT(*) FROM value_bets WHERE created_at BETWEEN :s AND :e"),
                {"s": today_start, "e": today_end},
            )
            bet_count = bet_result.scalar() or 0

            # Settlements
            settle_result = await session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(profit_loss), 0) FROM settlements "
                    "WHERE settled_at BETWEEN :s AND :e"
                ),
                {"s": today_start, "e": today_end},
            )
            settle_row = settle_result.fetchone()
            settle_count = settle_row[0] or 0
            settle_pl = settle_row[1] or 0

        # Build report
        today_str = today_start.strftime("%Y-%m-%d")
        report_lines = [
            f"# Daily Performance Report — {today_str}",
            "",
            f"Generated: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')} Europe/Paris",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Fixtures tracked | {fixture_count} |",
            f"| Predictions generated | {pred_count} |",
            f"| Value bets identified | {bet_count} |",
            f"| Bets settled | {settle_count} |",
            f"| Today P&L | {settle_pl:+.4f} units |",
            "",
        ]

        # Performance snapshots
        perf_result = await session.execute(
            text("SELECT * FROM performance_snapshots ORDER BY snapshot_date DESC LIMIT 1"),
        )
        perf_row = perf_result.fetchone()
        if perf_row:
            report_lines.extend([
                "## Performance Overview",
                "",
                f"| Metric | Value |",
                f"|---|---|",
                f"| Total bets | {getattr(perf_row, 'total_bets', 'N/A')} |",
                f"| Win rate | {getattr(perf_row, 'win_rate', 'N/A')} |",
                f"| Total P&L | {getattr(perf_row, 'total_pl', 'N/A')} |",
                f"| ROI | {getattr(perf_row, 'roi', 'N/A')} |",
                "",
            ])

        report_content = "\n".join(report_lines)

        # ── Enhancement 4: Include ROI metrics ──
        roi = _load_roi_metrics()
        if roi:
            report_lines.append("## ROI Metrics\n")
            report_lines.append("| Metric | Value |")
            report_lines.append("|---|---|")
            if "weekly" in roi:
                w = roi["weekly"]
                report_lines.append(f"| Weekly ROI | {w['roi_pct']:+.1f}% ({w['won']}/{w['bets']} bets) |")
            if "monthly" in roi:
                m = roi["monthly"]
                report_lines.append(f"| 30-Day ROI | {m['roi_pct']:+.1f}% ({m['won']}/{m['bets']} bets) |")
            if "win_rate" in roi:
                report_lines.append(f"| Win Rate | {roi['win_rate']['overall']}% |")
            if "total_pnl" in roi:
                report_lines.append(f"| Total P&L | {roi['total_pnl']:+.2f} units |")
            if "ev" in roi:
                report_lines.append(f"| Avg EV | {roi['ev']['avg_pct']}% |")
            if "kelly" in roi:
                report_lines.append(f"| Avg Kelly | {roi['kelly']['avg_fraction']:.2f} |")
            if "brier" in roi and roi["brier"]["score"] is not None:
                report_lines.append(f"| Brier Score | {roi['brier']['score']:.4f} |")
            if "clv" in roi:
                report_lines.append(f"| CLV | {roi['clv']['avg']:+.4f} ({roi['clv']['positive_pct']}% positive) |")
            report_lines.append("")

        report_content = "\n".join(report_lines)

        report_dir = PROJECT_ROOT / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"daily_performance_{today_str}.md"
        report_path.write_text(report_content, encoding="utf-8")
        log.info("Daily report written to %s", report_path)
    finally:
        await container.shutdown_resources()


async def _run_weekly_report(log: logging.Logger) -> None:
    """Generate weekly performance report (Markdown)."""
    from app.core.container import Container

    container = Container()
    container.init_resources()
    try:
        async with container.database.session() as session:
            from sqlalchemy import text

            now = datetime.now(PARIS)
            # Monday → this week starts today; other days → start from last Monday
            days_since_monday = now.weekday()  # 0=Monday
            week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
            week_end = week_start + timedelta(days=7)

            # Weekly settlements
            settle_result = await session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(profit_loss), 0) FROM settlements "
                    "WHERE settled_at BETWEEN :s AND :e"
                ),
                {"s": week_start, "e": week_end},
            )
            settle_row = settle_result.fetchone()
            settle_count = settle_row[0] or 0
            settle_pl = settle_row[1] or 0

            # Weekly predictions
            pred_result = await session.execute(
                text("SELECT COUNT(*) FROM predictions WHERE created_at BETWEEN :s AND :e"),
                {"s": week_start, "e": week_end},
            )
            pred_count = pred_result.scalar() or 0

            # Weekly value bets
            bet_result = await session.execute(
                text("SELECT COUNT(*) FROM value_bets WHERE created_at BETWEEN :s AND :e"),
                {"s": week_start, "e": week_end},
            )
            bet_count = bet_result.scalar() or 0

        report_lines = [
            f"# Weekly Performance Report",
            f"",
            f"**Period**: {week_start.strftime('%Y-%m-%d')} to {(week_end - timedelta(days=1)).strftime('%Y-%m-%d')}",
            f"**Generated**: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')} Europe/Paris",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Predictions generated | {pred_count} |",
            f"| Value bets identified | {bet_count} |",
            f"| Bets settled | {settle_count} |",
            f"| Weekly P&L | {settle_pl:+.4f} units |",
            "",
        ]

        perf_result = await session.execute(
            text("SELECT * FROM performance_snapshots ORDER BY snapshot_date DESC LIMIT 1"),
        )
        perf_row = perf_result.fetchone()
        if perf_row:
            report_lines.extend([
                "## Cumulative Performance",
                "",
                f"| Metric | Value |",
                f"|---|---|",
                f"| Total bets | {getattr(perf_row, 'total_bets', 'N/A')} |",
                f"| Win rate | {getattr(perf_row, 'win_rate', 'N/A')} |",
                f"| Total P&L | {getattr(perf_row, 'total_pl', 'N/A')} |",
                f"| ROI | {getattr(perf_row, 'roi', 'N/A')} |",
                "",
            ])

        report_content = "\n".join(report_lines)

        # ── Enhancement 4: Include ROI metrics ──
        roi = _load_roi_metrics()
        if roi:
            report_lines.append("## ROI Metrics\n")
            report_lines.append("| Metric | Value |")
            report_lines.append("|---|---|")
            if "weekly" in roi:
                w = roi["weekly"]
                report_lines.append(f"| Weekly ROI | {w['roi_pct']:+.1f}% ({w['won']}/{w['bets']} bets) |")
            if "monthly" in roi:
                m = roi["monthly"]
                report_lines.append(f"| 30-Day ROI | {m['roi_pct']:+.1f}% ({m['won']}/{m['bets']} bets) |")
            if "win_rate" in roi:
                report_lines.append(f"| Win Rate | {roi['win_rate']['overall']}% |")
            if "total_pnl" in roi:
                report_lines.append(f"| Total P&L | {roi['total_pnl']:+.2f} units |")
            if "ev" in roi:
                report_lines.append(f"| Avg EV | {roi['ev']['avg_pct']}% |")
            if "kelly" in roi:
                report_lines.append(f"| Avg Kelly | {roi['kelly']['avg_fraction']:.2f} |")
            if "brier" in roi and roi["brier"]["score"] is not None:
                report_lines.append(f"| Brier Score | {roi['brier']['score']:.4f} |")
            if "clv" in roi:
                report_lines.append(f"| CLV | {roi['clv']['avg']:+.4f} ({roi['clv']['positive_pct']}% positive) |")
            report_lines.append("")

        report_content = "\n".join(report_lines)

        report_dir = PROJECT_ROOT / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"weekly_performance_{week_start.strftime('%Y%m%d')}.md"
        report_path.write_text(report_content, encoding="utf-8")
        log.info("Weekly report written to %s", report_path)
    finally:
        await container.shutdown_resources()


async def _run_dashboard_refresh(log: logging.Logger) -> None:
    """Regenerate the full daily dashboard HTML from DB data and latest provider health."""
    from app.core.container import Container
    from app.dashboard.db_builder import build_daily_dashboard
    from app.dashboard.renderer import DashboardRenderer

    container = Container()
    container.init_resources()
    try:
        today = date.today()
        async with container.database.session() as session:
            data = await build_daily_dashboard(session, today, pipeline_version="scheduler")
            renderer = DashboardRenderer()
            html = renderer.render_daily_overview(data)

        output_dir = PROJECT_ROOT / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"dashboard_{today.isoformat()}.html"
        output_path.write_text(html, encoding="utf-8")
        log.info("Dashboard refreshed: %s (%d matches)", output_path, len(data.matches))
    finally:
        await container.shutdown_resources()


TASK_MAP = {
    "health_check": _run_health_check,
    "daily_run": _run_daily_run,
    "recovery_check": _run_recovery_check,
    "pre_kickoff": _run_pre_kickoff,
    "settlement_fallback": _run_settlement_fallback,
    "daily_report": _run_daily_report,
    "weekly_report": _run_weekly_report,
    "dashboard_refresh": _run_dashboard_refresh,
}


async def _compute_and_save_roi(log: logging.Logger) -> dict:
    """Enhancement 4: Compute ROI metrics from DB and save to data/roi_metrics.json."""
    from app.core.container import Container
    from sqlalchemy import text

    container = Container()
    container.init_resources()
    roi: dict = {}
    try:
        async with container.database.session() as session:
            now = datetime.now(PARIS)

            # ── Weekly ROI ──
            week_start = now - timedelta(days=7)
            wr = await session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(s.profit_loss), 0), "
                    "COALESCE(SUM(vb.stake_amount), 0), "
                    "SUM(CASE WHEN s.profit_loss > 0 THEN 1 ELSE 0 END) "
                    "FROM settlements s "
                    "JOIN value_bets vb ON s.value_bet_id = vb.id "
                    "WHERE s.settlement_timestamp BETWEEN :s AND :e"
                ),
                {"s": week_start, "e": now},
            )
            wr_row = wr.fetchone()
            w_bets = wr_row[0] or 0
            w_pnl = float(wr_row[1] or 0)
            w_staked = float(wr_row[2] or 0)
            w_won = wr_row[3] or 0
            roi["weekly"] = {
                "roi_pct": round(w_pnl / w_staked * 100, 1) if w_staked > 0 else 0,
                "bets": w_bets,
                "won": w_won,
                "staked": w_staked,
                "pnl": round(w_pnl, 2),
            }

            # ── 30-Day ROI ──
            month_start = now - timedelta(days=30)
            mr = await session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(s.profit_loss), 0), "
                    "COALESCE(SUM(vb.stake_amount), 0), "
                    "SUM(CASE WHEN s.profit_loss > 0 THEN 1 ELSE 0 END) "
                    "FROM settlements s "
                    "JOIN value_bets vb ON s.value_bet_id = vb.id "
                    "WHERE s.settlement_timestamp BETWEEN :s AND :e"
                ),
                {"s": month_start, "e": now},
            )
            mr_row = mr.fetchone()
            m_bets = mr_row[0] or 0
            m_pnl = float(mr_row[1] or 0)
            m_staked = float(mr_row[2] or 0)
            m_won = mr_row[3] or 0
            roi["monthly"] = {
                "roi_pct": round(m_pnl / m_staked * 100, 1) if m_staked > 0 else 0,
                "bets": m_bets,
                "won": m_won,
                "staked": m_staked,
                "pnl": round(m_pnl, 2),
            }

            # ── Win Rate (overall) ──
            wr_all = await session.execute(
                text(
                    "SELECT COUNT(*), SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) "
                    "FROM settlements"
                ),
            )
            wr_all_row = wr_all.fetchone()
            settle_total = wr_all_row[0] or 0
            settle_wins = wr_all_row[1] or 0
            roi["win_rate"] = {
                "overall": round(settle_wins / settle_total * 100, 1) if settle_total > 0 else 0,
                    }

            # ── Total P&L ──
            pl_result = await session.execute(
                text("SELECT COALESCE(SUM(profit_loss), 0) FROM settlements"),
            )
            total_pl = float(pl_result.scalar() or 0)
            roi["total_pnl"] = round(total_pl, 2)

            # ── Average EV from value_bets ──
            ev_result = await session.execute(
                text(
                    "SELECT AVG((odds_decimal - 1) * model_probability - (1 - model_probability)) "
                    "FROM value_bets "
                    "WHERE odds_decimal IS NOT NULL AND model_probability IS NOT NULL"
                ),
            )
            ev_val = ev_result.scalar()
            roi["ev"] = {
                "avg_pct": round(float(ev_val) * 100, 1) if ev_val is not None else 0,
            }

            # ── Kelly fraction from value_bets ──
            kelly_result = await session.execute(
                text(
                    "SELECT AVG(stake_fraction) FROM value_bets "
                    "WHERE stake_fraction IS NOT NULL"
                ),
            )
            kelly_val = kelly_result.scalar()
            roi["kelly"] = {
                "avg_fraction": round(float(kelly_val), 2) if kelly_val is not None else 0,
            }

            # ── Brier Score from predictions ──
            brier_result = await session.execute(
                text(
                    "SELECT AVG((p.market_probability - "
                    "CASE WHEN s.result = 'W' THEN 1 WHEN s.result = 'L' THEN 0 ELSE 0.5 END) * "
                    "(p.market_probability - "
                    "CASE WHEN s.result = 'W' THEN 1 WHEN s.result = 'L' THEN 0 ELSE 0.5 END)) "
                    "FROM predictions p "
                    "JOIN settlements s ON p.fixture_id = s.fixture_id "
                    "WHERE p.market_probability IS NOT NULL"
                ),
            )
            brier_val = brier_result.scalar()
            roi["brier"] = {
                "score": round(float(brier_val), 4) if brier_val is not None else None,
            }

            # ── CLV (closing line value) ── not yet implemented: table lacks opening_odds/closing_odds columns
            roi["clv"] = {"avg": 0.0, "positive_pct": 0.0}

            _save_roi_metrics(roi)
            log.info(
                "ROI computed: weekly=%+.1f%% monthly=%+.1f%% total_pl=%+.2f win_rate=%.1f%%",
                roi["weekly"]["roi_pct"], roi["monthly"]["roi_pct"],
                total_pl, roi["win_rate"]["overall"],
            )
    except Exception as e:
        log.warning("ROI computation failed (DB may be unavailable): %s", e)
        # Write fallback empty metrics
        roi = {
            "weekly": {"roi_pct": 0, "bets": 0, "won": 0, "staked": 0, "pnl": 0},
            "monthly": {"roi_pct": 0, "bets": 0, "won": 0, "staked": 0, "pnl": 0},
            "clv": {"avg": 0, "positive_pct": 0},
            "brier": {"score": None},
            "kelly": {"avg_fraction": 0},
            "ev": {"avg_pct": 0},
            "win_rate": {"overall": 0},
            "total_pnl": 0,
        }
        _save_roi_metrics(roi)
    finally:
        await container.shutdown_resources()
    return roi


# ===================================================================
# Main
# ===================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Football Agent Task Scheduler Runner")
    parser.add_argument("--task", required=True, choices=list(TASK_MAP),
                        help="Task ID to execute")
    parser.add_argument("--run-id", default="",
                        help="Unique run identifier (yyyyMMddHHmmss)")
    parser.add_argument("--trigger-source", default="manual",
                        choices=["scheduler", "manual"],
                        help="How this run was triggered")
    parser.add_argument("--failure-reason", default="",
                        choices=["", "db_error", "api_error", "redis_error", "no_fixtures", "timeout", "unknown"],
                        help="Pre-set failure reason for daily_run (used by recovery)")
    parser.add_argument("--failed-provider", default="",
                        help="Provider that caused the failure")
    args = parser.parse_args()

    task_name: str = args.task
    trigger_source: str = args.trigger_source
    run_id: str = args.run_id or datetime.now(PARIS).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    start_dt = datetime.now(PARIS)
    start_time = start_dt.isoformat()

    status = "success"
    error_detail = None
    lock_path = None

    sys.path.insert(0, str(PROJECT_ROOT))
    _ensure_dirs()
    log = _get_logger(task_name)

    try:
        lock_path = acquire_lock(task_name)
        if lock_path is None:
            log.warning("[SKIP] %s: lock held by another instance.", task_name)
            sys.exit(0)

        log.info("[START] %s trigger=%s run_id=%s pid=%d", task_name, trigger_source, run_id, os.getpid())

        coro = TASK_MAP[task_name](log)
        if task_name == "daily_run" and (args.failure_reason or args.failed_provider):
            coro = _run_daily_run(log, failure_reason=args.failure_reason, failed_provider=args.failed_provider)
        asyncio.run(coro)

        log.info("[DONE] %s completed.", task_name)

    except Exception:
        status = "failed"
        error_detail = traceback.format_exc()
        log.error("[FAIL] %s\n%s", task_name, error_detail)
        sys.exit(1)

    finally:
        end_dt = datetime.now(PARIS)
        duration_s = (end_dt - start_dt).total_seconds()

        write_heartbeat(
            task_name,
            start_time=start_time,
            end_time=end_dt.isoformat(),
            status=status,
            error=error_detail,
            trigger_source=trigger_source,
            run_id=run_id,
        )
        # ── Enhancement 3: Append timeline entry ──
        _append_timeline_entry(
            task=task_name,
            status=status,
            duration_s=duration_s,
            details="" if status == "success" else (error_detail[:80] if error_detail else ""),
            time_str=start_dt.strftime("%H:%M"),
        )
        if lock_path:
            release_lock(lock_path)
        log.info("[HEARTBEAT] %s: status=%s duration=%.1fs run_id=%s",
                 task_name, status, duration_s, run_id)


if __name__ == "__main__":
    main()
