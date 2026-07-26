"""API-Football injuries implementation of :class:`InjuryProvider`.

Uses the API-Football ``/v3/injuries`` endpoint (requires the same API key and
client as ``ApiFootballProvider``). The endpoint accepts ``fixture``, ``league``,
``season``, ``team`` filters — we primarily query by ``fixture_id`` for per-match
availability checks in the recommendation gate.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.injury_provider import InjuryProvider
from app.providers.schemas.injury import PlayerInjury, TeamInjuries

logger = get_logger(__name__)


class ApiFootballInjuryProvider(BaseHTTPProvider, InjuryProvider):
    """Injury feed backed by API-Football /v3/injuries."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://v3.football.api-sports.io",
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

    async def get_injuries(
        self,
        *,
        fixture_id: int,
    ) -> list[TeamInjuries]:
        """Query injuries for a specific fixture, grouped by team."""
        params: dict[str, Any] = {"fixture": fixture_id}
        try:
            payload = await self._get_json("/injuries", params=params)
        except Exception:
            logger.warning(
                "Injury fetch failed for fixture=%d", fixture_id, exc_info=True
            )
            return []

        response = payload.get("response", []) if isinstance(payload, dict) else []
        if not response:
            return []

        return self._group_by_team(response, fixture_id)

    @staticmethod
    def _group_by_team(
        items: list[dict[str, Any]], fixture_id: int
    ) -> list[TeamInjuries]:
        """Group injury records by team_id, returning one TeamInjuries per team."""
        players: list[PlayerInjury] = []
        for item in items:
            player_data = item.get("player", {}) if isinstance(item, dict) else {}
            team_data = item.get("team", {}) if isinstance(item, dict) else {}
            fixture_data = item.get("fixture", {}) if isinstance(item, dict) else {}
            league_data = item.get("league", {}) if isinstance(item, dict) else {}

            players.append(
                PlayerInjury(
                    player_id=player_data.get("id", 0),
                    player_name=player_data.get("name", ""),
                    player_photo=player_data.get("photo", ""),
                    team_id=team_data.get("id", 0),
                    team_name=team_data.get("name", ""),
                    team_logo=team_data.get("logo", ""),
                    injury_type=item.get("type", ""),
                    injury_reason=item.get("reason", ""),
                    fixture_id=fixture_data.get("id"),
                    fixture_date=fixture_data.get("date"),
                    league_id=league_data.get("id"),
                    league_name=league_data.get("name", ""),
                    league_season=league_data.get("season"),
                )
            )

        # Group by team_id
        by_team: dict[int, list[PlayerInjury]] = defaultdict(list)
        for p in players:
            by_team[p.team_id].append(p)

        return [
            TeamInjuries(
                fixture_id=fixture_id,
                team_id=tid,
                team_name=plist[0].team_name,
                players=plist,
                total_injured=len(plist),
            )
            for tid, plist in by_team.items()
        ]
