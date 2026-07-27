"""Windows Task Scheduler runner wrapper.

Acquires file lock, executes target command, writes heartbeat, releases lock.
Usage: python -m app.workers.scheduler_runner --command daily_job
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
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_DIR = PROJECT_ROOT / ".lock"
HEARTBEAT_FILE = PROJECT_ROOT / "app" / "state" / "heartbeat.json"
LOG_DIR = PROJECT_ROOT / "app" / "state" / "logs"

PARIS = ZoneInfo("Europe/Paris")

type JsonObject = dict[str, object]

# ---------------------------------------------------------------------------
# Logging setup — rotating file logs per command
# ---------------------------------------------------------------------------
_LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_LOG_FILES = {
    "daily_job": "daily_production.log",
    "pre_kickoff": "pre_kickoff.log",
    "settlement": "settlement.log",
    "provider_health": "provider_health.log",
    "dashboard_refresh": "dashboard_refresh.log",
    "production_recovery": "production_recovery.log",
    "daily_report": "daily_report.log",
    "weekly_report": "weekly_report.log",
    "health_check": "health_check.log",
    "coverage_monitor": "coverage_monitor.log",
    "gate_funnel": "gate_funnel.log",
    "alert_monitor": "alert_monitor.log",
    "season_prep": "season_prep.log",
}

_loggers: dict[str, logging.Logger] = {}


def _get_logger(command_name: str) -> logging.Logger:
    """Return a rotating-file logger for the given command (cached)."""
    if command_name in _loggers:
        return _loggers[command_name]

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / _LOG_FILES.get(command_name, f"{command_name}.log")

    handler = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(_LOG_FORMAT)

    logger = logging.getLogger(f"scheduler.{command_name}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False  # don't duplicate to root

    # Also add a stream handler so Task Scheduler captures stderr/stdout
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(_LOG_FORMAT)
    logger.addHandler(stream_handler)

    _loggers[command_name] = logger
    return logger


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def _load_heartbeat() -> JsonObject:
    if HEARTBEAT_FILE.exists():
        try:
            loaded = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        data = cast("JsonObject", loaded)

        # ── Staleness cleanup: auto-clear health_check failures older than 24h ──
        # provider_health is the canonical health key; health_check is legacy.
        # If health_check last_start is >24h ago, drop it so stale failures
        # don't pollute the heartbeat view.
        now_ts = datetime.now(PARIS)
        stale_hc = data.get("health_check")
        if stale_hc and isinstance(stale_hc, dict):
            last_start = stale_hc.get("last_start", "")
            if last_start:
                try:
                    hc_ts = datetime.fromisoformat(str(last_start))
                    if (now_ts - hc_ts).total_seconds() > 86400:
                        del data["health_check"]
                        _save_heartbeat(data)
                except (ValueError, TypeError):
                    pass
        return data
    return {}


def _save_heartbeat(data: JsonObject) -> None:
    HEARTBEAT_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def write_heartbeat(
    command_name: str,
    start_time: str,
    end_time: str,
    status: str,
    error: str | None = None,
    *,
    trigger_source: str = "manual",
    task_name: str = "",
    scheduled_time: str = "",
    delay_seconds: float = 0.0,
    run_id: str = "",
    fixtures_count: int = 0,
    odds_count: int = 0,
    predictions_count: int = 0,
    bet_count: int = 0,
    watch_count: int = 0,
    no_bet_count: int = 0,
    settlements_count: int = 0,
    performance_snapshot: str = "",
) -> None:
    """Write or update heartbeat record with full diagnostics."""
    heartbeat = _load_heartbeat()
    record: JsonObject = {
        "trigger_source": trigger_source,
        "task_name": task_name or command_name,
        "scheduled_time": scheduled_time,
        "actual_start_time": start_time,
        "delay_seconds": delay_seconds,
        "run_id": run_id,
        "last_start": start_time,
        "last_end": end_time,
        "status": status,
        "error": error,
    }
    # Attach pipeline counters when present
    if fixtures_count or odds_count or predictions_count:
        record["fixtures_count"] = fixtures_count
        record["odds_count"] = odds_count
        record["predictions_count"] = predictions_count
        record["bet_count"] = bet_count
        record["watch_count"] = watch_count
        record["no_bet_count"] = no_bet_count
        record["settlements_count"] = settlements_count
    if performance_snapshot:
        record["performance_snapshot"] = performance_snapshot
    heartbeat[command_name] = record
    _save_heartbeat(heartbeat)


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

# Expected maximum run duration per command (seconds). Used to detect stale locks.
# Stale threshold = 2× expected, capped at 3600s (1h).
_EXPECTED_DURATION = {
    "daily_job": 900,  # 15 min
    "pre_kickoff": 300,  #  5 min
    "settlement": 120,  #  2 min
    "provider_health": 600,  # 10 min
    "dashboard_refresh": 120,  #  2 min (read-only)
    "production_recovery": 900,  # 15 min (may run daily_job)
    "daily_report": 120,  #  2 min (read-only)
    "weekly_report": 120,  #  2 min (read-only)
    "health_check": 120,  #  2 min (10 component checks)
    "coverage_monitor": 300,  #  5 min (DB queries across leagues)
    "gate_funnel": 120,  #  2 min (DB queries, read-only)
    "alert_monitor": 120,  #  2 min (DB + file checks)
    "season_prep": 900,  # 15 min (API calls for 5 leagues)
}
_DEFAULT_EXPECTED_DURATION = 300  # fallback for unknown commands


def _is_pid_alive(pid: int) -> bool:
    """Return True if the given process ID exists on this system."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stale_ttl(command_name: str) -> int:
    """Return the stale lock TTL in seconds for the given command."""
    return min(2 * _EXPECTED_DURATION.get(command_name, _DEFAULT_EXPECTED_DURATION), 3600)


