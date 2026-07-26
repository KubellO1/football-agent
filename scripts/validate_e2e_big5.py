"""Premier League end-to-end validation: run full daily pipeline for a date
where new-season Premier League fixtures exist (2026-08-21 or later).

Usage:
    python scripts/validate_e2e_premier_league.py [--date 2026-08-21]
"""
import asyncio, os, sys
from datetime import date, datetime

sys.path.insert(0, r"C:\Users\ruowa\Projects\football-agent")
os.chdir(r"C:\Users\ruowa\Projects\football-agent")

FIVE_LEAGUES = {39: "PL", 140: "LaLiga", 135: "SerieA", 61: "Ligue1", 78: "Bundesliga"}


async def check_before():
    """Pre-pipeline DB snapshot."""
    import asyncpg
    conn = await asyncpg.connect(
        user="football", password="changeme",
        database="football", host="localhost", port=5432,
    )
    try:
        # Per-league fixture counts for new season
        rows = await conn.fetch("""
            SELECT e.external_id, e.name, COUNT(*) AS fixtures,
                   COUNT(*) FILTER (WHERE f.status = 'scheduled') AS scheduled
            FROM fixtures f
            JOIN competitions e ON e.id = f.competition_id
            WHERE e.external_id = ANY($1)
              AND f.kickoff >= '2026-07-01'
            GROUP BY e.external_id, e.name
            ORDER BY e.external_id
        """, ["39", "140", "135", "61", "78"])
        print("\n--- Fixtures (2026-27 season) ---")
        for r in rows:
            print(f"  {r['name']:20s} (id={r['external_id']})  fixtures={r['fixtures']:4d}  scheduled={r['scheduled']:4d}")

        # Existing predictions / value_bets counts
        preds = await conn.fetchval("SELECT COUNT(*) FROM predictions")
        vbs = await conn.fetchval("SELECT COUNT(*) FROM value_bets")
        odds = await conn.fetchval("SELECT COUNT(*) FROM odds_snapshots")
        print(f"\nBefore: predictions={preds}, value_bets={vbs}, odds_snapshots={odds}")
    finally:
        await conn.close()


async def run_pipeline(on_date: date):
    from app.config.settings import get_settings
    from app.core.container import Container
    from app.workers.daily_job import run_daily_job

    settings = get_settings()
    container = Container(settings)
    container.init_resources()

    try:
        report = await run_daily_job(container, on_date)
        print("\n" + "=" * 60)
        print(f"Daily Job Report: {report.date}")
        print("=" * 60)

        # Step 1: Fixtures
        f = report.fixtures
        print(f"\n[1] Fixtures: processed={f.fixtures_processed} created={f.fixtures_created} "
              f"updated={f.fixtures_updated} skipped={f.fixtures_skipped}")

        # Step 2: Odds
        o = report.odds
        print(f"[2] Odds: fetched={o.events_fetched} matched={o.events_matched} "
              f"snapshots_created={o.snapshots_created}")

        # Step 3: Picks
        p = report.picks
        print(f"[3] Picks: analyzed={p.fixtures_analyzed} qualified={p.fixtures_qualified} "
              f"reviewed={p.fixtures_reviewed} "
              f"skipped_existing={p.fixtures_skipped_existing} "
              f"skipped_unsupported={p.fixtures_skipped_unsupported_competition} "
              f"value_bets={p.value_bets_created} "
              f"predictions_logged={getattr(p, 'predictions_logged', 'N/A')}")

        if report.settlement:
            s = report.settlement
            print(f"[4] Settlement: checked={s.fixtures_checked} settled={s.bets_settled}")

        if report.performance:
            perf = report.performance
            print(f"[5] Performance: total_bets={perf.total_bets} "
                  f"win_rate={perf.win_rate} total_pl={perf.total_pl}")

        return report
    finally:
        await container.shutdown_resources()


async def check_after():
    """Post-pipeline DB snapshot."""
    import asyncpg
    conn = await asyncpg.connect(
        user="football", password="changeme",
        database="football", host="localhost", port=5432,
    )
    try:
        preds = await conn.fetchval("SELECT COUNT(*) FROM predictions")
        vbs = await conn.fetchval("SELECT COUNT(*) FROM value_bets")
        odds = await conn.fetchval("SELECT COUNT(*) FROM odds_snapshots")

        if vb_rows is None:
            vb_rows = []

        # Big 5 predictions count
        big5_preds = await conn.fetch("""
            SELECT e.external_id, e.name, COUNT(*) AS cnt
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN competitions e ON e.id = f.competition_id
            WHERE e.external_id = ANY($1)
              AND f.kickoff >= '2026-07-01'
            GROUP BY e.external_id, e.name
            ORDER BY e.external_id
        """, ["39", "140", "135", "61", "78"])

        print(f"\n--- After Pipeline ---")
        print(f"predictions={preds}, value_bets={vbs}, odds_snapshots={odds}")
        print("\nBig 5 predictions (new season):")
        for r in big5_preds:
            print(f"  {r['name']:20s} (id={r['external_id']})  predictions={r['cnt']}")

        # Gate decisions
        gates = await conn.fetch("""
            SELECT e.external_id, e.name, p.final_decision, COUNT(*) AS cnt
            FROM predictions p
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN competitions e ON e.id = f.competition_id
            WHERE e.external_id = ANY($1)
              AND f.kickoff >= '2026-07-01'
            GROUP BY e.external_id, e.name, p.final_decision
            ORDER BY e.external_id, p.final_decision
        """, ["39", "140", "135", "61", "78"])
        print("\nGate decisions:")
        for r in gates:
            print(f"  {r['name']:20s}  {r['final_decision']:30s}  count={r['cnt']}")

        # Value bets for Big 5
        vb_rows = await conn.fetch("""
            SELECT e.external_id, e.name, COUNT(*) AS cnt
            FROM value_bets vb
            JOIN predictions p ON p.id = vb.prediction_id
            JOIN fixtures f ON f.id = p.fixture_id
            JOIN competitions e ON e.id = f.competition_id
            WHERE e.external_id = ANY($1)
              AND f.kickoff >= '2026-07-01'
            GROUP BY e.external_id, e.name
            ORDER BY e.external_id
        """, ["39", "140", "135", "61", "78"])
        print("\nValue bets (Big 5 new season):")
        if vb_rows:
            for r in vb_rows:
                print(f"  {r['name']:20s}  value_bets={r['cnt']}")
        else:
            print("  None")
    finally:
        await conn.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-08-21")
    args = parser.parse_args()

    on_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    print(f"Validating end-to-end pipeline for {on_date}")

    await check_before()
    await run_pipeline(on_date)
    await check_after()
    print("\n=== End-to-end validation complete ===")


if __name__ == "__main__":
    asyncio.run(main())
