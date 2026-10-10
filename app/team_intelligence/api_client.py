"""Raw-preserving API-Football client for isolated evidence collection."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.team_intelligence.contracts import RawPayload, SourcePolicy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.providers.api_football_rate_limit import ApiFootballRequestLimiter


class ApiFootballEvidenceClient:
    """Return immutable raw JSON while preserving the production pacing semantics."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        limiter: ApiFootballRequestLimiter,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("API_FOOTBALL_KEY is not configured")
        self._limiter = limiter
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"x-apisports-key": api_key},
        )
        self._owns_client = client is None

    @property
    def requests(self) -> int:
        return int(self._limiter.physical_requests)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None],
    ) -> RawPayload:
        async with self._limiter.serial():
            for attempt in range(self._max_retries + 1):
                await self._limiter.admit()
                response = await self._client.get(path, params=params)
                self._limiter.observe_headers(response.headers)
                if response.status_code == 429:
                    delay = _retry_delay(response, attempt)
                    self._limiter.note_rate_limit(delay)
                    if attempt >= self._max_retries:
                        raise ExternalServiceError("API_FOOTBALL_HTTP_429")
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                payload = _validated_payload(response)
                if _has_rate_limit_error(payload):
                    delay = _retry_delay(response, attempt)
                    self._limiter.note_rate_limit(delay)
                    if attempt >= self._max_retries:
                        raise ExternalServiceError("API_FOOTBALL_BODY_RATE_LIMIT")
                    await asyncio.sleep(delay)
                    continue
                if payload.get("errors"):
                    raise ExternalServiceError("API_FOOTBALL_APPLICATION_ERROR")
                self._limiter.note_success()
                return RawPayload(
                    source_name="api-football",
                    source_url=str(response.request.url),
                    captured_at=datetime.now(UTC),
                    content=response.content,
                    media_type=response.headers.get("content-type", "application/json"),
                    source_policy=SourcePolicy.EXISTING_LICENSED_API,
                    response_headers=response.headers,
                )
        raise AssertionError("bounded retry loop exhausted unexpectedly")


def _validated_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        value: Any = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise ExternalServiceError("API_FOOTBALL_INVALID_JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("response"), list):
        raise ExternalServiceError("API_FOOTBALL_INVALID_PAYLOAD")
    return value


def _has_rate_limit_error(payload: Mapping[str, Any]) -> bool:
    errors = payload.get("errors")
    return isinstance(errors, dict) and any(str(key).casefold() == "ratelimit" for key in errors)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("retry-after")
    if value:
        try:
            return min(max(float(value), 0.0), 60.0)
        except ValueError:
            pass
    return float(min(2 ** (attempt + 1), 8))
