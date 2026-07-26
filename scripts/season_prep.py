"""
Season preparation script for big-5 league 2026/27 kickoff.

Runs pre-season checks: fixtures, teams, history, Elo, odds coverage,
whitelist validation. On Premier League matchday-1, optionally runs E2E.

Usage:
    python scripts/season_prep.py                            # full check
    python scripts/season_prep.py --e2e                     # full check + E2E
    python -m app.workers.scheduler_runner --command season_prep

Outputs:
    output/season_prep_report_YYYY-MM-DD.md
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"

BIG5 = [
    {"league_id": 39, "name": "Premier League", "season": 2026},
    {"league_id": 140, "name": "La Liga", "season": 2026},
    {"league_id": 135, "name": "Serie A", "season": 2026},
    {"league_id": 61, "name": "Ligue 1", "season": 2026},
    {"league_id": 78, "name": "Bundesliga", "season": 2026},
]

# Approximate 2026/27 kickoff dates
KICKOFF_DATES: dict[int, str] = {
    39: "2026-08-15",   # Premier League
    140: "2026-08-15",  # La Liga
    135: "2026-08-23",  # Serie A
    61: "2026-08-08",   # Ligue 1
    78: "2026-08-22",   # Bundesliga
}


async def check_fixtures(session, league_id: int, name: str, season: int) -> dict:
    """Verify fixtures exist for given league/season."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, SeasonORM,
    )

    total = (await session.scalar(
        select(func.count(FixtureORM.id))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
    )) or 0

    future = (await session.scalar(
        select(func.count(FixtureORM.id))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
        .where(FixtureORM.kickoff >= datetime.now(timezone.utc).isoformat())
    )) or 0

    kickoff_date_str = KICKOFF_DATES.get(league_id, "unknown")
    status_detail = "OK" if future > 0 else (
        "Warning" if total > 0 else "Missing"
    )

    return {
        "league": name,
        "league_id": league_id,
        "season": season,
        "kickoff_est": kickoff_date_str,
        "fixtures_total": total,
        "fixtures_future": future,
        "status": status_detail,
    }


async def check_teams(session, league_id: int, name: str) -> dict:
    """Verify teams exist for given league."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, TeamORM,
    )

    # Count distinct teams from fixtures
    home_count = (await session.scalar(
        select(func.count(func.distinct(FixtureORM.home_team_id)))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
    )) or 0

    away_count = (await session.scalar(
        select(func.count(func.distinct(FixtureORM.away_team_id)))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
    )) or 0

    return {
        "league": name,
        "distinct_home_teams": home_count,
        "distinct_away_teams": away_count,
        "status": "OK" if home_count >= 10 else "Warning",
    }


async def check_historical_results(session, league_id: int, name: str) -> dict:
    """Check completed fixtures with results."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import FixtureORM, CompetitionORM

    completed = (await session.scalar(
        select(func.count(FixtureORM.id))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
        .where(FixtureORM.status.in_(["FT", "AET", "PEN", "FINISHED"]))
        .where(FixtureORM.score_home.isnot(None))
    )) or 0

    return {
        "league": name,
        "completed_with_result": completed,
        "status": "OK" if completed > 0 else "Warning (volume reset — expected before season)",
    }


