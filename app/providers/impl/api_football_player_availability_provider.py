"""API-Football 球员可用性 Provider 实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.core.exceptions import ExternalServiceError
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.player_availability_provider import (
    PlayerAvailabilityProvider,
)
from app.providers.schemas.player_availability import (
    ProviderAvailabilityBatch,
    ProviderPlayerAvailability,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

SOURCE_API_FOOTBALL = "api-football"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApiFootballPlayerAvailabilityProvider(
    BaseHTTPProvider,
    PlayerAvailabilityProvider,
):
    """通过 API-Football ``/injuries`` 读取比赛级球员可用性事实。"""

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

    async def get_fixture_availability(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderAvailabilityBatch:
        fixture_id = self._normalize_external_id(
            fixture_external_id,
            field="fixture_external_id",
        )
        payload = await self._get_json(
            "/injuries",
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
    ) -> ProviderAvailabilityBatch:
        if not isinstance(payload, dict):
            raise ExternalServiceError("API-Football returned a non-object payload")

        errors = payload.get("errors")
        if errors:
            raise ExternalServiceError("API-Football returned application-level errors")

        response = payload.get("response")
        if not isinstance(response, list):
            raise ExternalServiceError("API-Football response must be a list")

        records = [
            cls._parse_record(item, fixture_external_id=fixture_external_id) for item in response
        ]
        try:
            return ProviderAvailabilityBatch(
                source=SOURCE_API_FOOTBALL,
                fixture_external_id=fixture_external_id,
                captured_at=captured_at,
                response_complete=cls._is_complete(payload.get("paging")),
                records=records,
                request_reference=f"/injuries?fixture={fixture_external_id}",
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football availability batch failed validation",
            ) from exc

    @classmethod
    def _parse_record(
        cls,
        item: Any,
        *,
        fixture_external_id: str,
    ) -> ProviderPlayerAvailability:
        if not isinstance(item, dict):
            raise ExternalServiceError("API-Football injury record must be an object")

        fixture = item.get("fixture")
        player = item.get("player")
        team = item.get("team")
        if not all(isinstance(value, dict) for value in (fixture, player, team)):
            raise ExternalServiceError(
                "API-Football injury record is missing identity objects",
            )

        assert isinstance(fixture, dict)
        assert isinstance(player, dict)
        assert isinstance(team, dict)

        record_fixture_id = cls._normalize_external_id(
            fixture.get("id"),
            field="fixture.id",
        )
        if record_fixture_id != fixture_external_id:
            raise ExternalServiceError(
                "API-Football injury record fixture id does not match request",
            )

        try:
            return ProviderPlayerAvailability(
                team_external_id=cls._normalize_external_id(
                    team.get("id"),
                    field="team.id",
                ),
                player_external_id=cls._normalize_external_id(
                    player.get("id"),
                    field="player.id",
                ),
                player_name=player.get("name") or "",
                raw_status=item.get("type"),
                reason=cls._optional_text(item.get("reason")),
            )
        except ValidationError as exc:
            raise ExternalServiceError(
                "API-Football injury record failed validation",
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
