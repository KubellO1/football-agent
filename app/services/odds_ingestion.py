"""赔率采集（odds ingestion）编排。

从 The Odds API 抓取足球赔率，**保守地**匹配到已入库比赛（见 odds_matching：
唯一命中才写入，未匹配/歧义一律跳过并报告，绝不猜测），把 h2h 盘口映射为
1x2 赔率快照并**幂等**写入 PostgreSQL。

不做任何预测/下注推荐。整个过程在调用方（请求作用域）的同一事务内完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.core.logging import get_logger
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.odds import Odds
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import ProviderFixtureOdds
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
from app.repositories.interfaces.reference import BookmakerRepository, TeamRepository
from app.schemas.odds_sync import OddsSyncReport
from app.services.odds_matching import (
    MatchCandidate,
    MatchOutcome,
    match_event,
    normalize_team_name,
)

logger = get_logger(__name__)

SOURCE_THE_ODDS_API = "the-odds-api"
_H2H_MARKET = "h2h"
_SAMPLE_CAP = 20  # 报告中未匹配/歧义样例的最大条数


def classify_outcome(name: str, home_team: str, away_team: str) -> str | None:
    """把 h2h 赔项名映射为 1x2 的 code（home/away/draw），无法识别返回 None。"""
    normalized = normalize_team_name(name)
    if normalized == normalize_team_name(home_team):
        return "home"
    if normalized == normalize_team_name(away_team):
        return "away"
    if normalized == "draw":
        return "draw"
    return None


@dataclass
class _Counters:
    fetched: int = 0
    matched: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    created: int = 0
    existing: int = 0
    outcomes_skipped: int = 0


class OddsIngestionService:
    """从 OddsProvider 抓取赔率、匹配比赛并幂等写入赔率快照。"""

    def __init__(
        self,
        *,
        odds_provider: OddsProvider,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        bookmakers: BookmakerRepository,
        odds_snapshots: OddsSnapshotRepository,
        sport_keys: list[str],
        regions: list[str],
        tolerance_minutes: int,
        source: str = SOURCE_THE_ODDS_API,
    ) -> None:
        self._odds = odds_provider
        self._fixtures = fixtures
        self._teams = teams
        self._bookmakers = bookmakers
        self._snapshots = odds_snapshots
        self._sport_keys = sport_keys
        self._regions = regions
        self._tolerance = timedelta(minutes=tolerance_minutes)
        self._source = source

    async def sync_odds_today(self, on_date: date) -> OddsSyncReport:
        """抓取并写入 ``on_date`` 当日的足球赔率快照，返回统计。可安全重复运行。"""
        candidates = await self._build_candidates(on_date)
        counters = _Counters()
        unmatched_samples: list[str] = []
        ambiguous_samples: list[str] = []
        bookmaker_cache: dict[str, Bookmaker] = {}

        for sport_key in self._sport_keys:
            events = await self._odds.get_odds(
                sport=sport_key, markets=("h2h",), regions=tuple(self._regions)
            )
            for event in events:
                counters.fetched += 1
                result = match_event(
                    event_home=event.home_team,
                    event_away=event.away_team,
                    commence_time=event.commence_time,
                    candidates=candidates,
                    tolerance=self._tolerance,
                )
                label = (
                    f"{event.home_team} vs {event.away_team} @ {event.commence_time.isoformat()}"
                )
                if result.outcome is MatchOutcome.UNMATCHED:
                    counters.unmatched += 1
                    if len(unmatched_samples) < _SAMPLE_CAP:
                        unmatched_samples.append(label)
                    continue
                if result.outcome is MatchOutcome.AMBIGUOUS:
                    counters.ambiguous += 1
                    if len(ambiguous_samples) < _SAMPLE_CAP:
                        ambiguous_samples.append(f"{label} ({result.candidate_count} candidates)")
                    continue

                counters.matched += 1
                assert result.fixture_id is not None
                await self._ingest_event_odds(event, result.fixture_id, bookmaker_cache, counters)

        logger.info(
            "Odds sync %s: fetched=%d matched=%d unmatched=%d ambiguous=%d "
            "snapshots(created=%d existing=%d) outcomes_skipped=%d",
            on_date.isoformat(),
            counters.fetched,
            counters.matched,
            counters.unmatched,
            counters.ambiguous,
            counters.created,
            counters.existing,
            counters.outcomes_skipped,
        )
        return OddsSyncReport(
            source=self._source,
            date=on_date.isoformat(),
            sport_keys=list(self._sport_keys),
            events_fetched=counters.fetched,
            events_matched=counters.matched,
            events_unmatched=counters.unmatched,
            events_ambiguous=counters.ambiguous,
            snapshots_created=counters.created,
            snapshots_existing=counters.existing,
            outcomes_skipped=counters.outcomes_skipped,
            unmatched_samples=unmatched_samples,
            ambiguous_samples=ambiguous_samples,
        )

    async def _build_candidates(self, on_date: date) -> list[MatchCandidate]:
        start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        fixtures = await self._fixtures.list_by_kickoff_window(start, end)

        team_ids = {f.home_team_id for f in fixtures} | {f.away_team_id for f in fixtures}
        team_names = {t.id: t.name for t in await self._teams.list_by_ids(team_ids)}

        candidates: list[MatchCandidate] = []
        for f in fixtures:
            home = team_names.get(f.home_team_id)
            away = team_names.get(f.away_team_id)
            if home is None or away is None:
                continue  # 参考数据缺失，无法安全匹配 → 忽略该比赛
            candidates.append(
                MatchCandidate(
                    fixture_id=f.id,
                    home_norm=normalize_team_name(home),
                    away_norm=normalize_team_name(away),
                    kickoff=f.kickoff,
                )
            )
        return candidates

    async def _ingest_event_odds(
        self,
        event: ProviderFixtureOdds,
        fixture_id: UUID,
        bookmaker_cache: dict[str, Bookmaker],
        counters: _Counters,
    ) -> None:
        for market in event.bookmakers:
            if market.market != _H2H_MARKET:
                continue
            if market.last_update is None:
                # 无观测时间 → 无法构造幂等键，跳过该盘口的全部赔项。
                counters.outcomes_skipped += len(market.outcomes)
                continue

            bookmaker = await self._get_or_create_bookmaker(
                market.bookmaker_key, market.bookmaker_title, bookmaker_cache
            )
            for outcome in market.outcomes:
                code = classify_outcome(outcome.name, event.home_team, event.away_team)
                if code is None or outcome.price <= 1.0:
                    counters.outcomes_skipped += 1
                    continue
                snapshot = OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker_id=bookmaker.id,
                    selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                    odds=Odds(Decimal(str(outcome.price))),
                    captured_at=market.last_update,
                )
                if await self._snapshots.add_if_absent(snapshot):
                    counters.created += 1
                else:
                    counters.existing += 1

    async def _get_or_create_bookmaker(
        self, key: str, title: str, cache: dict[str, Bookmaker]
    ) -> Bookmaker:
        if key in cache:
            return cache[key]
        existing = await self._bookmakers.get_by_external_id(self._source, key)
        if existing is not None:
            cache[key] = existing
            return existing
        created = await self._bookmakers.add(
            Bookmaker(name=title, external_id=key, external_source=self._source)
        )
        cache[key] = created
        return created
