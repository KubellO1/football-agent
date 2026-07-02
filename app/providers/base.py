"""Shared async HTTP foundation for external providers.

Every provider talks to its upstream through :class:`BaseHTTPProvider`, which
centralises the cross-cutting concerns that must not be re-implemented per
vendor: a configured timeout, bounded retries with exponential backoff on
*transient* failures, and translation of any terminal failure into the app's
``ExternalServiceError``. Vendor-specific details (auth, paths, payload shape)
live in the concrete subclasses.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

# HTTP statuses worth retrying: rate limiting and transient server errors.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class BaseHTTPProvider:
    """Owns one ``httpx.AsyncClient`` and adds timeout + retry semantics."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``client`` injection exists purely so tests can pass a MockTransport.
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers or {},
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds

    async def aclose(self) -> None:
        """Close the underlying connection pool (call on shutdown)."""
        await self._client.aclose()

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff for a zero-based attempt index."""
        return self._backoff_base * (2.0**attempt)

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET ``path`` and return decoded JSON, retrying transient failures.

        Raises :class:`ExternalServiceError` on non-retryable responses, invalid
        JSON, or once the retry budget is exhausted.
        """
        # Total attempts = 1 initial + max_retries retries.
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = self._backoff_delay(attempt - 1)
                logger.warning(
                    "Provider retry %d/%d for %s after %.2fs",
                    attempt,
                    self._max_retries,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)

            try:
                response = await self._client.get(path, params=params, headers=headers)
            except httpx.TransportError as exc:  # timeouts, connection/read errors
                last_error = exc
                logger.warning("Provider transport error for %s: %s", path, exc)
                continue

            if response.status_code in _RETRYABLE_STATUSES:
                last_error = httpx.HTTPStatusError(
                    f"retryable status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                logger.warning("Provider transient status %d for %s", response.status_code, path)
                continue

            if response.is_error:  # non-retryable 4xx (bad key, bad request, ...)
                logger.error(
                    "Provider error status %d for %s: %s",
                    response.status_code,
                    path,
                    response.text[:500],
                )
                raise ExternalServiceError(
                    f"provider request to {path} failed with status {response.status_code}"
                )

            try:
                return response.json()
            except ValueError as exc:  # malformed JSON body
                raise ExternalServiceError(f"provider returned invalid JSON for {path}") from exc

        raise ExternalServiceError(
            f"provider request to {path} failed after {self._max_retries + 1} attempts"
        ) from last_error
