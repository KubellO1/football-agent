"""The Odds API implementation of :class:`OddsProvider`.

Talks to The Odds API v4 (https://the-odds-api.com/). Auth is an ``apiKey`` query
parameter. Each event carries a list of bookmakers, and each bookmaker a list of
markets; we flatten bookmaker×market into one :class:`BookmakerMarket` each.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import BookmakerMarket, OddsOutcome, ProviderFixtureOdds

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
        payload = await self._get_json(f"/sports/{sport}/odds", params=params)
        # The v4 odds endpoint returns a bare JSON array of events.
        return [self._parse_event(event) for event in payload]

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
        )
