"""回填历史赔率：从 The Odds API 的 /v4/historical 端点抓取 1X2 赔率，保守匹配到
已入库比赛并幂等写入 odds_snapshots（复用既有 provider/服务/匹配逻辑）。

不预测、不评审、不落 ValueBet/DecisionLog——只写赔率快照。每天取一份快照（默认
UTC 12:00），只与当天开赛的比赛匹配；重复运行不会产生重复快照。

用法：
    python scripts/backfill_historical_odds.py --sport soccer_epl --from 2024-08-01 --to 2025-05-31
    python scripts/backfill_historical_odds.py --sport soccer_epl --from 2024-08-16 --to 2024-08-18 \
        --competition-name "Premier League"
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging, get_logger
from app.core.service_factory import build_odds_ingestion_service
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyCompetitionRepository

logger = get_logger(__name__)

_API_FOOTBALL = "api-football"
# sport key → API-Football 赛事 external_id（用于把候选限定到该赛事）。用 external_id 而
# 非赛事名解析，因为库中同名赛事很多（多个 "Premier League"），按名解析可能命中错的。
# 命中不了就退回「名+时间」匹配；可用 --league / --competition-name 覆盖。
SPORT_LEAGUE_IDS = {
    "soccer_epl": 39,
    "soccer_spain_la_liga": 140,
    "soccer_germany_bundesliga": 78,
    "soccer_italy_serie_a": 135,
    "soccer_france_ligue_one": 61,
}


async def _resolve_competition(session, args):  # type: ignore[no-untyped-def]
    """返回 (competition_id, competition_name)；无法解析则 (None, None) 并告警。

    解析优先级：--competition-name（按名，用户显式） > --league（api-football external_id）
    > 内置 sport→league_id 映射（external_id，无歧义）。
    """
    comps = SqlAlchemyCompetitionRepository(session)
    if args.competition_name:
        comp = await comps.get_by_name(args.competition_name)
        if comp is None:
            logger.warning(
                "competition %r not found; competition filter OFF (names + time only)",
                args.competition_name,
            )
            return None, None
        return comp.id, comp.name

    league_id = args.league or SPORT_LEAGUE_IDS.get(args.sport)
    if league_id is None:
        logger.warning(
            "no league mapping for sport %r and no --league/--competition-name given; "
            "matching by team names + kickoff time only (competition filter OFF)",
            args.sport,
        )
        return None, None
    comp = await comps.get_by_external_id(_API_FOOTBALL, str(league_id))
    if comp is None:
        logger.warning(
            "no competition with external_id=%s (%s); competition filter OFF (names + time only)",
            league_id,
            _API_FOOTBALL,
        )
        return None, None
    return comp.id, comp.name


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    container = Container(get_settings())
    container.init_resources()
    try:
        async with container.database.session() as session:
            competition_id, competition_scope = await _resolve_competition(session, args)
            service = build_odds_ingestion_service(container, session)
            report = await service.backfill_historical(
                sport=args.sport,
                start=args.date_from,
                end=args.date_to,
                competition_id=competition_id,
                competition_scope=competition_scope,
                snapshot_hour=args.snapshot_hour,
                regions=args.regions,
            )
    finally:
        await container.shutdown_resources()

    print(
        f"done: sport={report.sport} {report.date_from}..{report.date_to} "
        f"days={report.days_processed} scope={report.competition_scope!r}\n"
        f"  events: fetched={report.events_fetched} matched={report.events_matched} "
        f"unmatched={report.events_unmatched} ambiguous={report.events_ambiguous}\n"
        f"  snapshots: created={report.snapshots_created} existing={report.snapshots_existing} "
        f"outcomes_skipped={report.outcomes_skipped}"
    )
    if report.unmatched_samples:
        print("  unmatched samples:")
        for s in report.unmatched_samples:
            print(f"    - {s}")
    if report.ambiguous_samples:
        print("  ambiguous samples:")
        for s in report.ambiguous_samples:
            print(f"    - {s}")


def _region_list(value: str) -> list[str]:
    return [r.strip() for r in value.split(",") if r.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical 1X2 odds from The Odds API.")
    parser.add_argument("--sport", required=True, help="The Odds API sport key, e.g. soccer_epl")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--competition-name",
        type=str,
        default=None,
        help="限定候选比赛的赛事名（按名解析，可能歧义；优先于 --league）",
    )
    parser.add_argument(
        "--league",
        type=int,
        default=None,
        help="限定候选比赛的赛事 API-Football external_id（如 39=EPL，无歧义）",
    )
    parser.add_argument(
        "--regions", type=_region_list, default=None, help="逗号分隔的地区，如 eu,uk（默认取配置）"
    )
    parser.add_argument(
        "--snapshot-hour", type=int, default=12, help="每天取快照的 UTC 小时（默认 12）"
    )
    args = parser.parse_args()
    if args.date_to < args.date_from:
        raise SystemExit("--to must not be before --from")
    if not 0 <= args.snapshot_hour <= 23:
        raise SystemExit("--snapshot-hour must be 0..23")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
