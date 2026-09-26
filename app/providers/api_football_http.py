"""API-Football-specific HTTP safety layered over the shared client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.exceptions import ExternalServiceError
from app.providers.base import BaseHTTPProvider

if TYPE_CHECKING:
    import httpx

    from app.providers.api_football_rate_limit import ApiFootballRequestLimiter


class ApiFootballHTTPProvider(BaseHTTPProvider):
    """Pace all physical requests and reject application-level API errors."""

    def __init__(
        self,
        *,
        rate_limiter: ApiFootballRequestLimiter | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_football_limiter = rate_limiter

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        limiter = self._api_football_limiter
        if limiter is None:
            return await super()._get_json(path, params=params, headers=headers)
        async with limiter.serial():
            return await super()._get_json(path, params=params, headers=headers)

    async def _before_request(self) -> None:
        if self._api_football_limiter is not None:
            await self._api_football_limiter.admit()

    async def _observe_response(self, response: httpx.Response) -> None:
        limiter = self._api_football_limiter
        if limiter is None:
            return
        limiter.observe_headers(response.headers)
        if response.status_code == 429:
            limiter.note_rate_limit(self._rate_limit_delay(response))

    async def _is_rate_limited_payload(self, payload: Any, response: httpx.Response) -> bool:
        if not isinstance(payload, dict):
            raise ExternalServiceError("API_FOOTBALL_INVALID_PAYLOAD")
        errors = payload.get("errors")
        if isinstance(errors, dict) and any(str(key).casefold() == "ratelimit" for key in errors):
            if self._api_football_limiter is not None:
                self._api_football_limiter.note_rate_limit(self._rate_limit_delay(response))
            return True
        if errors:
            # Never include provider error values: they may contain sensitive
            # account details, URLs, or a reflected credential.
            raise ExternalServiceError(
                "API-Football returned application-level errors " "(API_FOOTBALL_APPLICATION_ERROR)"
            )
        if self._api_football_limiter is not None:
            self._api_football_limiter.note_success()
        return False

    def _retry_429_without_reset(self) -> bool:
        return True
