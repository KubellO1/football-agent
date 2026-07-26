"""
Daily data coverage monitor for football-agent.

Per-league stats: fixtures / odds matched / historical data / predictions /
WATCH / BET / value bets. Flags Warning if odds coverage <70%.

Usage:
    python scripts/coverage_monitor.py
    python -m app.workers.scheduler_runner --command coverage_monitor

Outputs:
    output/daily_coverage_report_YYYY-MM-DD.json
    output/daily_coverage_report_YYYY-MM-DD.md
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"

# Five leagues (league_id -> label) plus aggregate
BIG5_LEAGUES: dict[int, str] = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    61: "Ligue 1",
    78: "Bundesliga",
}


async def fetch_league_stats(session, league_id: int, label: str) -> dict:
    """Fetch coverage stats for a single league from PostgreSQL."""
    from sqlalchemy import func, select, text, case
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, PredictionORM, ValueBetORM, OddsSnapshotORM,
    )

    # Count fixtures for this league (via competition.external_id == league_id)
    subq_fixture = (
        select(FixtureORM.id)
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
        .subquery()
    )

    fixtures_total = (await session.scalar(
        select(func.count()).select_from(subq_fixture)
    )) or 0

    fixtures_with_odds = (await session.scalar(
        select(func.count(func.distinct(OddsSnapshotORM.fixture_id)))
        .where(OddsSnapshotORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    odds_coverage_pct = round(fixtures_with_odds / fixtures_total * 100, 1) if fixtures_total > 0 else 0.0

    # Predictions for this league
    predictions_total = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    # WATCH / BET / value_bets breakdown
    watch_count = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision == "WATCH")
    )) or 0

    no_odds_count = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision == "NO_ODDS")
    )) or 0

    bet_count = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision.in_(["BET", "VALUE_BET"]))
    )) or 0

    value_bets = (await session.scalar(
        select(func.count(ValueBetORM.id))
        .where(ValueBetORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    # Historical data coverage: fixtures with completed status (score recorded)
    fixtures_with_result = (await session.scalar(
        select(func.count(FixtureORM.id))
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
        .where(FixtureORM.status.in_(["FT", "AET", "PEN", "FINISHED"]))
        .where(FixtureORM.score_home.isnot(None))
    )) or 0

    historical_coverage_pct = round(fixtures_with_result / fixtures_total * 100, 1) if fixtures_total > 0 else 0.0

    # Flags
    warnings = []
    if odds_coverage_pct < 70 and fixtures_total > 0:
        warnings.append(f"Odds coverage {odds_coverage_pct}% below 70% threshold")
    if historical_coverage_pct < 20 and fixtures_total > 0:
        warnings.append(f"Historical data coverage {historical_coverage_pct}% below 20%")

    return {
        "league_id": league_id,
        "label": label,
        "fixtures_total": fixtures_total,
        "odds_matched": fixtures_with_odds,
        "odds_coverage_pct": odds_coverage_pct,
        "historical_data_pct": historical_coverage_pct,
        "predictions_total": predictions_total,
        "watch_count": watch_count,
        "no_odds_count": no_odds_count,
        "bet_count": bet_count,
        "value_bets": value_bets,
        "warnings": warnings,
    }


async def run(log=None) -> dict:
    """Run coverage monitor for all big-5 leagues. Returns structured report."""
    from app.core.container import container

    now = datetime.now(timezone.utc)
    today = date.today().isoformat()

    container.init_resources()
    try:
        async with container.database.session() as session:
            # Per-league stats
            league_stats = []
            for lid, label in BIG5_LEAGUES.items():
                try:
                    stats = await fetch_league_stats(session, lid, label)
                    league_stats.append(stats)
                    if log:
                        log.info("  %s (league_id=%d): fixtures=%d odds=%.1f%% preds=%d value_bets=%d",
                                 label, lid, stats["fixtures_total"],
                                 stats["odds_coverage_pct"], stats["predictions_total"],
                                 stats["value_bets"])
                except Exception as e:
                    if log:
                        log.warning("  %s (league_id=%d): FAILED — %s", label, lid, e)
                    league_stats.append({
                        "league_id": lid, "label": label, "error": str(e),
                        "fixtures_total": 0, "odds_matched": 0, "odds_coverage_pct": 0,
                        "historical_data_pct": 0, "predictions_total": 0,
                        "watch_count": 0, "no_odds_count": 0, "bet_count": 0,
                        "value_bets": 0, "warnings": [f"Stats query failed: {e}"],
                    })

            # All-league aggregate
            from sqlalchemy import func, select
            from app.repositories.sqlalchemy.models import PredictionORM, ValueBetORM

            total_predictions = (await session.scalar(
                select(func.count(PredictionORM.id))
            )) or 0
            total_value_bets = (await session.scalar(
                select(func.count(ValueBetORM.id))
            )) or 0

            any_warning = any(s["warnings"] for s in league_stats)
            overall_status = "Warning" if any_warning else "OK"

        report = {
            "timestamp": now.isoformat(),
            "date": today,
            "overall_status": overall_status,
            "summary": {
                "leagues_checked": len(league_stats),
                "leagues_with_warnings": sum(1 for s in league_stats if s["warnings"]),
                "total_predictions": total_predictions,
                "total_value_bets": total_value_bets,
            },
            "leagues": league_stats,
        }
        return report
    finally:
        await container.shutdown_resources()


def write_reports(report: dict) -> tuple[Path, Path]:
    """Write JSON and Markdown reports."""
    today = report["date"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"daily_coverage_report_{today}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    md_path = OUTPUT_DIR / f"daily_coverage_report_{today}.md"
    lines = [
        f"# Daily Coverage Report — {today}",
        "",
        f"**Overall Status**: {report['overall_status']}",
        f"Leagues checked: {report['summary']['leagues_checked']} | "
        f"Warnings: {report['summary']['leagues_with_warnings']}",
        "",
        "## Per-League Stats",
        "",
        "| # | League | Fixtures | Odds-matched | Odds Coverage | Hist. Data | Preds | WATCH | BET | Value Bets |",
        "|---|--------|----------|-------------|---------------|------------|-------|-------|-----|------------|",
    ]

    for i, s in enumerate(report["leagues"], 1):
        flag = " ⚠️" if s.get("warnings") else ""
        lines.append(
            f"| {i} | {s['label']}{flag} | {s['fixtures_total']} | "
            f"{s['odds_matched']} | {s['odds_coverage_pct']}% | "
            f"{s['historical_data_pct']}% | {s['predictions_total']} | "
            f"{s['watch_count']} | {s['bet_count']} | {s['value_bets']} |"
        )

    # Warnings section
    warning_leagues = [s for s in report["leagues"] if s.get("warnings")]
    if warning_leagues:
        lines.append("")
        lines.append("## Warnings")
        lines.append("")
        for s in warning_leagues:
            for w in s["warnings"]:
                lines.append(f"- **{s['label']}**: {w}")

    # 7-day Odds Coverage trend
    lines.extend(_build_7day_trend(today))

    lines.append("")
    lines.append(f"*Report generated: {report['timestamp']}*")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _build_7day_trend(today_str: str) -> list[str]:
    """Build 7-day Odds Coverage trend table from historical coverage reports.

    Reads the last 7 daily_coverage_report_*.json files, extracts per-league
    Odds Coverage %, and renders an aggregate trend table.
    """
    from datetime import date, timedelta

    lines = []
    lines.append("")
    lines.append("## 7-Day Odds Coverage Trend")
    lines.append("")

    today_date = date.fromisoformat(today_str)

    # Collect historical data
    days_data = []
    for offset in range(7):
        d = today_date - timedelta(days=offset)
        d_str = d.isoformat()
        report_path = OUTPUT_DIR / f"daily_coverage_report_{d_str}.json"
        if not report_path.exists():
            continue

        try:
            hist = json.loads(report_path.read_text(encoding="utf-8"))
            leagues_data = hist.get("leagues", [])
            if not leagues_data:
                continue

            row = {"date": d_str}
            total_fixtures = 0
            total_odds = 0
            for l in leagues_data:
                total_fixtures += l.get("fixtures_total", 0)
                total_odds += l.get("odds_matched", 0)
            row["total_fixtures"] = total_fixtures
            row["odds_matched"] = total_odds
            row["coverage_pct"] = round(total_odds / total_fixtures * 100, 1) if total_fixtures > 0 else 0.0
            days_data.append(row)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    if not days_data:
        lines.append("_No historical coverage data available._")
        return lines

    # Sort by date ascending
    days_data.sort(key=lambda x: x["date"])

    lines.append("| Date | Total Fixtures | Odds Matched | Coverage % |")
    lines.append("|------|---------------|-------------|-----------|")
    for row in days_data:
        lines.append(
            f"| {row['date']} | {row['total_fixtures']} | "
            f"{row['odds_matched']} | {row['coverage_pct']}% |"
        )
    return lines


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(run())
    json_path, md_path = write_reports(report)
    print(f"Overall: {report['overall_status']}")
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    for s in report["leagues"]:
        w = f"  ⚠️ {', '.join(s['warnings'])}" if s.get("warnings") else ""
        print(f"  {s['label']}: fixtures={s['fixtures_total']} odds={s['odds_coverage_pct']}% "
              f"preds={s['predictions_total']} value_bets={s['value_bets']}{w}")


if __name__ == "__main__":
    main()
