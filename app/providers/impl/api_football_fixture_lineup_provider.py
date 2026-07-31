"""API-Football 比赛官方阵容 Provider 实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.core.exceptions import ExternalServiceError
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.fixture_lineup_provider import FixtureLineupProvider
from app.providers.schemas.fixture_lineup import (
    ProviderFixtureLineupBatch,
    ProviderLineupPlayer,
    ProviderTeamLineup,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

SOURCE_API_FOOTBALL = "api-football"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiFootballFixtureLineupProvider(BaseHTTPProvider, FixtureLineupProvider):
    """通过 API-Football ``/fixtures/lineups`` 读取官方阵容。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            headers={"x-apisports-key": api_key},
            client=client,
        )
        self._clock = clock or _utcnow

    async def get_fixture_lineups(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderFixtureLineupBatch:
        fixture_id = self._normalize_external_id(
            fixture_external_id,
            field="fixture_external_id",
        )
        payload = await self._get_json(
            "/fixtures/lineups",
            params={"fixture": fixture_id},
        )
        return self._parse_payload(
            payload,
            fixture_external_id=fixture_id,
            captured_at=self._clock(),
        )

    @classmethod
    def _parse_payload(
        cls,
        payload: Any,
        *,
        fixture_external_id: str,
        captured_at: datetime,
    ) -> ProviderFixtureLineupBatch:
        if not isinstance(payload, dict):
            raise ExternalServiceError("API-Football returned a non-object payload")
        if payload.get("errors"):
            raise ExternalServiceError("API-Football returned application-level errors")

        response = payload.get("response")
        if not isinstance(response, list):
            raise ExternalServiceError("API-Football lineup response must be a list")
        results = payload.get("results")
        if results is not None and (
            not isinstance(results, int) or isinstance(results, bool) or results != len(response)
        ):
            raise ExternalServiceError("API-Football lineup result count is inconsistent")

        lineups = [cls._parse_team_lineup(item) for item in response]
        try:
            return ProviderFixtureLineupBatch(
                source=SOURCE_API_FOOTBALL,
                fixture_external_id=fixture_external_id,
                captured_at=captured_at,
                response_complete=cls._is_complete(payload.get("paging")),
                lineups=lineups,
                request_reference=f"/fixtures/lineups?fixture={fixture_external_id}",
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football fixture lineup batch failed validation",
            ) from exc

    @classmethod
    def _parse_team_lineup(cls, item: Any) -> ProviderTeamLineup:
        if not isinstance(item, dict):
            raise ExternalServiceError("API-Football team lineup must be an object")
        team = item.get("team")
        if not isinstance(team, dict):
            raise ExternalServiceError("API-Football team lineup is missing team identity")

        starting = cls._parse_players(item.get("startXI"), field="startXI")
        substitutes = cls._parse_players(item.get("substitutes"), field="substitutes")
        try:
            return ProviderTeamLineup(
                team_external_id=cls._normalize_external_id(
                    team.get("id"),
                    field="team.id",
                ),
                formation=cls._optional_text(item.get("formation")),
                starting=starting,
                substitutes=substitutes,
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football team lineup failed validation",
            ) from exc

    @classmethod
    def _parse_players(
        cls,
        items: Any,
        *,
        field: str,
    ) -> list[ProviderLineupPlayer]:
        if not isinstance(items, list):
            raise ExternalServiceError(f"API-Football lineup {field} must be a list")
        return [cls._parse_player(item) for item in items]

    @classmethod
    def _parse_player(cls, item: Any) -> ProviderLineupPlayer:
        if not isinstance(item, dict):
            raise ExternalServiceError("API-Football lineup player entry must be an object")
        player = item.get("player")
        if not isinstance(player, dict):
            raise ExternalServiceError("API-Football lineup entry is missing player identity")

        try:
            return ProviderLineupPlayer(
                player_external_id=cls._normalize_external_id(
                    player.get("id"),
                    field="player.id",
                ),
                player_name=player.get("name"),
                raw_position=player.get("pos"),
                shirt_number=player.get("number"),
                grid_position=cls._optional_text(player.get("grid")),
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football lineup player failed validation",
            ) from exc

    @staticmethod
    def _normalize_external_id(value: Any, *, field: str) -> str:
        if value is None:
            raise ExternalServiceError(f"API-Football record is missing {field}")
        normalized = str(value).strip()
        if not normalized or len(normalized) > 120:
            raise ExternalServiceError(f"API-Football record has invalid {field}")
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _is_complete(paging: Any) -> bool:
        if not isinstance(paging, dict):
            return False
        current = paging.get("current")
        total = paging.get("total")
        if (
            not isinstance(current, int)
            or isinstance(current, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or current < 1
            or total < 1
        ):
            return False
        return current == total