def acquire_lock(command_name: str) -> str | None:
    """Try to acquire a file lock. Returns lock path on success, None if locked.

    If the lock is stale (PID dead or older than 2× expected run duration),
    it is broken and treated as a fresh acquisition.
    """
    _ensure_dirs()
    lock_path = LOCK_DIR / f"{command_name}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        pid = os.getpid()
        os.write(fd, f"{pid}\n{datetime.now(PARIS).isoformat()}\n".encode())
        os.close(fd)
        return str(lock_path)
    except FileExistsError:
        # Lock exists — check if it's stale
        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            lines = content.splitlines()
            old_pid = int(lines[0]) if lines else -1
            old_time_str = lines[1] if len(lines) > 1 else ""
        except (ValueError, OSError):
            old_pid = -1
            old_time_str = ""

        # Check 1: PID liveness
        if old_pid > 0 and _is_pid_alive(old_pid):
            return None  # real lock holder still running

        # Check 2: lock age
        stale = False
        if old_time_str:
            try:
                old_time = datetime.fromisoformat(old_time_str)
                age = (datetime.now(PARIS) - old_time).total_seconds()
                ttl = _stale_ttl(command_name)
                if age > ttl:
                    stale = True
            except (ValueError, TypeError):
                stale = True  # unparseable timestamp → treat as stale
        else:
            stale = True  # no timestamp → treat as stale

        if stale:
            logger = _get_logger(command_name)
            logger.warning(
                "Breaking stale lock for %s (pid=%s age=%s), re-acquiring.",
                command_name,
                old_pid,
                old_time_str or "unknown",
            )
            release_lock(str(lock_path))
            return acquire_lock(command_name)  # retry

        return None


def release_lock(lock_path: str) -> None:
    """Release a previously acquired file lock."""
    with suppress(OSError):
        os.remove(lock_path)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


