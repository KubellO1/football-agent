"""Odds-API.io implementation of :class:`OddsProvider`.

Talks to Odds-API.io v3 (https://api.odds-api.io/). Auth is an ``apiKey`` query
parameter.

**Phase 1 scope**: football only, pre-match only, 1X2 moneyline only, decimal odds.

API flow (two-step):
1. ``GET /events?sport=football`` — fetch all events (league, teams, kickoff).
2. ``GET /odds?eventId=<id>&bookmakers=<list>`` — fetch odds per event.

Bookmaker names are **case-sensitive** (e.g. ``10BET`` not ``10bet``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.database.redis import RedisConnection
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import BookmakerMarket, OddsOutcome, ProviderFixtureOdds
from app.utils.rate_limiter import TokenBucketRateLimiter, sort_events_by_kickoff

logger = get_logger(__name__)

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
            try:
                self.date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

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

    # Market names in Odds-API.io response
    _MARKET_1X2 = "ML"

    # Default bookmakers to query (case-sensitive!)
    _DEFAULT_BOOKMAKERS = (
        "10BET",
        "Bet365",
    )

    # How many parallel odds requests to fire at once
    _MAX_CONCURRENT = 8

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
        self._max_concurrent = max_concurrent or self._MAX_CONCURRENT
        self._rate_limiter = rate_limiter
        self._redis = redis
        self._cache_ttl = cache_ttl
        # Stats
        self._reqs_made: int = 0
        self._reqs_rate_limited: int = 0
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
        markets: str = "1x2",
        regions: str = "eu",
    ) -> list[ProviderFixtureOdds]:
        """Fetch pre-match 1X2 odds for *sport* via the two-step flow.

        1. ``GET /events?sport=<sport>``
        2. For each pending/future event: ``GET /odds?eventId=<id>&bookmakers=...``

        Parameters
        ----------
        sport : The Odds API sport key (e.g. "soccer_brazil_campeonato").
            Auto-normalised to Odds-API.io slug ("football") and deduplicated
            via an in-memory events cache.
        markets / regions :
            Ignored in Phase 1 — provider hard-codes 1X2.
        """
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

        # -- Reserve tokens for near-kickoff events (within 3 hours) ---------------
        if self._rate_limiter is not None:
            near_threshold = datetime.now(UTC).timestamp() + 3 * 3600
            near_count = sum(
                1 for e in events if e.date is not None and e.date.timestamp() <= near_threshold
            )
            if near_count > 0:
                budget = await self._rate_limiter.budget()
                # Reserve up to 80% of remaining budget for near-kickoff, min 5 events
                reserve = min(near_count, max(5, int(budget.remaining * 0.8)))
                logger.info(
                    "Odds-API.io: reserving %d tokens for %d near-kickoff events "
                    "(budget remaining=%d)",
                    reserve, near_count, budget.remaining,
                )
                self._near_kickoff_reserve = reserve

        # -- Step 2: fetch odds per event (batched in parallel) --------------------
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _one_event(event: _EventInfo) -> ProviderFixtureOdds | None:
            async with semaphore:
                return await self._fetch_event_odds(event)

        tasks = [_one_event(evt) for evt in events]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[ProviderFixtureOdds] = []
        for item in raw_results:
            if item is None:
                continue
            if isinstance(item, BaseException):
                logger.debug("Odds-API.io event fetch exception: %s", item)
                continue
            if isinstance(item, ProviderFixtureOdds):
                results.append(item)
        return results

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: str = "1x2",
        regions: str = "eu",
    ) -> list[ProviderFixtureOdds]:
        """Historical odds — uses the same two-step flow.

        For historical data the events endpoint still returns all events
        (including settled/cancelled), so we just pass everything through.
        """
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

        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _one_event(event: _EventInfo) -> ProviderFixtureOdds | None:
            async with semaphore:
                return await self._fetch_event_odds(event)

        tasks = [_one_event(evt) for evt in nearby]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[ProviderFixtureOdds] = []
        for item in raw_results:
            if isinstance(item, ProviderFixtureOdds):
                results.append(item)
        return results

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
                "Odds-API.io /events cache hit for '%s' (%d events), "
                "skipping API call",
                sport, len(self._events_cache[sport]),
            )
            return self._events_cache[sport]

        # --- API call -------------------------------------------------------
        params: dict[str, Any] = {"sport": sport, "apiKey": self._api_key}
        payload = await self._get_json("/events", params=params)

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
            if include_settled:
                events.append(evt)
            elif evt.status == "pending":
                events.append(evt)

        # Cache for subsequent calls with same sport slug
        self._events_cache[sport] = events
        logger.info(
            "Odds-API.io /events cached %d events for sport '%s'",
            len(events), sport,
        )
        return events

    async def _fetch_event_odds(self, event: _EventInfo) -> ProviderFixtureOdds | None:
        """Call ``GET /odds`` for a single event and parse into DTO.

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
                            {"bookmakers": data.get("bookmakers", {}),
                             "home": data.get("home", ""), "away": data.get("away", ""),
                             "date": data.get("date", ""), "id": event.event_id},
                            event,
                        )
                    # Stale/empty cache marker → skip
                    if data.get("_empty"):
                        return None
            except Exception:
                pass  # cache read failure → fall through to API call
        self._cache_misses += 1

        # --- Rate limiter check -----------------------------------------------
        if self._rate_limiter is not None:
            if not await self._rate_limiter.acquire():
                self._reqs_rate_limited += 1
                logger.info("Odds-API.io rate budget exhausted for event '%s'", event.event_id)
                return None

        params: dict[str, Any] = {
            "eventId": event.event_id,
            "bookmakers": ",".join(self._bookmakers),
            "apiKey": self._api_key,
        }
        try:
            payload = await self._get_json("/odds", params=params)
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

        if not isinstance(payload, dict):
            return None

        result = self._parse_odds_response(payload, event)
        await self._cache_odds(cache_key, payload, result)
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
                "10BET": [{"name": "ML", "odds": [{"home": "1.17", "draw": "6.00", "away": "12.00"}]}]
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
                if market_name != self._MARKET_1X2:
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
                        try:
                            last_update = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                    bookmaker_markets.append(
                        BookmakerMarket(
                            bookmaker_key=bk_name,
                            bookmaker_title=bk_name,
                            market="1x2",
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
            raise OddsAuthError(
                f"Odds-API.io auth failed on /events for sport '{sport}'"
            ) from exc
        logger.error("Odds-API.io /events failed for sport '%s': %s", sport, exc)
        raise OddsProviderError(
            f"Odds-API.io provider error on /events for sport '{sport}'"
        ) from exc
