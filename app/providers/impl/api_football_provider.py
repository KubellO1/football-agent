"""API-Football implementation of :class:`FixturesProvider`.

Talks to the API-Football v3 REST API (https://www.api-football.com/). Auth is a
per-request ``x-apisports-key`` header. Responses wrap the payload in a
``response`` array alongside ``errors`` / ``paging`` metadata.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam

logger = get_logger(__name__)


class ApiFootballProvider(BaseHTTPProvider, FixturesProvider):
    """Fixtures feed backed by API-Football v3."""

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
            headers={"x-apisports-key": api_key},
            client=client,
        )

    async def get_fixtures(
        self,
        *,
        on_date: date | None = None,
        league: str | int | None = None,
        season: int | None = None,
    ) -> list[ProviderFixture]:
        params: dict[str, Any] = {}
        if on_date is not None:
            params["date"] = on_date.isoformat()
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season

        payload = await self._get_json("/fixtures", params=params)
        return [self._parse_fixture(item) for item in payload.get("response", [])]

    async def get_fixture(self, provider_id: str) -> ProviderFixture | None:
        payload = await self._get_json("/fixtures", params={"id": provider_id})
        items = payload.get("response", [])
        if not items:
            return None
        return self._parse_fixture(items[0])

    @staticmethod
    def _parse_fixture(item: dict[str, Any]) -> ProviderFixture:
        """Map one API-Football ``response`` element onto a ``ProviderFixture``."""
        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        home = teams.get("home", {})
        away = teams.get("away", {})

        return ProviderFixture(
            provider_id=str(fixture.get("id")),
            kickoff=fixture.get("date"),
            status=(fixture.get("status") or {}).get("short", "NS"),
            home=ProviderTeam(provider_id=str(home.get("id")), name=home.get("name", "")),
            away=ProviderTeam(provider_id=str(away.get("id")), name=away.get("name", "")),
            league=league.get("name"),
            league_id=str(league["id"]) if league.get("id") is not None else None,
            league_country=league.get("country"),
            season=league.get("season"),
            home_score=goals.get("home"),
            away_score=goals.get("away"),
            venue=(fixture.get("venue") or {}).get("name"),
        )
