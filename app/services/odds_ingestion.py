"""赔率采集（odds ingestion）编排。

从 The Odds API 抓取足球赔率，**保守地**匹配到已入库比赛（见 odds_matching：
唯一命中才写入，未匹配/歧义一律跳过并报告，绝不猜测），把 h2h 盘口映射为
1x2 赔率快照并**幂等**写入 PostgreSQL。

不做任何预测/下注推荐。整个过程在调用方（请求作用域）的同一事务内完成。
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.odds import Odds
from app.providers.schemas.odds import ProviderFixtureOdds, ProviderOddsTarget
from app.schemas.odds_sync import HistoricalOddsBackfillReport, OddsSyncReport
from app.services.odds_matching import (
    MatchCandidate,
    MatchOutcome,
    match_event,
    normalize_team_name,
)
from app.services.team_aliases import accepted_names

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from app.providers.interfaces.odds_provider import OddsProvider
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
    from app.repositories.interfaces.reference import (
        BookmakerRepository,
        CompetitionRepository,
        TeamRepository,
    )

logger = get_logger(__name__)

_H2H_MARKET = "h2h"
_SAMPLE_CAP = 20  # 报告中未匹配/歧义样例的最大条数
_BOOKMAKER_SOURCE = "the-odds-api"


def canonical_bookmaker_identity(key: str, title: str) -> tuple[str, str]:
    """Return a provider-neutral bookmaker identity without merging brands."""
    raw = key or title
    normalized = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    if normalized in {"bet365", "bet365nolatency"}:
        return "bet365", "Bet365"
    return normalized, title or key


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


@dataclass(frozen=True)
class _CandidateBatch:
    candidates: list[MatchCandidate]
    sport_keys_by_fixture: dict[UUID, str]
    requested_fixtures: int
    reason_counts: dict[str, int]


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
        competitions: CompetitionRepository | None = None,
        sport_keys: list[str],
        regions: list[str],
        tolerance_minutes: int,
        alias_names: Callable[[str], frozenset[str]] = accepted_names,
    ) -> None:
        self._odds = odds_provider
        self._fixtures = fixtures
        self._teams = teams
        self._bookmakers = bookmakers
        self._snapshots = odds_snapshots
        self._competitions = competitions
        self._sport_keys = sport_keys
        self._regions = regions
        self._tolerance = timedelta(minutes=tolerance_minutes)
        self._alias_names = alias_names

    async def sync_odds_today(
        self,
        on_date: date,
        *,
        fixture_ids: set[UUID] | None = None,
    ) -> OddsSyncReport:
        """抓取并写入 ``on_date`` 当日的足球赔率快照，返回统计。可安全重复运行。"""
        candidate_batch = await self._build_candidates(on_date, fixture_ids=fixture_ids)
        candidates = candidate_batch.candidates
        counters = _Counters()
        unmatched_samples: list[str] = []
        ambiguous_samples: list[str] = []
        bookmaker_cache: dict[str, Bookmaker] = {}
        all_events: list[ProviderFixtureOdds] = []

        targets = [
            ProviderOddsTarget(
                fixture_id=candidate.fixture_id,
                home_team=candidate.home_norm,
                away_team=candidate.away_norm,
                kickoff=candidate.kickoff,
                sport_key=candidate_batch.sport_keys_by_fixture.get(candidate.fixture_id),
            )
            for candidate in candidates
        ]
        events = await self._odds.get_odds_for_fixtures(
            sport="football",
            fixtures=targets,
            markets=("h2h",),
            regions=tuple(self._regions),
        )
        logger.info(
            "Targeted odds multi returned %d events (approved_candidates=%d)",
            len(events),
            len(candidates),
        )
        all_events.extend(events)
        await self._process_events(
            events,
            candidates,
            counters=counters,
            unmatched_samples=unmatched_samples,
            ambiguous_samples=ambiguous_samples,
            bookmaker_cache=bookmaker_cache,
        )

        primary_provider_hits = sum(1 for e in all_events if e.source == "odds-api.io")
        fallback_provider_hits = sum(1 for e in all_events if e.source == "the-odds-api")

        if primary_provider_hits > 0 and fallback_provider_hits == 0:
            source_label = "odds-api.io"
        elif primary_provider_hits == 0 and fallback_provider_hits > 0:
            source_label = "the-odds-api"
        elif primary_provider_hits > 0 and fallback_provider_hits > 0:
            source_label = "odds-api.io-primary_with_the-odds-api-fallback"
        else:
            source_label = "unknown"

        coverage_stats: dict[str, Any] = getattr(self._odds, "pop_coverage_stats", lambda: {})()
        provider_reasons = dict(coverage_stats.get("unmatched_reason_counts", {}))
        for reason, count in candidate_batch.reason_counts.items():
            provider_reasons[reason] = provider_reasons.get(reason, 0) + count
        coverage_stats["unmatched_reason_counts"] = provider_reasons
        provider_errors: dict[str, int] = getattr(self._odds, "pop_errors", lambda: {})()
        logger.info(
            "Odds sync %s: fetched=%d matched=%d unmatched=%d ambiguous=%d "
            "snapshots(created=%d existing=%d) outcomes_skipped=%d "
            "source=%s primary_hits=%d fallback_hits=%d",
            on_date.isoformat(),
            counters.fetched,
            counters.matched,
            counters.unmatched,
            counters.ambiguous,
            counters.created,
            counters.existing,
            counters.outcomes_skipped,
            source_label,
            primary_provider_hits,
            fallback_provider_hits,
        )
        return OddsSyncReport(
            source=source_label,
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
            primary_provider_hits=primary_provider_hits,
            fallback_provider_hits=fallback_provider_hits,
            provider_errors_by_source=provider_errors,
            requested_fixtures=candidate_batch.requested_fixtures,
            **coverage_stats,
        )

    async def backfill_historical(
        self,
        *,
        sport: str,
        start: date,
        end: date,
        competition_id: UUID | None = None,
        competition_scope: str | None = None,
        snapshot_hour: int = 12,
        regions: Sequence[str] | None = None,
    ) -> HistoricalOddsBackfillReport:
        """回填 ``[start, end]``（含）内每天一份历史赔率快照并幂等写入。

        每天在 ``snapshot_hour``(UTC) 取一份快照，只与**当天开赛**的比赛匹配
        （复用 sync 相同的保守匹配 + 幂等写入）。``competition_id`` 用于把候选限定
        到某赛事（宪法：绝不猜测）。可安全重复运行：同一快照的 last_update 稳定，
        命中幂等键即静默跳过。
        """
        regions_t = tuple(regions) if regions is not None else tuple(self._regions)
        counters = _Counters()
        unmatched_samples: list[str] = []
        ambiguous_samples: list[str] = []
        bookmaker_cache: dict[str, Bookmaker] = {}
        days_processed = 0
        all_events: list[ProviderFixtureOdds] = []

        day = start
        days_skipped_empty = 0
        while day <= end:
            candidate_batch = await self._build_candidates(day, competition_id=competition_id)
            candidates = candidate_batch.candidates
            if not candidates:
                # 当天该赛事无比赛（如国际比赛周/休赛日）→ 不必消耗一次历史赔率请求
                days_skipped_empty += 1
                day += timedelta(days=1)
                continue
            snapshot_at = datetime(day.year, day.month, day.day, snapshot_hour, tzinfo=UTC)
            events = await self._odds.get_historical_odds(
                sport=sport, at=snapshot_at, markets=("h2h",), regions=regions_t
            )
            days_processed += 1
            # 只处理「当天开赛」的事件；快照里其它日期的赛事留给对应那天处理，
            # 避免把未来场次误计为未匹配。
            day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            same_day = [
                e
                for e in events
                if day_start - self._tolerance <= e.commence_time < day_end + self._tolerance
            ]
            all_events.extend(same_day)
            await self._process_events(
                same_day,
                candidates,
                counters=counters,
                unmatched_samples=unmatched_samples,
                ambiguous_samples=ambiguous_samples,
                bookmaker_cache=bookmaker_cache,
            )
            day += timedelta(days=1)

        logger.info(
            "Historical odds backfill %s %s..%s: days=%d (skipped_empty=%d) fetched=%d matched=%d "
            "unmatched=%d ambiguous=%d snapshots(created=%d existing=%d) outcomes_skipped=%d",
            sport,
            start.isoformat(),
            end.isoformat(),
            days_processed,
            days_skipped_empty,
            counters.fetched,
            counters.matched,
            counters.unmatched,
            counters.ambiguous,
            counters.created,
            counters.existing,
            counters.outcomes_skipped,
        )
        primary_provider_hits = sum(1 for e in all_events if e.source == "odds-api.io")
        fallback_provider_hits = sum(1 for e in all_events if e.source == "the-odds-api")
        if primary_provider_hits > 0 and fallback_provider_hits == 0:
            source_label = "odds-api.io"
        elif primary_provider_hits == 0 and fallback_provider_hits > 0:
            source_label = "the-odds-api"
        elif primary_provider_hits > 0 and fallback_provider_hits > 0:
            source_label = "odds-api.io-primary_with_the-odds-api-fallback"
        else:
            source_label = "unknown"
        return HistoricalOddsBackfillReport(
            source=source_label,
            sport=sport,
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            days_processed=days_processed,
            competition_scope=competition_scope,
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

    async def _process_events(
        self,
        events: list[ProviderFixtureOdds],
        candidates: list[MatchCandidate],
        *,
        counters: _Counters,
        unmatched_samples: list[str],
        ambiguous_samples: list[str],
        bookmaker_cache: dict[str, Bookmaker],
    ) -> None:
        """把一批赔率事件保守匹配到候选比赛并幂等写入（sync/backfill 共用）。"""
        for event in events:
            counters.fetched += 1
            result = match_event(
                event_home=event.home_team,
                event_away=event.away_team,
                commence_time=event.commence_time,
                candidates=candidates,
                tolerance=self._tolerance,
                alias_names=self._alias_names,
            )
            label = f"{event.home_team} vs {event.away_team} @ {event.commence_time.isoformat()}"
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

    async def _build_candidates(
        self,
        on_date: date,
        *,
        competition_id: UUID | None = None,
        fixture_ids: set[UUID] | None = None,
    ) -> _CandidateBatch:
        start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        fixtures = await self._fixtures.list_by_kickoff_window(start, end)
        requested_fixtures = len(fixture_ids) if fixture_ids is not None else len(fixtures)
        reason_counts: dict[str, int] = {}
        if fixture_ids is not None:
            found_ids = {fixture.id for fixture in fixtures}
            missing_count = len(fixture_ids - found_ids)
            if missing_count:
                reason_counts["MISSING_FIXTURE"] = missing_count
            fixtures = [fixture for fixture in fixtures if fixture.id in fixture_ids]
        if on_date == datetime.now(UTC).date():
            now = datetime.now(UTC)
            pre_kickoff_end = now + timedelta(minutes=90)
            before_window = len(fixtures)
            fixtures = [f for f in fixtures if now <= f.kickoff <= pre_kickoff_end]
            outside_window = before_window - len(fixtures)
            if outside_window:
                reason_counts["OUTSIDE_REQUEST_WINDOW"] = outside_window
        if competition_id is not None:
            # 按赛事限定候选：赔率事件只能匹配到该赛事内的比赛（更保守）。
            fixtures = [f for f in fixtures if f.competition_id == competition_id]

        candidate_sport_keys: dict[UUID, str] = {}
        if self._competitions is not None and fixtures:
            from app.config.whitelist import get_whitelist

            competition_ids = {f.competition_id for f in fixtures}
            competitions = {
                competition.id: competition
                for competition in await self._competitions.list_by_ids(competition_ids)
            }
            whitelist = get_whitelist()
            allowed_ids = set()
            for competition in competitions.values():
                league_id: int | None = None
                if competition.external_id:
                    with suppress(TypeError, ValueError):
                        league_id = int(competition.external_id)
                if whitelist.is_allowed(
                    competition.name,
                    league_id=league_id,
                    country=competition.country,
                ):
                    allowed_ids.add(competition.id)
                    sport_key = whitelist.get_sport_key_for(
                        competition.name,
                        league_id=league_id,
                        country=competition.country,
                    )
                    if sport_key is not None:
                        candidate_sport_keys[competition.id] = sport_key
            unsupported = sum(
                1 for fixture in fixtures if fixture.competition_id not in allowed_ids
            )
            if unsupported:
                reason_counts["UNSUPPORTED_COMPETITION"] = unsupported
            fixtures = [f for f in fixtures if f.competition_id in allowed_ids]
        elif len(self._sport_keys) == 1:
            candidate_sport_keys = {
                fixture.competition_id: self._sport_keys[0] for fixture in fixtures
            }

        team_ids = {f.home_team_id for f in fixtures} | {f.away_team_id for f in fixtures}
        team_names = {t.id: t.name for t in await self._teams.list_by_ids(team_ids)}

        candidates: list[MatchCandidate] = []
        sport_keys_by_fixture: dict[UUID, str] = {}
        for f in fixtures:
            home = team_names.get(f.home_team_id)
            away = team_names.get(f.away_team_id)
            if home is None or away is None:
                reason_counts["MISSING_TEAM_MAPPING"] = (
                    reason_counts.get("MISSING_TEAM_MAPPING", 0) + 1
                )
                continue  # 参考数据缺失，无法安全匹配 → 忽略该比赛
            candidates.append(
                MatchCandidate(
                    fixture_id=f.id,
                    home_norm=normalize_team_name(home),
                    away_norm=normalize_team_name(away),
                    kickoff=f.kickoff,
                )
            )
            sport_key = candidate_sport_keys.get(f.competition_id)
            if sport_key is not None:
                sport_keys_by_fixture[f.id] = sport_key
        return _CandidateBatch(
            candidates=candidates,
            sport_keys_by_fixture=sport_keys_by_fixture,
            requested_fixtures=requested_fixtures,
            reason_counts=reason_counts,
        )

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
                    provider_source=event.source,
                    provider_event_id=event.provider_id,
                )
                if await self._snapshots.add_if_absent(snapshot):
                    counters.created += 1
                else:
                    counters.existing += 1

    async def _get_or_create_bookmaker(
        self, key: str, title: str, cache: dict[str, Bookmaker]
    ) -> Bookmaker:
        canonical_key, canonical_title = canonical_bookmaker_identity(key, title)
        if canonical_key in cache:
            return cache[canonical_key]
        existing = await self._bookmakers.get_by_external_id(_BOOKMAKER_SOURCE, canonical_key)
        if existing is not None:
            cache[canonical_key] = existing
            return existing
        created = await self._bookmakers.add(
            Bookmaker(
                name=canonical_title,
                external_id=canonical_key,
                external_source=_BOOKMAKER_SOURCE,
            )
        )
        cache[canonical_key] = created
        return created
