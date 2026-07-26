"""
Daily Gate Funnel report for football-agent.

Outputs the complete prediction pipeline funnel per league and aggregate.

Usage:
    python scripts/gate_funnel.py
    python -m app.workers.scheduler_runner --command gate_funnel

Outputs:
    output/daily_funnel_YYYY-MM-DD.md
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


def _build_production_league_map() -> dict[int, str]:
    """Build league_id → label mapping from production whitelist.

    Only entries with ``enabled=True`` and a valid ``api_football_league_id``
    are included. This replaces the old hardcoded BIG5_LEAGUES dict.
    """
    from app.config.whitelist import get_whitelist

    whitelist = get_whitelist()
    league_map: dict[int, str] = {}
    for entry in whitelist.entries:
        if entry.enabled and entry.api_football_league_id is not None:
            league_map[entry.api_football_league_id] = entry.name
    return league_map


async def build_league_funnel(session, league_id: int, label: str) -> dict:
    """Build funnel stats for one league."""
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, CompetitionORM, PredictionORM, ValueBetORM, OddsSnapshotORM,
    )

    subq_fixture = (
        select(FixtureORM.id)
        .join(CompetitionORM, FixtureORM.competition_id == CompetitionORM.id)
        .where(CompetitionORM.external_id == str(league_id))
        .subquery()
    )

    fixtures_total = (await session.scalar(
        select(func.count()).select_from(subq_fixture)
    )) or 0

    odds_matched = (await session.scalar(
        select(func.count(func.distinct(OddsSnapshotORM.fixture_id)))
        .where(OddsSnapshotORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    no_odds = fixtures_total - odds_matched
    odds_pct = round(odds_matched / fixtures_total * 100, 1) if fixtures_total > 0 else 0

    predictions = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    watch = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision == "WATCH")
    )) or 0

    no_bet = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision == "NO_BET")
    )) or 0

    no_odds_pred = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision == "NO_ODDS")
    )) or 0

    gate_approved = (await session.scalar(
        select(func.count(PredictionORM.id))
        .where(PredictionORM.fixture_id.in_(select(subq_fixture.c.id)))
        .where(PredictionORM.final_decision.in_(["BET", "VALUE_BET"]))
    )) or 0

    value_bets = (await session.scalar(
        select(func.count(ValueBetORM.id))
        .where(ValueBetORM.fixture_id.in_(select(subq_fixture.c.id)))
    )) or 0

    return {
        "league_id": league_id,
        "label": label,
        "fixtures": fixtures_total,
        "odds_matched": odds_matched,
        "odds_matched_pct": odds_pct,
        "no_odds_from_provider": no_odds,
        "predictions": predictions,
        "watch": watch,
        "no_bet": no_bet,
        "no_odds_pred": no_odds_pred,
        "gate_approved": gate_approved,
        "value_bets": value_bets,
    }


async def run(log=None) -> dict:
    """Build funnel for all production-whitelist leagues + aggregate. Returns structured report."""
    from app.core.container import container
    from sqlalchemy import func, select
    from app.repositories.sqlalchemy.models import (
        FixtureORM, PredictionORM, ValueBetORM, OddsSnapshotORM,
    )

    now = datetime.now(timezone.utc)
    today = date.today().isoformat()

    # Build league map from production whitelist (not hardcoded Big-5)
    try:
        PRODUCTION_LEAGUES = _build_production_league_map()
    except Exception as e:
        if log:
            log.warning("Failed to load production whitelist, falling back to empty: %s", e)
        PRODUCTION_LEAGUES = {}

    container.init_resources()
    try:
        async with container.database.session() as session:
            leagues = []
            for lid, label in PRODUCTION_LEAGUES.items():
                try:
                    funnel = await build_league_funnel(session, lid, label)
                    leagues.append(funnel)
                    if log:
                        log.info("  %s: fixtures=%d odds=%d preds=%d gate=%d vb=%d",
                                 label, funnel["fixtures"], funnel["odds_matched"],
                                 funnel["predictions"], funnel["gate_approved"],
                                 funnel["value_bets"])
                except Exception as e:
                    if log:
                        log.warning("  %s: FAILED — %s", label, e)
                    leagues.append({
                        "league_id": lid, "label": label, "error": str(e),
                        "fixtures": 0, "odds_matched": 0, "odds_matched_pct": 0,
                        "no_odds_from_provider": 0, "predictions": 0,
                        "watch": 0, "no_bet": 0, "no_odds_pred": 0,
                        "gate_approved": 0, "value_bets": 0,
                    })

            # Aggregate
            agg_fixtures = sum(l["fixtures"] for l in leagues)
            agg_odds = sum(l["odds_matched"] for l in leagues)
            agg_preds = sum(l["predictions"] for l in leagues)
            agg_watch = sum(l["watch"] for l in leagues)
            agg_no_bet = sum(l["no_bet"] for l in leagues)
            agg_no_odds = sum(l["no_odds_pred"] for l in leagues)
            agg_gate = sum(l["gate_approved"] for l in leagues)
            agg_vb = sum(l["value_bets"] for l in leagues)

            # Global all-league stats
            total_fixtures = (await session.scalar(
                select(func.count(FixtureORM.id))
            )) or 0
            total_predictions = (await session.scalar(
                select(func.count(PredictionORM.id))
            )) or 0
            total_vb = (await session.scalar(
                select(func.count(ValueBetORM.id))
            )) or 0

            report = {
                "timestamp": now.isoformat(),
                "date": today,
                "leagues": leagues,
                "aggregate": {
                    "fixtures": agg_fixtures,
                    "odds_matched": agg_odds,
                    "odds_matched_pct": round(agg_odds / agg_fixtures * 100, 1) if agg_fixtures else 0,
                    "no_odds_from_provider": agg_fixtures - agg_odds,
                    "predictions": agg_preds,
                    "watch": agg_watch,
                    "no_bet": agg_no_bet,
                    "no_odds_pred": agg_no_odds,
                    "gate_approved": agg_gate,
                    "value_bets": agg_vb,
                },
                "global": {
                    "total_fixtures": total_fixtures,
                    "total_predictions": total_predictions,
                    "total_value_bets": total_vb,
                },
            }
            return report
    finally:
        await container.shutdown_resources()


def write_funnel_md(report: dict) -> Path:
    """Write funnel Markdown report."""
    today = report["date"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    md_path = OUTPUT_DIR / f"daily_funnel_{today}.md"
    agg = report["aggregate"]
    gb = report["global"]

    lines = [
        f"# Gate Funnel Report — {today}",
        "",
        "## Aggregate Funnel (All Production Whitelist Leagues)",
        "",
        "```",
        f"Fixtures ({agg['fixtures']})",
        f"  ├─ Odds Matched ({agg['odds_matched']}, {agg['odds_matched_pct']}%)",
        f"  │   └─ Predictions ({agg['predictions']})",
        f"  │       ├─ WATCH ({agg['watch']}) — 历史数据不足",
        f"  │       ├─ NO_BET ({agg['no_bet']}) — EV≤0 或其他原因",
        f"  │       ├─ NO_ODDS ({agg['no_odds_pred']}) — Provider 无赔率",
        f"  │       └─ Gate Approved ({agg['gate_approved']})",
        f"  │           └─ Value Bets ({agg['value_bets']})",
        f"  └─ No Odds ({agg['no_odds_from_provider']}) — Provider 无数据",
        "```",
        "",
        "## Per-League Breakdown",
        "",
        "| League | Fixtures | Odds-matched | Preds | WATCH | NO_BET | Gate Approved | Value Bets |",
        "|--------|----------|-------------|-------|-------|--------|---------------|------------|",
    ]

    for l in report["leagues"]:
        lines.append(
            f"| {l['label']} | {l['fixtures']} | "
            f"{l['odds_matched']} ({l['odds_matched_pct']}%) | "
            f"{l['predictions']} | {l['watch']} | {l['no_bet']} | "
            f"{l['gate_approved']} | {l['value_bets']} |"
        )

    lines += [
        "",
        "## Global (All Leagues)",
        f"- Total Fixtures: {gb['total_fixtures']}",
        f"- Total Predictions: {gb['total_predictions']}",
        f"- Total Value Bets: {gb['total_value_bets']}",
        "",
        "## Reasons Legend",
        "- **WATCH**: Historical data insufficient for confident prediction",
        "- **NO_BET**: EV ≤ 0 or below confidence/Kelly thresholds",
        "- **NO_ODDS**: Odds snapshots missing for this prediction",
        "- **Gate Approved**: Met all thresholds, qualified for value bet recommendation",
        "",
        "## Pipeline Steps (per fixture)",
        "1. Fixture ingestion → whitelist check (SKIPPED_UNSUPPORTED_COMPETITION if not matched)",
        "2. Odds snapshot fetch → match to fixtures",
        "3. Mathematical analysis (EV, Kelly, confidence)",
        "4. Gate evaluation (threshold checks)",
        "5. Value bet generation (if Gate Approved)",
        "",
    ]

    # 30-day funnel trend
    lines.extend(_build_30day_trend(today))

    lines.append(f"*Report generated: {report['timestamp']}*")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _build_30day_trend(today_str: str) -> list[str]:
    """Build 30-day Funnel trend table from historical funnel reports.

    Parses past daily_funnel_*.md files, extracts the Aggregate Funnel
    numbers from the embedded ASCII block, and renders a trend table.
    If fewer than 30 days of history exist, shows all available days.
    """
    from datetime import date, timedelta
    import re

    lines = []
    lines.append("## 30-Day Funnel Trend")
    lines.append("")

    today_date = date.fromisoformat(today_str)

    days_data = []
    for offset in range(30):
        d = today_date - timedelta(days=offset)
        d_str = d.isoformat()
        report_path = OUTPUT_DIR / f"daily_funnel_{d_str}.md"
        if not report_path.exists():
            continue

        try:
            text = report_path.read_text(encoding="utf-8")
            # Extract numbers from the ASCII funnel block
            # Pattern: "Fixtures (N)" and similar lines
            fixtures_m = re.search(r"Fixtures\s*\((\d+)\)", text)
            odds_m = re.search(r"Odds Matched\s*\((\d+)", text)
            preds_m = re.search(r"Predictions\s*\((\d+)\)", text)
            watch_m = re.search(r"WATCH\s*\((\d+)\)", text)
            no_bet_m = re.search(r"NO_BET\s*\((\d+)\)", text)
            gate_m = re.search(r"Gate Approved\s*\((\d+)\)", text)
            vb_m = re.search(r"Value Bets\s*\((\d+)\)", text)

            if fixtures_m:
                row = {
                    "date": d_str,
                    "fixtures": int(fixtures_m.group(1)),
                    "predictions": int(preds_m.group(1)) if preds_m else 0,
                    "watch": int(watch_m.group(1)) if watch_m else 0,
                    "no_bet": int(no_bet_m.group(1)) if no_bet_m else 0,
                    "gate_approved": int(gate_m.group(1)) if gate_m else 0,
                    "value_bets": int(vb_m.group(1)) if vb_m else 0,
                }
                days_data.append(row)
        except Exception:
            continue

    if not days_data:
        lines.append(f"_No historical funnel data available (searched {date.today().isoformat()} back 30 days)._")
        return lines

    # Sort by date ascending
    days_data.sort(key=lambda x: x["date"])
    available_days = len(days_data)

    if available_days < 30:
        lines.append(f"_Showing {available_days} of 30 days (historical data limited)._")
        lines.append("")

    lines.append("| Date | Fixtures | Predictions | WATCH | NO_BET | Gate Approved | Value Bets |")
    lines.append("|------|----------|-------------|-------|--------|---------------|------------|")
    for row in days_data:
        lines.append(
            f"| {row['date']} | {row['fixtures']} | {row['predictions']} | "
            f"{row['watch']} | {row['no_bet']} | {row['gate_approved']} | {row['value_bets']} |"
        )
    return lines


def main():
    report = asyncio.run(run())
    md_path = write_funnel_md(report)
    agg = report["aggregate"]
    print(f"Funnel report written: {md_path}")
    print(f"Aggregate: fixtures={agg['fixtures']} odds={agg['odds_matched']} "
          f"preds={agg['predictions']} gate={agg['gate_approved']} vb={agg['value_bets']}")
    for l in report["leagues"]:
        print(f"  {l['label']}: {l['fixtures']} → {l['odds_matched']} → "
              f"{l['predictions']} → {l['gate_approved']} → {l['value_bets']}")


if __name__ == "__main__":
    main()