async def _run_daily_job(log: logging.Logger) -> None:
    """Execute the daily production run (full pipeline)."""
    from datetime import date

    from app.core.container import container
    from app.workers.daily_job import run_daily_job

    container.init_resources()
    try:
        report = await run_daily_job(container, date.today())
        log.info(
            "Daily job complete: fixtures(created=%d updated=%d) odds(matched=%d snapshots=%d) "
            "picks(analyzed=%d qualified=%d reviewed=%d skipped=%d skipped_unsupported=%d value_bets=%d) "
            "settlement(checked=%d settled=%d pl=%s) performance(total_bets=%d win_rate=%s total_pl=%s)",
            report.fixtures.fixtures_created if report.fixtures else 0,
            report.fixtures.fixtures_updated if report.fixtures else 0,
            report.odds.events_matched if report.odds else 0,
            report.odds.snapshots_created if report.odds else 0,
            report.picks.fixtures_analyzed,
            report.picks.fixtures_qualified,
            report.picks.fixtures_reviewed,
            report.picks.fixtures_skipped_existing,
            report.picks.fixtures_skipped_unsupported_competition,
            report.picks.value_bets_created,
            report.settlement.fixtures_checked if report.settlement else 0,
            report.settlement.bets_settled if report.settlement else 0,
            report.settlement.total_pl if report.settlement else 0,
            report.performance.total_bets if report.performance else 0,
            (
                f"{report.performance.win_rate:.2%}"
                if (report.performance and report.performance.win_rate)
                else "N/A"
            ),
            report.performance.total_pl if report.performance else 0,
        )
    finally:
        await container.shutdown_resources()


