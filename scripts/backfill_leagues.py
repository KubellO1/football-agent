"""定向回填：只回填指定联赛×赛季的 API-Football 赛程（幂等、可续跑、限速）。

只复用既有 provider + IngestionService.sync_league_season；导入赛事/球队/比赛/
比分/状态，不导入赔率、不预测、不调用 LLM。默认联赛与赛季见下。

用法：
    python scripts/backfill_leagues.py                       # 默认 10 联赛 × 5 赛季
    python scripts/backfill_leagues.py --leagues 39,140 --seasons 2024,2025
    python scripts/backfill_leagues.py --restart
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging
from app.services.backfill import (
    DEFAULT_LEAGUES,
    DEFAULT_SEASONS,
    LeagueSeasonBackfill,
)

DEFAULT_CHECKPOINT = Path("data/.league_backfill_checkpoint.json")


def _int_list(value: str) -> list[int]:
    return [int(x) for x in value.split(",") if x.strip()]


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    container = Container(get_settings())
    container.init_resources()
    min_interval = 60.0 / args.rpm if args.rpm > 0 else 0.0
    backfill = LeagueSeasonBackfill(
        container,
        checkpoint_path=args.checkpoint,
        min_interval_seconds=min_interval,
    )
    try:
        report = await backfill.run(args.leagues, args.seasons, restart=args.restart)
    finally:
        await container.shutdown_resources()
    print(
        f"done: pairs={report.pairs_processed} skipped={report.pairs_skipped} "
        f"created={report.fixtures_created} updated={report.fixtures_updated} "
        f"comps={report.competitions_created} teams={report.teams_created}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill API-Football fixtures by league+season.")
    parser.add_argument(
        "--leagues", type=_int_list, default=DEFAULT_LEAGUES, help="league ids, comma-separated"
    )
    parser.add_argument(
        "--seasons", type=_int_list, default=DEFAULT_SEASONS, help="seasons, comma-separated"
    )
    parser.add_argument("--rpm", type=int, default=300, help="max requests per minute")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--restart", action="store_true", help="ignore existing checkpoint")
    args = parser.parse_args()
    if not args.leagues or not args.seasons:
        raise SystemExit("--leagues and --seasons must be non-empty")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
