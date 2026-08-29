"""Odds-API.io implementation of :class:`OddsProvider`.

Talks to Odds-API.io v3 (https://api.odds-api.io/). Auth is an ``apiKey`` query
parameter.

**Phase 1 scope**: football only, pre-match only, 1X2 moneyline only, decimal odds.

API flow (two-step):
1. ``GET /events?sport=football`` — fetch all events (league, teams, kickoff).
2. ``GET /odds/multi?eventIds=<ids>&bookmakers=<list>`` — fetch up to ten mapped events.

Bookmaker names are **case-sensitive** (e.g. ``Bet365``).
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import (
    BookmakerMarket,
    OddsOutcome,
    ProviderFixtureOdds,
    ProviderOddsTarget,
)
from app.services.odds_matching import (
    MatchCandidate,
    MatchOutcome,
    match_event,
    normalize_team_name,
)
from app.utils.rate_limiter import TokenBucketRateLimiter, sort_events_by_kickoff

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from app.database.redis import RedisConnection

logger = get_logger(__name__)

CANONICAL_H2H_MARKET = "h2h"
MARKET_REJECTION_UNSUPPORTED = "ODDS_API_IO_UNSUPPORTED_MARKET"

# Odds-API.io has used these exact names for three-way football moneyline.
# Normalization is deliberately conservative: similar markets such as double
# chance and draw-no-bet are not aliases of the canonical h2h market.
_H2H_MARKET_ALIASES = frozenset({"1x2", "h2h", "ml", "moneyline", "money line"})


def normalize_odds_api_io_market(raw_market: object) -> str | None:
    """Map an exact Odds-API.io three-way moneyline alias to canonical ``h2h``."""
    normalized = " ".join(str(raw_market).strip().casefold().split())
    if normalized in _H2H_MARKET_ALIASES:
        return CANONICAL_H2H_MARKET
    return None


# ---------------------------------------------------------------------------
# Failure detail classes — used inside predicate / NO_ODDS envelope
# ---------------------------------------------------------------------------


class OddsRateLimitError(ExternalServiceError):
    """Rate limit exceeded (HTTP 429)."""


class OddsAuthError(ExternalServiceError):
    """Authentication / API key failure (HTTP 401)."""


class OddsProviderError(ExternalServiceError):
    """Generic provider-side error (HTTP 5xx / unexpected)."""


# ---------------------------------------------------------------------------
# Failure reason enumeration — maps to prediction_logger NO_ODDS subtypes
# ---------------------------------------------------------------------------

FAILURE_REASON = {
    "RATE_LIMIT": "NO_ODDS_RATE_LIMIT",
    "AUTH": "NO_ODDS_AUTH",
    "EVENT_NOT_FOUND": "NO_ODDS_EVENT_NOT_FOUND",
    "MARKET_NOT_FOUND": "NO_ODDS_MARKET_NOT_FOUND",
    "MAPPING_FAILED": "NO_ODDS_MAPPING_FAILED",
    "PROVIDER_ERROR": "NO_ODDS_PROVIDER_ERROR",
}

# ---------------------------------------------------------------------------
# Event DTO (returned by /events endpoint)
# ---------------------------------------------------------------------------


class _EventInfo:
    """Lightweight DTO for one Odds-API.io event row."""

    __slots__ = ("event_id", "home", "away", "date", "league_name", "league_slug", "status")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.event_id: str = str(raw.get("id", ""))
        self.home: str = str(raw.get("home", "")).strip()
        self.away: str = str(raw.get("away", "")).strip()
        self.status: str = str(raw.get("status", "")).strip().lower()

        date_str: str = str(raw.get("date", ""))
        self.date: datetime | None = None
        if date_str:
            with suppress(ValueError, TypeError):
                self.date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

        league: dict[str, Any] = raw.get("league", {}) or {}
        self.league_name: str = str(league.get("name", "")).strip()
        self.league_slug: str = str(league.get("slug", "")).strip()


# ---------------------------------------------------------------------------
# OddsApiIoProvider
# ---------------------------------------------------------------------------


class OddsApiIoProvider(BaseHTTPProvider, OddsProvider):
    """Bookmaker odds feed backed by Odds-API.io v3.

    *Phase 1*: football / pre-match / 1X2 ("ML") / decimal odds only.
    """

    # Default bookmakers to query (case-sensitive!)
    _DEFAULT_BOOKMAKERS = ("Bet365",)

    _MAX_MULTI_EVENTS = 10

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.odds-api.io/v3",
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        bookmakers: Sequence[str] | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        redis: RedisConnection | None = None,
        cache_ttl: int = 300,
        run_request_budget: int = 10,
        max_concurrent: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            client=client,
        )
        self._api_key = api_key
        self._bookmakers = tuple(bookmakers) if bookmakers else self._DEFAULT_BOOKMAKERS
        # 保留构造参数兼容性；批量端点不再使用逐赛事并发。
        self._max_concurrent = max_concurrent or self._MAX_MULTI_EVENTS
        self._rate_limiter = rate_limiter
        self._run_request_budget = run_request_budget
        self._run_requests_used = 0
        self._redis = redis
        self._cache_ttl = cache_ttl
        # Stats
        self._reqs_made: int = 0
        self._reqs_rate_limited: int = 0
        self._markets_rejected_unsupported: int = 0
        # In-memory events cache: avoids redundant /events when multiple
        # sport keys map to the same Odds-API.io slug (e.g. all "football").
        self._events_cache: dict[str, list[_EventInfo]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    # ------------------------------------------------------------------
    # Public API (OddsProvider interface)
    # ------------------------------------------------------------------

    # Maps The Odds API / API-Football sport keys to Odds-API.io sport slugs.
    # Odds-API.io only has "football" (not granular league-level keys).
    _SPORT_KEY_TO_SLUG: dict[str, str] = {
        "football": "football",
        "soccer": "football",
    }

    def _normalise_sport(self, sport: str) -> str | None:
        """Convert a granular sport key to an Odds-API.io sport slug (or None)."""
        # Exact match
        if sport in self._SPORT_KEY_TO_SLUG:
            return self._SPORT_KEY_TO_SLUG[sport]
        # Prefix match: "soccer_brazil_campeonato" → "football"
        for prefix, slug in self._SPORT_KEY_TO_SLUG.items():
            if sport.startswith(prefix + "_"):
                return slug
        return None

    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Fetch pre-match 1X2 odds for *sport* via the two-step flow.

        1. ``GET /events?sport=<sport>``
        2. Fetch mapped events in batches through ``GET /odds/multi``.

        Parameters
        ----------
        sport : The Odds API sport key (e.g. "soccer_brazil_campeonato").
            Auto-normalised to Odds-API.io slug ("football") and deduplicated
            via an in-memory events cache.
        markets / regions :
            Ignored in Phase 1 — provider hard-codes 1X2.
        """
        self._run_requests_used = 0
        # -- Normalise sport key → Odds-API.io slug --------------------------------
        api_sport: str | None = self._normalise_sport(sport)
        if api_sport is None:
            logger.info("Odds-API.io: sport '%s' not supported, skipping", sport)
            return []

        # -- Step 1: fetch events (deduplicated per instance) ----------------------
        try:
            events = await self._fetch_events(api_sport)
        except ExternalServiceError as exc:
            return self._handle_get_events_error(exc, sport)

        if not events:
            logger.info("Odds-API.io: no pending events for sport '%s'", sport)
            return []

        logger.info("Odds-API.io: fetched %d pending events for sport '%s'", len(events), sport)

        # -- Sort by kickoff: near-kickoff events get quota priority ---------------
        events = sort_events_by_kickoff(events)
        return await self._fetch_odds_batches(events)

    async def get_odds_for_fixtures(
        self,
        *,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """仅对批准的 fixture 做目录映射，再按最多十场调用 multi。"""
        del markets, regions
        self._run_requests_used = 0
        if not fixtures:
            return []
        api_sport = self._normalise_sport(sport)
        if api_sport is None:
            return []
        try:
            events = await self._fetch_events(api_sport)
        except ExternalServiceError as exc:
            return self._handle_get_events_error(exc, sport)

        candidates = [
            MatchCandidate(
                fixture_id=target.fixture_id,
                home_norm=normalize_team_name(target.home_team),
                away_norm=normalize_team_name(target.away_team),
                kickoff=target.kickoff,
            )
            for target in fixtures
        ]
        matched: list[_EventInfo] = []
        matched_fixture_ids: set[object] = set()
        for event in events:
            if event.date is None:
                continue
            result = match_event(
                event_home=event.home,
                event_away=event.away,
                commence_time=event.date,
                candidates=candidates,
                tolerance=timedelta(minutes=180),
            )
            if (
                result.outcome is MatchOutcome.MATCHED
                and result.fixture_id not in matched_fixture_ids
            ):
                matched.append(event)
                matched_fixture_ids.add(result.fixture_id)
        logger.info(
            "Odds-API.io target mapping: requested=%d matched=%d",
            len(fixtures),
            len(matched),
        )
        return await self._fetch_odds_batches(sort_events_by_kickoff(matched))

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Historical odds — uses the same two-step flow.

        For historical data the events endpoint still returns all events
        (including settled/cancelled), so we just pass everything through.
        """
        self._run_requests_used = 0
        try:
            events = await self._fetch_events(sport, include_settled=True)
        except ExternalServiceError as exc:
            return self._handle_get_events_error(exc, sport)

        if not events:
            return []

        # Filter to events near *at* (±6 hours) to reduce noise
        target = at.astimezone(UTC)
        window = 6 * 3600
        nearby = [
            e
            for e in events
            if e.date is not None and abs((e.date - target).total_seconds()) <= window
        ]
        if not nearby:
            return []

        return await self._fetch_odds_batches(sort_events_by_kickoff(nearby))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_events(
        self,
        sport: str = "football",
        *,
        include_settled: bool = False,
    ) -> list[_EventInfo]:
        """Call ``GET /events`` and return pending (or all) events.

        Uses in-memory cache per sport slug to avoid redundant API calls
        when multiple granular sport keys map to the same slug.
        """
        # --- In-memory cache hit -------------------------------------------
        if sport in self._events_cache:
            logger.info(
                "Odds-API.io /events cache hit for '%s' (%d events), skipping API call",
                sport,
                len(self._events_cache[sport]),
            )
            return self._events_cache[sport]

        # --- API call -------------------------------------------------------
        if not await self._acquire_request_budget():
            raise OddsRateLimitError("Odds-API.io local request budget exhausted")
        params: dict[str, Any] = {"sport": sport, "apiKey": self._api_key}
        payload = await self._get_json("/events", params=params)
        self._reqs_made += 1

        if not isinstance(payload, list):
            logger.warning(
                "Odds-API.io /events returned unexpected type %s for sport '%s'",
                type(payload).__name__,
                sport,
            )
            return []

        events: list[_EventInfo] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            evt = _EventInfo(raw)
            if include_settled or evt.status == "pending":
                events.append(evt)

        # Cache for subsequent calls with same sport slug
        self._events_cache[sport] = events
        logger.info(
            "Odds-API.io /events cached %d events for sport '%s'",
            len(events),
            sport,
        )
        return events

    async def _fetch_odds_batches(self, events: Sequence[_EventInfo]) -> list[ProviderFixtureOdds]:
        """通过 ``/odds/multi`` 按最多十个 event ID 批量获取赔率。"""
        results: list[ProviderFixtureOdds] = []
        event_map = {event.event_id: event for event in events if event.event_id}
        pending = list(event_map.values())
        self._cache_misses += len(pending)
        for offset in range(0, len(pending), self._MAX_MULTI_EVENTS):
            batch = pending[offset : offset + self._MAX_MULTI_EVENTS]
            if not await self._acquire_request_budget():
                self._reqs_rate_limited += 1
                logger.warning("Odds-API.io local request budget exhausted before multi batch")
                break
            params: dict[str, Any] = {
                "eventIds": ",".join(event.event_id for event in batch),
                "bookmakers": ",".join(self._bookmakers),
                "apiKey": self._api_key,
            }
            try:
                payload = await self._get_json("/odds/multi", params=params)
                self._reqs_made += 1
            except ExternalServiceError as exc:
                self._reqs_made += 1
                if "429" in str(exc):
                    self._reqs_rate_limited += 1
                logger.warning("Odds-API.io /odds/multi failed: %s", exc)
                continue
            rows = payload if isinstance(payload, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                event = event_map.get(str(row.get("id", "")))
                if event is None:
                    continue
                parsed = self._parse_odds_response(row, event)
                if parsed is not None:
                    results.append(parsed)
                    await self._cache_odds(f"odds:event:{event.event_id}", row, parsed)
        return results

    async def _acquire_request_budget(self) -> bool:
        if self._run_requests_used >= self._run_request_budget:
            return False
        if self._rate_limiter is not None and not await self._rate_limiter.acquire():
            return False
        self._run_requests_used += 1
        return True

    async def _observe_response(self, response: httpx.Response) -> None:
        if self._rate_limiter is not None:
            await self._rate_limiter.update_from_headers(response.headers)

    async def _fetch_event_odds(self, event: _EventInfo) -> ProviderFixtureOdds | None:
        """Compatibility helper using ``GET /odds/multi`` for one event.

        Respects rate limiter, uses Redis cache when available.
        """
        if not event.event_id:
            return None

        cache_key = f"odds:event:{event.event_id}"

        # --- Redis cache hit (skip API call) ----------------------------------
        if self._redis is not None:
            try:
                cached = await self._redis.cache_get(cache_key)
                if cached is not None:
                    self._cache_hits += 1
                    import json

                    data = json.loads(cached)
                    if isinstance(data, dict) and data.get("_cached"):
                        return self._parse_odds_response(
                            {
                                "bookmakers": data.get("bookmakers", {}),
                                "home": data.get("home", ""),
                                "away": data.get("away", ""),
                                "date": data.get("date", ""),
                                "id": event.event_id,
                            },
                            event,
                        )
                    # Stale/empty cache marker → skip
                    if data.get("_empty"):
                        return None
            except Exception:
                pass  # cache read failure → fall through to API call
        self._cache_misses += 1

        # --- Rate limiter check -----------------------------------------------
        if self._rate_limiter is not None and not await self._rate_limiter.acquire():
            self._reqs_rate_limited += 1
            logger.info("Odds-API.io rate budget exhausted for event '%s'", event.event_id)
            return None

        params: dict[str, Any] = {
            "eventIds": event.event_id,
            "bookmakers": ",".join(self._bookmakers),
            "apiKey": self._api_key,
        }
        try:
            payload = await self._get_json("/odds/multi", params=params)
            self._reqs_made += 1
        except ExternalServiceError as exc:
            self._reqs_made += 1
            err_msg = str(exc)
            if "404" in err_msg:
                # Cache 404s briefly to avoid repeated misses
                await self._cache_empty(cache_key)
                logger.debug("Odds-API.io /odds 404 for event '%s'", event.event_id)
            elif "401" in err_msg:
                logger.error("Odds-API.io auth failure on event '%s'", event.event_id)
            elif "429" in err_msg:
                self._reqs_rate_limited += 1
                await self._cache_empty(cache_key)
                logger.warning("Odds-API.io rate limited on event '%s'", event.event_id)
            else:
                logger.warning("Odds-API.io /odds failed for event '%s': %s", event.event_id, exc)
            return None

        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return None

        row = payload[0]
        result = self._parse_odds_response(row, event)
        await self._cache_odds(cache_key, row, result)
        return result

    async def _cache_odds(
        self, cache_key: str, raw: dict[str, Any], result: ProviderFixtureOdds | None
    ) -> None:
        """Cache odds response in Redis (if available)."""
        if self._redis is None:
            return
        try:
            import json

            if result is not None and raw.get("bookmakers"):
                cached = {
                    "_cached": True,
                    "home": raw.get("home", ""),
                    "away": raw.get("away", ""),
                    "date": raw.get("date", ""),
                    "bookmakers": raw["bookmakers"],
                }
                await self._redis.cache_set(cache_key, json.dumps(cached), ttl=self._cache_ttl)
            else:
                await self._cache_empty(cache_key)
        except Exception:
            pass

    async def _cache_empty(self, cache_key: str) -> None:
        """Cache a marker indicating no data for this event (shorter TTL)."""
        if self._redis is None:
            return
        try:
            import json

            empty = {"_empty": True}
            # Short TTL for failures (60s) vs success (5 min)
            await self._redis.cache_set(cache_key, json.dumps(empty), ttl=60)
        except Exception:
            pass

    def stats(self) -> dict[str, int]:
        """Return provider statistics."""
        return {
            "requests_made": self._reqs_made,
            "requests_rate_limited": self._reqs_rate_limited,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "markets_rejected_unsupported": self._markets_rejected_unsupported,
        }

    def _parse_odds_response(
        self,
        data: dict[str, Any],
        event: _EventInfo,
    ) -> ProviderFixtureOdds | None:
        """Transform one ``/odds`` JSON dict into a ``ProviderFixtureOdds``.

        The response looks like::

            {
              "id": 68687762,
              "home": "...", "away": "...",
              "date": "2026-07-22T21:30:00Z",
              "bookmakers": {
                "Bet365": [{"name": "ML", "odds": [{"home": "1.17", "draw": "6.00", "away": "12.00"}]}]
              }
            }
        """
        bookmakers = data.get("bookmakers", {})
        if not isinstance(bookmakers, dict) or not bookmakers:
            logger.debug("Odds-API.io event '%s': no bookmakers data", event.event_id)
            return None

        commence_time: datetime | None = None
        date_str = str(data.get("date", event.date or ""))
        if date_str:
            try:
                commence_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                commence_time = event.date

        home_team = str(data.get("home", event.home)).strip()
        away_team = str(data.get("away", event.away)).strip()

        bookmaker_markets: list[BookmakerMarket] = []

        for bk_name, markets in bookmakers.items():
            if not isinstance(markets, list):
                continue

            for market in markets:
                if not isinstance(market, dict):
                    continue
                market_name = market.get("name", "")
                canonical_market = normalize_odds_api_io_market(market_name)
                if canonical_market is None:
                    self._markets_rejected_unsupported += 1
                    logger.info(
                        "Odds-API.io market rejected reason_code=%s event_id=%s "
                        "bookmaker=%s provider_market=%r",
                        MARKET_REJECTION_UNSUPPORTED,
                        event.event_id,
                        bk_name,
                        market_name,
                    )
                    continue

                odds_list: list[dict[str, Any]] = market.get("odds", [])
                if not odds_list:
                    continue

                for odds_entry in odds_list:
                    home_price = self._to_float(odds_entry.get("home"))
                    away_price = self._to_float(odds_entry.get("away"))
                    draw_price = self._to_float(odds_entry.get("draw"))

                    if home_price is None or away_price is None or draw_price is None:
                        logger.debug(
                            "Odds-API.io event '%s' bookmaker '%s': missing price (h=%s a=%s d=%s)",
                            event.event_id,
                            bk_name,
                            odds_entry.get("home"),
                            odds_entry.get("away"),
                            odds_entry.get("draw"),
                        )
                        continue

                    # Negative / zero prices are invalid
                    if home_price <= 0 or away_price <= 0 or draw_price <= 0:
                        continue

                    last_update: datetime | None = None
                    updated = market.get("updatedAt", "")
                    if updated:
                        with suppress(ValueError, TypeError):
                            last_update = datetime.fromisoformat(updated.replace("Z", "+00:00"))

                    bookmaker_markets.append(
                        BookmakerMarket(
                            bookmaker_key=bk_name,
                            bookmaker_title=bk_name,
                            market=canonical_market,
                            last_update=last_update,
                            outcomes=[
                                OddsOutcome(name=home_team, price=float(home_price)),
                                OddsOutcome(name="Draw", price=float(draw_price)),
                                OddsOutcome(name=away_team, price=float(away_price)),
                            ],
                        )
                    )

                # Only take the first odds entry per market (skip alternates)
                break  # exit odds_list loop

        if not bookmaker_markets:
            logger.debug(
                "Odds-API.io event '%s': no 1X2 market found (%s vs %s)",
                event.event_id,
                home_team,
                away_team,
            )
            return None

        return ProviderFixtureOdds(
            provider_id=str(event.event_id),
            commence_time=commence_time,
            home_team=home_team,
            away_team=away_team,
            sport_key=f"football_{event.league_slug}" if event.league_slug else "football",
            bookmakers=bookmaker_markets,
            source="odds-api.io",
        )

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """Safely convert a value (str / int / float) to float, returning ``None`` on failure."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _handle_get_events_error(
        self,
        exc: ExternalServiceError,
        sport: str,
    ) -> list[ProviderFixtureOdds]:
        """Classify ``/events`` failures."""
        err_msg = str(exc)
        if "429" in err_msg:
            logger.error("Odds-API.io /events rate limited for sport '%s'", sport)
            raise OddsRateLimitError(
                f"Odds-API.io rate limited on /events for sport '{sport}'"
            ) from exc
        if "401" in err_msg:
            logger.error("Odds-API.io /events auth failed for sport '%s'", sport)
            raise OddsAuthError(f"Odds-API.io auth failed on /events for sport '{sport}'") from exc
        logger.error("Odds-API.io /events failed for sport '%s': %s", sport, exc)
        raise OddsProviderError(
            f"Odds-API.io provider error on /events for sport '{sport}'"
        ) from exc
