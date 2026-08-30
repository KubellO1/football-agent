"""The Odds API implementation of :class:`OddsProvider`.

Talks to The Odds API v4 (https://the-odds-api.com/). Auth is an ``apiKey`` query
parameter. Each event carries a list of bookmakers, and each bookmaker a list of
markets; we flatten bookmaker×market into one :class:`BookmakerMarket` each.
"""

from __future__ import annotations

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

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

logger = get_logger(__name__)


class TheOddsApiProvider(BaseHTTPProvider, OddsProvider):
    """Bookmaker odds feed backed by The Odds API v4."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
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
        self._requests_made = 0

    async def _observe_response(self, response: httpx.Response) -> None:
        del response
        self._requests_made += 1

    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        params = {
            "apiKey": self._api_key,
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        try:
            payload = await self._get_json(f"/sports/{sport}/odds", params=params)
        except ExternalServiceError as exc:
            err_msg = str(exc)
            if "QUOTA_EXHAUSTED" in err_msg:
                logger.error(
                    "Odds API quota exhausted for sport key '%s'. Monthly limit reached.",
                    sport,
                )
            elif "INVALID_API_KEY" in err_msg:
                logger.error(
                    "Odds API key invalid for sport key '%s'. Check ODDS_API_KEY in .env.",
                    sport,
                )
            elif "404" in err_msg:
                logger.warning(
                    "Odds API sport key '%s' returned 404 (not supported by The Odds API). Skipping.",
                    sport,
                )
            elif "401" in err_msg or "403" in err_msg:
                logger.warning(
                    "Odds API sport key '%s' returned %s (auth failed). Skipping.",
                    sport,
                    "401" if "401" in err_msg else "403",
                )
            elif "422" in err_msg:
                logger.warning(
                    "Odds API sport key '%s' returned 422 (invalid parameters). Skipping.",
                    sport,
                )
            else:
                logger.warning(
                    "Odds API sport key '%s' failed: %s. Skipping.",
                    sport,
                    exc,
                )
            return []
        # The v4 odds endpoint returns a bare JSON array of events.
        return [self._parse_event(event) for event in payload]

    async def get_odds_for_fixtures(
        self,
        *,
        sport: str,
        fixtures: Sequence[ProviderOddsTarget],
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Query only league feeds required by the missing fixture targets."""
        if not fixtures:
            return []

        grouped: dict[str, list[ProviderOddsTarget]] = {}
        for fixture in fixtures:
            sport_key = fixture.sport_key
            if sport_key is None and sport != "football":
                sport_key = sport
            if sport_key is None:
                logger.warning(
                    "The Odds API targeted fallback skipped fixture %s: MISSING_PROVIDER_MAPPING",
                    fixture.fixture_id,
                )
                continue
            grouped.setdefault(sport_key, []).append(fixture)

        results: list[ProviderFixtureOdds] = []
        seen: set[tuple[str, str]] = set()
        for sport_key, targets in grouped.items():
            candidates = [
                MatchCandidate(
                    fixture_id=target.fixture_id,
                    home_norm=normalize_team_name(target.home_team),
                    away_norm=normalize_team_name(target.away_team),
                    kickoff=target.kickoff,
                )
                for target in targets
            ]
            for event in await self.get_odds(
                sport=sport_key,
                markets=markets,
                regions=regions,
            ):
                match = match_event(
                    event_home=event.home_team,
                    event_away=event.away_team,
                    commence_time=event.commence_time,
                    candidates=candidates,
                    tolerance=timedelta(minutes=180),
                )
                event_key = (event.source, event.provider_id)
                if match.outcome is MatchOutcome.MATCHED and event_key not in seen:
                    results.append(event)
                    seen.add(event_key)
        return results

    def stats(self) -> dict[str, int]:
        """Return actual upstream HTTP response count."""
        return {"requests_made": self._requests_made}

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        params = {
            "apiKey": self._api_key,
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "date": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            payload = await self._get_json(f"/historical/sports/{sport}/odds", params=params)
        except ExternalServiceError as exc:
            err_msg = str(exc)
            if "QUOTA_EXHAUSTED" in err_msg:
                logger.error(
                    "Historical odds API quota exhausted for sport key '%s'. Monthly limit reached.",
                    sport,
                )
            elif "INVALID_API_KEY" in err_msg:
                logger.error(
                    "Historical odds API key invalid for sport key '%s'. Check ODDS_API_KEY in .env.",
                    sport,
                )
            elif "404" in err_msg:
                logger.warning(
                    "Historical odds sport key '%s' returned 404. Skipping.",
                    sport,
                )
            elif "401" in err_msg or "403" in err_msg:
                logger.warning(
                    "Historical odds sport key '%s' returned %s (auth failed). Skipping.",
                    sport,
                    "401" if "401" in err_msg else "403",
                )
            else:
                logger.warning(
                    "Historical odds sport key '%s' failed: %s. Skipping.",
                    sport,
                    exc,
                )
            return []
        # Unlike the live endpoint, the historical endpoint wraps the event array
        # in a snapshot envelope: {timestamp, previous_timestamp, next_timestamp, data}.
        events = payload.get("data", []) if isinstance(payload, dict) else []
        return [self._parse_event(event) for event in events]

    @staticmethod
    def _parse_event(event: dict[str, Any]) -> ProviderFixtureOdds:
        """Map one Odds API event onto a ``ProviderFixtureOdds``."""
        bookmaker_markets: list[BookmakerMarket] = []
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                bookmaker_markets.append(
                    BookmakerMarket(
                        bookmaker_key=bookmaker.get("key", ""),
                        bookmaker_title=bookmaker.get("title", ""),
                        market=market.get("key", ""),
                        last_update=market.get("last_update") or bookmaker.get("last_update"),
                        outcomes=[
                            OddsOutcome(
                                name=outcome.get("name", ""),
                                price=outcome.get("price"),
                                point=outcome.get("point"),
                            )
                            for outcome in market.get("outcomes", [])
                        ],
                    )
                )

        return ProviderFixtureOdds(
            provider_id=str(event.get("id")),
            commence_time=event.get("commence_time"),
            home_team=event.get("home_team", ""),
            away_team=event.get("away_team", ""),
            sport_key=event.get("sport_key"),
            bookmakers=bookmaker_markets,
            source="the-odds-api",
        )
