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
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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

    async def _observe_response(self, response: httpx.Response) -> None:
        """允许具体提供器读取响应头；默认不执行任何操作。"""

    @staticmethod
    def _rate_limit_delay(response: httpx.Response) -> float | None:
        """从标准或供应商响应头计算真实限流等待秒数。"""
        now = datetime.now(UTC)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    return max(0.0, (parsed.astimezone(UTC) - now).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass

        reset = response.headers.get("x-ratelimit-reset")
        if not reset:
            return None
        try:
            numeric = float(reset)
            if numeric > now.timestamp():
                return max(0.0, numeric - now.timestamp())
            if numeric >= 0:
                return numeric
        except ValueError:
            try:
                parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                return max(0.0, (parsed.astimezone(UTC) - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None
        return None

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
        last_status: int | None = None
        retry_delay: float | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = retry_delay if retry_delay is not None else self._backoff_delay(attempt - 1)
                retry_delay = None
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

            await self._observe_response(response)

            if response.status_code in _RETRYABLE_STATUSES:
                last_status = response.status_code
                last_error = httpx.HTTPStatusError(
                    f"retryable status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if response.status_code == 429:
                    retry_delay = self._rate_limit_delay(response)
                    if retry_delay is None or attempt >= self._max_retries:
                        logger.warning(
                            "Provider rate limit status 429 for %s without retryable reset",
                            path,
                        )
                        break
                    logger.warning(
                        "Provider rate limit status 429 for %s; waiting %.2fs until reset",
                        path,
                        retry_delay,
                    )
                else:
                    logger.warning(
                        "Provider transient status %d for %s",
                        response.status_code,
                        path,
                    )
                continue

            if response.is_error:  # non-retryable 4xx (bad key, bad request, ...)
                logger.error(
                    "Provider error status %d for %s: %s",
                    response.status_code,
                    path,
                    response.text[:500],
                )
                # Detect quota exhaustion on 401 (The Odds API returns 401 instead of 429)
                detail = f"failed with status {response.status_code}"
                if response.status_code == 401:
                    x_rem = response.headers.get("x-requests-remaining")
                    if x_rem is not None and str(x_rem) == "0":
                        detail = "QUOTA_EXHAUSTED (x-requests-remaining=0, status 401)"
                    else:
                        body = (response.text or "").lower()
                        if any(kw in body for kw in ("out_of_usage", "quota", "usage limit")):
                            detail = "QUOTA_EXHAUSTED (body indicates quota, status 401)"
                        else:
                            detail = "INVALID_API_KEY (status 401)"
                raise ExternalServiceError(f"provider request to {path} {detail}")

            try:
                return response.json()
            except ValueError as exc:  # malformed JSON body
                raise ExternalServiceError(f"provider returned invalid JSON for {path}") from exc

        status_detail = f" with status {last_status}" if last_status is not None else ""
        raise ExternalServiceError(
            f"provider request to {path} failed{status_detail} "
            f"after {self._max_retries + 1} attempts"
        ) from last_error