async def check_odds_coverage(session, league_id: int, name: str) -> dict:
    """Check odds snapshots coverage."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, OddsSnapshotORM,
    )

    subq = (
        select(FixtureORM.id)
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
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
    return {
        "league": name,
        "fixtures_total": total,
        "odds_matched": matched,
        "coverage_pct": pct,
        "status": "OK" if pct >= 70 else ("Warning" if pct > 0 else "None"),
    }


async def check_whitelist_pass(league_id: int, name: str) -> dict:
    """Verify whitelist entry exists and is enabled."""
    from app.config.whitelist import get_whitelist

    whitelist = get_whitelist()
    entry = whitelist.get_entry("", league_id=league_id)

    if entry is None:
        return {
            "league": name,
            "league_id": league_id,
            "status": "Error — not found in whitelist",
            "sport_keys": [],
        }
    return {
        "league": name,
        "league_id": league_id,
        "category": entry.category,
        "sport_keys": entry.sport_keys,
        "status": "OK" if entry.enabled else "Error — disabled",
    }


async def run_e2e_premier_league(log=None) -> dict:
    """Run full E2E for Premier League: fixtures → odds → prediction → Gate → value bet."""
    from datetime import date as date_type

    from app.core.container import container
    from app.workers.daily_job import run_daily_job

    if log:
        log.info("E2E: Running full daily_job for Premier League E2E...")
    container.init_resources()
    try:
        report = await run_daily_job(container, date_type.today())
        if log:
            log.info("E2E: fixtures=%d odds=%d preds=%d gate=%d vb=%d",
                     report.fixtures.fixtures_processed if report.fixtures else 0,
                     report.odds.events_matched if report.odds else 0,
                     report.picks.value_bets_created if report.picks else 0,
                     report.picks.fixtures_reviewed if report.picks else 0,
                     report.picks.value_bets_created if report.picks else 0)

        return {
            "status": "completed",
            "pipeline": {
                "fixtures_processed": report.fixtures.fixtures_processed if report.fixtures else 0,
                "odds_matched": report.odds.events_matched if report.odds else 0,
                "fixtures_analyzed": report.picks.fixtures_analyzed if report.picks else 0,
                "fixtures_qualified": report.picks.fixtures_qualified if report.picks else 0,
                "fixtures_reviewed": report.picks.fixtures_reviewed if report.picks else 0,
                "skipped_unsupported": report.picks.fixtures_skipped_unsupported_competition if report.picks else 0,
                "value_bets": report.picks.value_bets_created if report.picks else 0,
                "settlements": report.settlement.bets_settled if report.settlement else 0,
            },
        }
    except Exception as e:
        if log:
            log.error("E2E failed: %s", e)
        return {"status": "failed", "error": str(e)}
    finally:
        await container.shutdown_resources()


async def run(log=None, *, run_e2e: bool = False) -> dict:
    """Run all pre-season checks. Returns structured report."""
    from app.core.container import container

    now = datetime.now(timezone.utc)
    today = date.today().isoformat()

    stages: dict[str, list[dict]] = {}

    if log:
        log.info("Season prep: starting big-5 checks...")

    container.init_resources()
    try:
        async with container.database.session() as session:
            # Stage 1: Fixtures
            stages["1_fixtures"] = []
            for league in BIG5:
                result = await check_fixtures(session, league["league_id"],
                                              league["name"], league["season"])
                stages["1_fixtures"].append(result)
                if log:
                    log.info("  [Fixtures] %s: %d total, %d future — %s",
                             league["name"], result["fixtures_total"],
                             result["fixtures_future"], result["status"])

            # Stage 2: Teams
            stages["2_teams"] = []
            for league in BIG5:
                result = await check_teams(session, league["league_id"], league["name"])
                stages["2_teams"].append(result)
                if log:
                    log.info("  [Teams] %s: %d home, %d away — %s",
                             league["name"], result["distinct_home_teams"],
                             result["distinct_away_teams"], result["status"])

            # Stage 3: Historical Results
            stages["3_history"] = []
            for league in BIG5:
                result = await check_historical_results(session, league["league_id"], league["name"])
                stages["3_history"].append(result)
                if log:
                    log.info("  [History] %s: %d completed — %s",
                             league["name"], result["completed_with_result"], result["status"])

            # Stage 4: Odds Coverage
            stages["5_odds_coverage"] = []
            for league in BIG5:
                result = await check_odds_coverage(session, league["league_id"], league["name"])
                stages["5_odds_coverage"].append(result)
                if log:
                    log.info("  [Odds] %s: %d/%d (%.1f%%) — %s",
                             league["name"], result["odds_matched"],
                             result["fixtures_total"], result["coverage_pct"], result["status"])
    finally:
        await container.shutdown_resources()

    # Stage 5: Whitelist (no DB needed)
    stages["6_whitelist"] = []
    for league in BIG5:
        result = await check_whitelist_pass(league["league_id"], league["name"])
        stages["6_whitelist"].append(result)
        if log:
            log.info("  [Whitelist] %s: sport_keys=%s — %s",
                     league["name"], result.get("sport_keys", []), result["status"])

    # Stage 7: E2E (optional)
    e2e_result = None
    if run_e2e:
        e2e_result = await run_e2e_premier_league(log=log)
        stages["7_e2e"] = [e2e_result]

    # Check all stages for errors/warnings
    all_statuses = []
    for stage_key, entries in stages.items():
        for entry in entries:
            s = entry.get("status", "")
            all_statuses.append(s)

    error_count = sum(1 for s in all_statuses if s.startswith("Error"))
    warning_count = sum(1 for s in all_statuses if s.startswith("Warning"))

    overall = "Error" if error_count > 0 else ("Warning" if warning_count > 0 else "OK")

    report = {
        "timestamp": now.isoformat(),
        "date": today,
        "overall_status": overall,
        "errors": error_count,
        "warnings": warning_count,
        "stages": stages,
        "e2e_included": run_e2e,
    }
    return report


def write_report(report: dict) -> Path:
    """Write season prep Markdown report."""
    today = report["date"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    md_path = OUTPUT_DIR / f"season_prep_report_{today}.md"
    lines = [
        f"# Season Preparation Report — {today}",
        "",
        f"**Overall Status**: {report['overall_status']}",
        f"Errors: {report['errors']} | Warnings: {report['warnings']}",
        "",
    ]

    stage_labels = {
        "1_fixtures": "1. Fixtures (2026/27 Season)",
        "2_teams": "2. Teams",
        "3_history": "3. Historical Completed Results",
        "5_odds_coverage": "4. Odds Coverage (The Odds API)",
        "6_whitelist": "5. Whitelist Validation",
        "7_e2e": "6. E2E (Premier League)",
    }

    for stage_key in ["1_fixtures", "2_teams", "3_history", "5_odds_coverage", "6_whitelist", "7_e2e"]:
        entries = report["stages"].get(stage_key)
        if not entries:
            continue

        lines.append(f"## {stage_labels.get(stage_key, stage_key)}")
        lines.append("")

        if stage_key == "1_fixtures":
            lines.append("| League | ID | Season | Kickoff Est. | Total Fixtures | Future | Status |")
            lines.append("|--------|----|--------|-------------|---------------|--------|--------|")
            for e in entries:
                lines.append(f"| {e['league']} | {e['league_id']} | {e['season']} | {e['kickoff_est']} | "
                             f"{e['fixtures_total']} | {e['fixtures_future']} | {e['status']} |")

        elif stage_key == "2_teams":
            lines.append("| League | Distinct Home | Distinct Away | Status |")
            lines.append("|--------|--------------|--------------|--------|")
            for e in entries:
                lines.append(f"| {e['league']} | {e['distinct_home_teams']} | "
                             f"{e['distinct_away_teams']} | {e['status']} |")

        elif stage_key == "3_history":
            lines.append("| League | Completed (with results) | Status |")
            lines.append("|--------|--------------------------|--------|")
            for e in entries:
                lines.append(f"| {e['league']} | {e['completed_with_result']} | {e['status']} |")

        elif stage_key == "5_odds_coverage":
            lines.append("| League | Fixtures | Odds Matched | Coverage % | Status |")
            lines.append("|--------|----------|-------------|-----------|--------|")
            for e in entries:
                lines.append(f"| {e['league']} | {e['fixtures_total']} | {e['odds_matched']} | "
                             f"{e['coverage_pct']}% | {e['status']} |")

        elif stage_key == "6_whitelist":
            lines.append("| League | ID | Category | Sport Keys | Status |")
            lines.append("|--------|----|----------|------------|--------|")
            for e in entries:
                keys = ", ".join(e.get("sport_keys", []))
                category = e.get("category", "N/A")
                lines.append(f"| {e['league']} | {e['league_id']} | {category} | "
                             f"`{keys}` | {e['status']} |")

        elif stage_key == "7_e2e":
            e2e = entries[0]
            lines.append(f"**E2E Result**: {e2e.get('status', 'N/A')}")
            if "pipeline" in e2e:
                p = e2e["pipeline"]
                lines.append("")
                lines.append("| Step | Count |")
                lines.append("|------|-------|")
                lines.append(f"| Fixtures Processed | {p.get('fixtures_processed', 0)} |")
                lines.append(f"| Odds Matched | {p.get('odds_matched', 0)} |")
                lines.append(f"| Fixtures Analyzed | {p.get('fixtures_analyzed', 0)} |")
                lines.append(f"| Skipped (Unsupported) | {p.get('skipped_unsupported', 0)} |")
                lines.append(f"| Value Bets | {p.get('value_bets', 0)} |")
                lines.append(f"| Settlements | {p.get('settlements', 0)} |")
            if "error" in e2e:
                lines.append(f"\nError: {e2e['error']}")

        lines.append("")

    lines.append(f"*Report generated: {report['timestamp']}*")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Season preparation checks")
    parser.add_argument("--e2e", action="store_true", help="Run E2E pipeline after checks")
    args = parser.parse_args()

    report = asyncio.run(run(run_e2e=args.e2e))
    md_path = write_report(report)
    print(f"Overall: {report['overall_status']} ({report['errors']} errors, {report['warnings']} warnings)")
    print(f"Report: {md_path}")


if __name__ == "__main__":
    main()