async def _run_pre_kickoff(log: logging.Logger) -> None:
    """Pre-kickoff validation: re-evaluate fixtures inside T-90 and T-30 windows."""
    from datetime import timedelta

    from app.config.whitelist import get_whitelist
    from app.core.container import container
    from app.core.service_factory import (
        build_committee_review_service,
        build_fixture_analysis_service,
    )
    from app.repositories.sqlalchemy.decision_log_repository import (
        SqlAlchemyDecisionLogRepository,
    )
    from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
    from app.repositories.sqlalchemy.reference_repositories import (
        SqlAlchemyCompetitionRepository,
        SqlAlchemyTeamRepository,
    )
    from app.services.prediction_logger import log_fixture_predictions

    TRIGGER_LOG = PROJECT_ROOT / "app" / "state" / "pre_kickoff_triggers.json"

    def load_triggers() -> JsonObject:
        if TRIGGER_LOG.exists():
            try:
                loaded = json.loads(TRIGGER_LOG.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
            if isinstance(loaded, dict):
                return cast("JsonObject", loaded)
        return {}

    def save_triggers(data: JsonObject) -> None:
        TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER_LOG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    container.init_resources()
    try:
        async with container.database.session() as session:
            now = datetime.now(UTC)
            window_end = now + timedelta(minutes=100)
            fixtures = await SqlAlchemyFixtureRepository(session).list_by_kickoff_window(
                now, window_end
            )

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
                    comp_repo = SqlAlchemyCompetitionRepository(session)
                    team_repo = SqlAlchemyTeamRepository(session)
                    comp = await comp_repo.get(fixture.competition_id)
                    home = await team_repo.get(fixture.home_team_id)
                    away = await team_repo.get(fixture.away_team_id)

                    comp_name = comp.name if comp else str(fixture.competition_id)
                    home_name = home.name if home else str(fixture.home_team_id)
                    away_name = away.name if away else str(fixture.away_team_id)

                    # Whitelist gate — must match DailyTopPicksService to prevent leakage
                    # Resolve league_id + country for exact-match whitelist
                    league_id: int | None = None
                    country = comp.country if comp else None
                    if comp and comp.external_id:
                        with suppress(ValueError, TypeError):
                            league_id = int(comp.external_id)
                    if not whitelist.is_allowed(comp_name, league_id=league_id, country=country):
                        log.info(
                            "SKIPPED_UNSUPPORTED_COMPETITION fixture=%s competition=%s",
                            fixture.id,
                            comp_name,
                        )
                        skipped_unsupported += 1
                        continue

                    analysis = build_fixture_analysis_service(container, session)
                    detailed = await analysis.analyze_detailed(fixture)

                    await log_fixture_predictions(
                        detailed,
                        session=session,
                        competition_name=comp_name,
                        home_team_name=home_name,
                        away_team_name=away_name,
                        model_version="pre_kickoff",
                    )

                    qualifying = [
                        s
                        for s in detailed.result.selections
                        if s.expected_value > 0.03 and s.confidence > 0.5
                    ]
                    if qualifying:
                        dec_log_repo = SqlAlchemyDecisionLogRepository(session)
                        existing = await dec_log_repo.list_by_fixture(fixture.id)
                        if not existing:
                            review = build_committee_review_service(
                                container,
                                session,
                                analysis=analysis,
                            )
                            result = await review.review_detailed(detailed)
                            log.info(
                                "Pre-kickoff reviewed fixture %s: %d value bets",
                                fixture.id,
                                len(result.value_bet_ids),
                            )
                except Exception as exc:
                    save_triggers(triggers)
                    log.warning("Pre-kickoff error for fixture %s: %s", fixture.id, exc)

            save_triggers(triggers)
            log.info(
                "Pre-kickoff validation complete: %d fixture(s) processed, "
                "%d skipped (unsupported competition)",
                processed,
                skipped_unsupported,
            )
    finally:
        await container.shutdown_resources()


async def _run_settlement(log: logging.Logger) -> None:
    """Settlement fallback: settle fixtures not yet settled."""
    from app.core.container import container
    from app.core.service_factory import build_settlement_service

    container.init_resources()
    try:
        async with container.database.session() as session:
            svc = build_settlement_service(container, session)
            report = await svc.settle_all()
            log.info(
                "Settlement complete: checked=%d eligible=%d settled=%d skipped=%d total_pl=%s",
                report.fixtures_checked,
                report.bets_eligible,
                report.bets_settled,
                report.bets_skipped,
                report.total_pl,
            )
    finally:
        await container.shutdown_resources()


async def _run_provider_health(log: logging.Logger) -> None:
    """Provider health check: verify connectivity/status of all 7 providers."""
    from app.core.container import container

    log.info("Provider health check starting...")

    # Check PostgreSQL connectivity
    pg_ok = False
    try:
        container.init_resources()
        try:
            async with container.database.session() as session:
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))
                pg_ok = True
        finally:
            await container.shutdown_resources()
    except Exception as exc:
        log.warning("PostgreSQL: FAILED — %s", exc)

    log.info("PostgreSQL: %s", "OK" if pg_ok else "FAILED")

    # Read provider_health.json (generated by external health check pipeline)
    providers_ok = 0
    providers_total = 0
    health_path = PROJECT_ROOT / "data" / "provider_health.json"
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
            provider_keys = [
                "api_football",
                "odds_api",
                "weather_api",
                "postgresql",
                "redis",
                "openai",
            ]
            for key in provider_keys:
                p = health.get(key, {})
                uptime = p.get("uptime", "N/A") if isinstance(p, dict) else "N/A"
                if isinstance(uptime, (int, float)):
                    providers_total += 1
                    if uptime >= 99:
                        providers_ok += 1
                        log.info("Provider %s: OK (uptime=%.1f%%)", key, uptime)
                    else:
                        log.warning("Provider %s: DEGRADED (uptime=%.1f%%)", key, uptime)
                else:
                    log.info("Provider %s: status=N/A", key)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read provider_health.json: %s", exc)
    else:
        log.warning("provider_health.json not found at %s", health_path)

    summary = f"PG={'OK' if pg_ok else 'FAILED'} " f"providers={providers_ok}/{providers_total}"
    if providers_ok >= 5:
        _append_run_timeline("provider_health", "success", summary)
    else:
        _append_run_timeline("provider_health", "degraded", summary)

    log.info("Provider health check complete: %s", summary)


