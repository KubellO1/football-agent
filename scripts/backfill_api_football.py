"""按日期区间从 API-Football 回填历史赛程（幂等、可续跑、限速）。

只复用既有 provider + IngestionService；不做预测/下注，不调用 LLM。

用法：
    python scripts/backfill_api_football.py --from 2022-01-01 --to 2026-07-04
    python scripts/backfill_api_football.py --from 2022-01-01 --to 2026-07-04 --rpm 300
    python scripts/backfill_api_football.py --from 2022-01-01 --to 2026-07-04 --restart
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging
from app.services.backfill import ApiFootballBackfill

DEFAULT_CHECKPOINT = Path("data/.backfill_checkpoint.json")


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    container = Container(get_settings())
    container.init_resources()
    min_interval = 60.0 / args.rpm if args.rpm > 0 else 0.0
    backfill = ApiFootballBackfill(
        container,
        checkpoint_path=args.checkpoint,
        min_interval_seconds=min_interval,
    )
    try:
        report = await backfill.run(args.date_from, args.date_to, restart=args.restart)
    finally:
        await container.shutdown_resources()
    print(
        f"done: dates={report.dates_processed} created={report.fixtures_created} "
        f"updated={report.fixtures_updated} skipped={report.fixtures_skipped} "
        f"range={report.first_date}..{report.last_date}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill API-Football fixtures by date range.")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    parser.add_argument("--rpm", type=int, default=300, help="max requests per minute")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--restart", action="store_true", help="ignore existing checkpoint")
    args = parser.parse_args()
    if args.date_from > args.date_to:
        raise SystemExit("--from must be <= --to")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
