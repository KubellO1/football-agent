"""Pull 2026-27 season fixtures for the Big 5 European leagues.

Uses the existing IngestionService + ApiFootballProvider to fetch and
persist fixtures, teams, and competitions for:

    League              league_id  season
    ----------------------------------------
    Premier League      39         2026
    La Liga             140        2026
    Serie A             135        2026
    Ligue 1             61         2026
    Bundesliga          78         2026

Usage:
    python scripts/ingest_big5_2027.py [--league-ids 39,140] [--season 2026]

The script is idempotent — re-running it only refreshes mutable fields
(kickoff time, status, score) without creating duplicates.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path
# ---------------------------------------------------------------------------
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
BIG5_LEAGUES: dict[int, str] = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    61: "Ligue 1",
    78: "Bundesliga",
}


async def ingest_one(container, league_id: int, league_name: str, season: int):
    """Fetch + persist all fixtures for one league season."""
    from app.repositories.sqlalchemy.reference_repositories import (
        SqlAlchemyCompetitionRepository,
        SqlAlchemyTeamRepository,
    )
    from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
    from app.services.ingestion import IngestionService

    db = container.database
    fixtures_provider = container.resolve(
        __import__("app.providers", fromlist=["FixturesProvider"]).FixturesProvider,
    )

    async with db.session() as session:
        comp_repo = SqlAlchemyCompetitionRepository(session)
        team_repo = SqlAlchemyTeamRepository(session)
        fix_repo = SqlAlchemyFixtureRepository(session)

        svc = IngestionService(
            fixtures_provider=fixtures_provider,
            competitions=comp_repo,
            teams=team_repo,
            fixtures=fix_repo,
        )

        print(f"  [{league_name} (id={league_id})] Fetching season {season}...")
        start = datetime.now()
        report = await svc.sync_league_season(league_id, season)
        elapsed = (datetime.now() - start).total_seconds()

        print(
            f"  [{league_name}] done in {elapsed:.1f}s: "
            f"processed={report.fixtures_processed} "
            f"created={report.fixtures_created} "
            f"updated={report.fixtures_updated} "
            f"skipped={report.fixtures_skipped} "
            f"comps={report.competitions_created} "
            f"teams={report.teams_created}"
        )
        return report


async def main():
    parser = argparse.ArgumentParser(description="Ingest Big 5 2026-27 fixtures")
    parser.add_argument(
        "--league-ids",
        type=str,
        default="39,140,135,61,78",
        help="Comma-separated league IDs (default: all Big 5)",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=2026,
        help="API-Football season year (default: 2026)",
    )
    args = parser.parse_args()

    league_ids = [int(x.strip()) for x in args.league_ids.split(",")]

    # Initialise container
    from app.core.container import container

    print("Initialising container...")
    container.init_resources()

    try:
        total = len(league_ids)
        for i, lid in enumerate(league_ids, 1):
            name = BIG5_LEAGUES.get(lid, f"League {lid}")
            print(f"\n[{i}/{total}] {name}")
            try:
                await ingest_one(container, lid, name, args.season)
            except Exception as exc:
                print(f"  !! FAILED: {exc}", file=sys.stderr)
    finally:
        print("\nShutting down...")
        await container.shutdown_resources()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