async def _run_production_recovery(log: logging.Logger) -> None:
    """Production recovery: read heartbeat/run_status, decide RETRY|SKIP, rerun daily_job if needed."""
    from datetime import date, datetime

    from app.core.container import container
    from app.workers.daily_job import run_daily_job

    today_str = datetime.now(PARIS).strftime("%Y-%m-%d")

    # ── Gate 1: daily_job already succeeded today ──
    heartbeat = _load_heartbeat()
    daily_value = heartbeat.get("daily_job", {})
    daily = cast("JsonObject", daily_value) if isinstance(daily_value, dict) else {}
    last_start = daily.get("last_start", "")
    if today_str in str(last_start) and daily.get("status") == "success":
        log.info("Recovery SKIP: daily_job already succeeded at %s", last_start)
        _append_run_timeline("production_recovery", "success", "SKIP: daily_job succeeded")
        return

    # ── Gate 2: recovery already attempted today ──
    recovery_value = heartbeat.get("production_recovery", {})
    recovery_own = cast("JsonObject", recovery_value) if isinstance(recovery_value, dict) else {}
    recovery_last = recovery_own.get("last_start", "")
    if today_str in str(recovery_last):
        log.info("Recovery SKIP: already attempted today at %s", recovery_last)
        _append_run_timeline("production_recovery", "success", "SKIP: already attempted")
        return

    # ── Read failure reason from run_status.json ──
    failure_reason = "unknown"
    run_status_path = PROJECT_ROOT / "data" / "run_status.json"
    if run_status_path.exists():
        try:
            run_status = json.loads(run_status_path.read_text(encoding="utf-8"))
            failure_reason = run_status.get("failure_reason", "unknown")
        except (json.JSONDecodeError, OSError):
            pass

    # If daily_job heartbeat is missing entirely, treat as unknown → RETRY
    if not daily:
        failure_reason = failure_reason or "unknown"

    # ── Decision matrix ──
    RETRY_REASONS = {"db_error", "redis_error", "timeout", "unknown"}
    SKIP_REASONS = {"api_error", "no_fixtures", "None"}

    if failure_reason in SKIP_REASONS or daily.get("status") == "success":
        decision = "SKIP"
    elif failure_reason in RETRY_REASONS:
        decision = "RETRY"
    else:
        # daily_job failed with non-standard reason — one best-effort retry
        decision = "RETRY"

    log.info("Recovery decision=%s failure_reason=%s", decision, failure_reason)

    if decision == "SKIP":
        _append_run_timeline("production_recovery", "success", f"SKIP: reason={failure_reason}")
        return

    # ── RETRY: execute daily_job inline ──
    log.warning("Recovery RETRY: triggering daily_job rerun...")
    try:
        container.init_resources()
        try:
            report = await run_daily_job(container, date.today())
            log.info("Recovery run complete: picks=%d", report.picks.value_bets_created)
            _append_run_timeline(
                "production_recovery",
                "success",
                f"RETRY success: picks={report.picks.value_bets_created}",
            )

            # ── Update run_status.json so it doesn't stay permanently in failed state ──
            if run_status_path.exists():
                try:
                    rs = json.loads(run_status_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    rs = {}
                rs["status"] = "recovered"
                rs["recovery_time"] = datetime.now(PARIS).isoformat()
                rs["recovery_attempted"] = True
                rs["recovery_result"] = "success"
                run_status_path.write_text(
                    json.dumps(rs, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                log.info("Updated run_status.json: status=recovered")
        finally:
            await container.shutdown_resources()
    except Exception as exc:
        log.error("Recovery run failed: %s", exc)
        _append_run_timeline("production_recovery", "failed", str(exc)[:500])
        raise


async def _run_daily_report(log: logging.Logger) -> None:
    """Generate daily performance report from PostgreSQL (read-only, zero API calls)."""
    from datetime import date, datetime

    from app.core.container import container

    today = date.today()
    iso = today.isoformat()
    now = datetime.now(PARIS)

    container.init_resources()
    try:
        async with container.database.session() as session:
            from sqlalchemy import func, select

            from app.repositories.sqlalchemy.models import (
                PREDICTION_RECORD_DECISION,
                PredictionORM,
                SettlementORM,
                ValueBetORM,
            )

            # Predictions count (all-time, for reference)
            pred_total = (
                await session.scalar(
                    select(func.count(PredictionORM.id)).where(
                        PredictionORM.record_kind == PREDICTION_RECORD_DECISION
                    )
                )
            ) or 0

            # Today's settlements
            settled_row = (
                await session.execute(
                    select(
                        func.count(SettlementORM.id),
                        func.coalesce(func.sum(SettlementORM.profit_loss), 0.0),
                    ).where(func.date(SettlementORM.settlement_timestamp) == today)
                )
            ).one_or_none()
            settled_count, settled_pl = settled_row if settled_row else (0, 0.0)
            settled_pl = float(settled_pl)

            # Value bets (all-time)
            vb_total = (await session.scalar(select(func.count(ValueBetORM.id)))) or 0

        # Build Markdown report
        lines = [
            f"# Daily Performance Report — {iso}",
            "",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (Europe/Paris)",
            "",
            "## Summary",
            f"- Predictions (total): {pred_total}",
            f"- Settlements today: {settled_count}",
            f"- Settlements P&L: {settled_pl:+.2f}",
            f"- Value Bets (total): {vb_total}",
            "",
            "## Notes",
            "- Report generated from PostgreSQL (read-only)",
            "- Zero API calls made during generation",
            "",
        ]

        report_dir = PROJECT_ROOT / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"daily_report_{iso}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")

        elapsed = (datetime.now(PARIS) - now).total_seconds()
        _append_run_timeline(
            "daily_report", "success", f"daily_report_{iso}.md ({report_path.stat().st_size} bytes)"
        )

        log.info(
            "Daily report written: %s (%d bytes, %.1fs)",
            report_path,
            report_path.stat().st_size,
            elapsed,
        )
    finally:
        await container.shutdown_resources()


async def _run_weekly_report(log: logging.Logger) -> None:
    """Generate weekly performance report from PostgreSQL (read-only, zero API calls)."""
    from datetime import date, datetime, timedelta

    from app.core.container import container

    today = date.today()
    iso = today.isoformat()
    week_start = today - timedelta(days=7)
    now = datetime.now(PARIS)

    container.init_resources()
    try:
        async with container.database.session() as session:
            from sqlalchemy import func, select

            from app.repositories.sqlalchemy.models import (
                PREDICTION_RECORD_DECISION,
                PredictionORM,
                SettlementORM,
                ValueBetORM,
            )

            # 7-day settlements
            settled_row = (
                await session.execute(
                    select(
                        func.count(SettlementORM.id),
                        func.coalesce(func.sum(SettlementORM.profit_loss), 0.0),
                    ).where(func.date(SettlementORM.settlement_timestamp) >= week_start)
                )
            ).one_or_none()
            week_settled, week_pl = settled_row if settled_row else (0, 0.0)
            week_pl = float(week_pl)

            # 7-day predictions
            pred_7d = (
                await session.scalar(
                    select(func.count(PredictionORM.id)).where(
                        PredictionORM.record_kind == PREDICTION_RECORD_DECISION,
                        func.date(PredictionORM.created_at) >= week_start,
                    )
                )
            ) or 0

            # All-time totals
            vb_total = (await session.scalar(select(func.count(ValueBetORM.id)))) or 0
            settled_total = (await session.scalar(select(func.count(SettlementORM.id)))) or 0

        # Read weekly run_timeline for scheduler reliability
        timeline_info = ""
        if RUN_TIMELINE_FILE.exists():
            try:
                timeline = json.loads(RUN_TIMELINE_FILE.read_text(encoding="utf-8"))
                week_entries = [
                    e for e in timeline if e.get("time", "").split("T")[0] >= week_start.isoformat()
                ]
                ok_count = sum(1 for e in week_entries if e.get("status") == "success")
                timeline_info = (
                    f"- Scheduler runs this week: {len(week_entries)} ({ok_count} success)"
                )
            except (json.JSONDecodeError, OSError):
                pass

        lines = [
            f"# Weekly Performance Report — Week ending {iso}",
            "",
            f"Period: {week_start.isoformat()} to {iso}",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (Europe/Paris)",
            "",
            "## Settlements (7 days)",
            f"- Total settled: {week_settled}",
            f"- P&L: {week_pl:+.2f}",
            "",
            "## Predictions (7 days)",
            f"- Predictions created: {pred_7d}",
            "",
            "## All-Time",
            f"- Total value bets: {vb_total}",
            f"- Total settlements: {settled_total}",
            "",
            "## Scheduler Reliability",
        ]
        if timeline_info:
            lines.append(timeline_info)
        else:
            lines.append("- (run_timeline.json not available)")

        lines += [
            "",
            "## Notes",
            "- Report generated from PostgreSQL (read-only)",
            "- Zero API calls made during generation",
            "",
        ]

        report_dir = PROJECT_ROOT / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"weekly_report_{iso}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")

        elapsed = (datetime.now(PARIS) - now).total_seconds()
        _append_run_timeline(
            "weekly_report",
            "success",
            f"weekly_report_{iso}.md ({report_path.stat().st_size} bytes)",
        )

        log.info(
            "Weekly report written: %s (%d bytes, %.1fs)",
            report_path,
            report_path.stat().st_size,
            elapsed,
        )
    finally:
        await container.shutdown_resources()


# ---------------------------------------------------------------------------
# Run timeline helper
# ---------------------------------------------------------------------------
RUN_TIMELINE_FILE = PROJECT_ROOT / "data" / "run_timeline.json"


def _append_run_timeline(
    task: str,
    status: str,
    details: str = "",
    duration_s: float = 0.0,
) -> None:
    """Append an entry to data/run_timeline.json for dashboard display."""
    now = datetime.now(PARIS)
    entry = {
        "time": now.strftime("%H:%M"),
        "task": task,
        "status": status,
        "duration_s": round(duration_s, 3),
        "details": str(details)[:500] if details else "",
    }
    existing: list[JsonObject] = []
    if RUN_TIMELINE_FILE.exists():
        try:
            loaded = json.loads(RUN_TIMELINE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        else:
            if isinstance(loaded, list):
                existing = [cast("JsonObject", item) for item in loaded if isinstance(item, dict)]
    existing.append(entry)
    RUN_TIMELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_TIMELINE_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Command: dashboard_refresh
# ---------------------------------------------------------------------------
async def _run_dashboard_refresh(log: logging.Logger) -> None:
    """Regenerate Dashboard HTML from existing data. Read-only, zero API calls."""
    from datetime import date, datetime
    from pathlib import Path

    from app.core.container import container
    from app.dashboard.db_builder import build_daily_dashboard
    from app.dashboard.renderer import DashboardRenderer

    today = date.today()
    iso = today.isoformat()
    t0 = datetime.now(PARIS)

    container.init_resources()
    try:
        log.info("Dashboard refresh: connecting to DB (read-only)...")
        async with container.database.session() as session:
            daily_data = await build_daily_dashboard(
                session,
                today,
                pipeline_version="production",
            )

        summary = daily_data.executive_summary
        log.info(
            "Dashboard data built: fixtures=%d predictions=%d gate_approved=%d bets=%d settlements=%d",
            summary.fixtures_total if summary else 0,
            summary.predictions_total if summary else 0,
            summary.gate_approved_count if summary else 0,
            summary.bet_count if summary else 0,
            summary.settlements if summary else 0,
        )

        renderer = DashboardRenderer()
        html = renderer.render_daily_overview(daily_data)

        out_dir = Path(__file__).resolve().parents[2] / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        dashboard_path = out_dir / f"dashboard_{iso}.html"
        dashboard_path.write_text(html, encoding="utf-8")

        elapsed = (datetime.now(PARIS) - t0).total_seconds()
        _append_run_timeline(
            "dashboard_refresh",
            "success",
            f"dashboard_{iso}.html ({len(html):,} chars)",
            duration_s=elapsed,
        )

        log.info(
            "Dashboard refresh complete: %d chars → %s (%.1fs)", len(html), dashboard_path, elapsed
        )
    finally:
        await container.shutdown_resources()


# ---------------------------------------------------------------------------
# P1–P6 Production Readiness commands
# ---------------------------------------------------------------------------


async def _run_script(log: logging.Logger, script_name: str) -> None:
    """Run a Python script from the scripts/ directory with logging."""
    script_path = PROJECT_ROOT / "scripts" / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    log.info("Running script: %s", script_path)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        log.info("stdout:\n%s", stdout.decode(errors="replace")[:5000])
    if stderr:
        log.warning("stderr:\n%s", stderr.decode(errors="replace")[:5000])
    if proc.returncode != 0:
        raise RuntimeError(f"Script {script_name} exited with code {proc.returncode}")
    log.info("Script %s completed successfully.", script_name)


async def _run_health_check(log: logging.Logger) -> None:
    """P1: Daily Health Dashboard — check 10 components."""
    await _run_script(log, "health_check.py")


async def _run_coverage_monitor(log: logging.Logger) -> None:
    """P2: Data coverage monitor — per-league fixture/odds/prediction stats."""
    await _run_script(log, "coverage_monitor.py")


async def _run_gate_funnel(log: logging.Logger) -> None:
    """P3: Gate funnel — full pipeline funnel visualization."""
    await _run_script(log, "gate_funnel.py")


async def _run_alert_monitor(log: logging.Logger) -> None:
    """P4: Alert monitor — 8 alert conditions across severity levels."""
    await _run_script(log, "alert_monitor.py")


async def _run_season_prep(log: logging.Logger) -> None:
    """P6: Season prep — pre-season validation for Big 5 leagues."""
    await _run_script(log, "season_prep.py")


COMMANDS = {
    "daily_job": _run_daily_job,
    "pre_kickoff": _run_pre_kickoff,
    "settlement": _run_settlement,
    "provider_health": _run_provider_health,
    "dashboard_refresh": _run_dashboard_refresh,
    "production_recovery": _run_production_recovery,
    "daily_report": _run_daily_report,
    "weekly_report": _run_weekly_report,
    "health_check": _run_health_check,
    "coverage_monitor": _run_coverage_monitor,
    "gate_funnel": _run_gate_funnel,
    "alert_monitor": _run_alert_monitor,
    "season_prep": _run_season_prep,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Windows Task Scheduler runner")
    parser.add_argument(
        "--command",
        required=True,
        choices=sorted(COMMANDS.keys()),
        help="Which pipeline command to execute",
    )
    parser.add_argument(
        "--trigger-source",
        default="manual",
        choices=["scheduler", "manual"],
        help="How this run was triggered",
    )
    args = parser.parse_args()

    command_name: str = args.command
    trigger_source: str = args.trigger_source
    run_id = uuid.uuid4().hex[:12]
    start_dt = datetime.now(PARIS)
    start_time = start_dt.isoformat()
    scheduled_time = ""
    delay_seconds = 0.0

    status = "success"
    error_detail = None
    lock_path = None

    sys.path.insert(0, str(PROJECT_ROOT))
    _ensure_dirs()
    log = _get_logger(command_name)

    try:
        lock_path = acquire_lock(command_name)
        if lock_path is None:
            log.warning("[SKIP] %s: lock already held, another instance running.", command_name)
            sys.exit(0)

        log.info(
            "[START] %s trigger=%s run_id=%s pid=%d",
            command_name,
            trigger_source,
            run_id,
            os.getpid(),
        )

        coro = COMMANDS[command_name](log)
        asyncio.run(coro)

        log.info("[DONE] %s completed successfully.", command_name)

    except Exception:
        status = "failed"
        error_detail = traceback.format_exc()
        log.error("[FAIL] %s\n%s", command_name, error_detail)
        sys.exit(1)

    finally:
        end_dt = datetime.now(PARIS)
        end_time = end_dt.isoformat()
        duration = (end_dt - start_dt).total_seconds()

        write_heartbeat(
            command_name,
            start_time=start_time,
            end_time=end_time,
            status=status,
            error=error_detail,
            trigger_source=trigger_source,
            task_name=command_name,
            scheduled_time=scheduled_time,
            delay_seconds=delay_seconds,
            run_id=run_id,
        )

        if lock_path:
            release_lock(lock_path)

        log.info(
            "[HEARTBEAT] %s: status=%s duration=%.1fs run_id=%s",
            command_name,
            status,
            duration,
            run_id,
        )


if __name__ == "__main__":
    main()
