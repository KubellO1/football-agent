"""API-Football 当前球队阵容 Provider 实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.core.exceptions import ExternalServiceError
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

SOURCE_API_FOOTBALL = "api-football"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiFootballPlayerSquadProvider(BaseHTTPProvider, PlayerSquadProvider):
    """通过 API-Football ``/players/squads`` 读取球队当前阵容。"""

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

    async def get_team_squad(
        self,
        *,
        team_external_id: str,
    ) -> ProviderSquadBatch:
        team_id = self._normalize_external_id(
            team_external_id,
            field="team_external_id",
        )
        payload = await self._get_json(
            "/players/squads",
            params={"team": team_id},
        )
        return self._parse_payload(
            payload,
            team_external_id=team_id,
            captured_at=self._clock(),
        )

    @classmethod
    def _parse_payload(
        cls,
        payload: Any,
        *,
        team_external_id: str,
        captured_at: datetime,
    ) -> ProviderSquadBatch:
        if not isinstance(payload, dict):
            raise ExternalServiceError("API-Football returned a non-object payload")
        if payload.get("errors"):
            raise ExternalServiceError("API-Football returned application-level errors")

        response = payload.get("response")
        if not isinstance(response, list):
            raise ExternalServiceError("API-Football squad response must be a list")
        results = payload.get("results")
        if results is not None and (
            not isinstance(results, int) or isinstance(results, bool) or results != len(response)
        ):
            raise ExternalServiceError("API-Football squad result count is inconsistent")

        if not response:
            return cls._batch(
                team_external_id=team_external_id,
                captured_at=captured_at,
                records=[],
            )
        if len(response) != 1:
            raise ExternalServiceError("API-Football returned multiple squad objects")

        squad = response[0]
        if not isinstance(squad, dict):
            raise ExternalServiceError("API-Football squad entry must be an object")
        team = squad.get("team")
        players = squad.get("players")
        if not isinstance(team, dict) or not isinstance(players, list):
            raise ExternalServiceError("API-Football squad is missing team or players")

        response_team_id = cls._normalize_external_id(team.get("id"), field="team.id")
        if response_team_id != team_external_id:
            raise ExternalServiceError("API-Football squad team id does not match request")

        records = [cls._parse_player(player) for player in players]
        player_ids = [record.player_external_id for record in records]
        if len(player_ids) != len(set(player_ids)):
            raise ExternalServiceError("API-Football squad contains duplicate player ids")
        return cls._batch(
            team_external_id=team_external_id,
            captured_at=captured_at,
            records=records,
        )

    @classmethod
    def _parse_player(cls, player: Any) -> ProviderSquadPlayer:
        if not isinstance(player, dict):
            raise ExternalServiceError("API-Football squad player must be an object")
        try:
            return ProviderSquadPlayer(
                player_external_id=cls._normalize_external_id(
                    player.get("id"),
                    field="player.id",
                ),
                player_name=player.get("name"),
                raw_position=player.get("position"),
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football squad player failed validation",
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
    def _batch(
        *,
        team_external_id: str,
        captured_at: datetime,
        records: list[ProviderSquadPlayer],
    ) -> ProviderSquadBatch:
        return ProviderSquadBatch(
            source=SOURCE_API_FOOTBALL,
            team_external_id=team_external_id,
            captured_at=captured_at,
            response_complete=True,
            records=records,
            request_reference=f"/players/squads?team={team_external_id}",
        )
