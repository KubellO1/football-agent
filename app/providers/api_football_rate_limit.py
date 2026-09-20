"""Conservative, process-local API-Football request admission.

The same limiter instance is shared by all API-Football provider adapters in
one application container. Every physical HTTP attempt, including a retry,
consumes the run, minute and day budgets. Server-reported remaining counts can
only tighten those budgets. A process restart resets the local counters, so a
production rollout with multiple writer processes needs a shared quota ledger.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.exceptions import ExternalServiceError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
    from datetime import date


class ApiFootballRequestLimiter:
    """Serialise API-Football requests and enforce deliberately low local caps."""

    def __init__(
        self,
        *,
        min_interval_seconds: float,
        per_minute_budget: int,
        daily_budget: int,
        run_budget: int,
        circuit_cooldown_seconds: float,
        clock: Callable[[], float] | None = None,
        utc_day: Callable[[], date] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if (
            min_interval_seconds < 0
            or per_minute_budget < 1
            or daily_budget < 1
            or run_budget < 1
            or circuit_cooldown_seconds < 1
        ):
            raise ValueError("invalid API-Football request limits")
        self._min_interval = min_interval_seconds
        self._minute_budget = per_minute_budget
        self._daily_budget = daily_budget
        self._run_budget = run_budget
        self._cooldown = circuit_cooldown_seconds
        self._clock = clock or time.monotonic
        self._utc_day = utc_day or (lambda: datetime.now(UTC).date())
        self._sleep = sleep or asyncio.sleep
        self._serial_lock = asyncio.Lock()
        self._minute_calls: deque[float] = deque()
        self._day = self._utc_day()
        self._daily_used = 0
        self._run_used = 0
        self._last_call_at: float | None = None
        self._server_daily_limit: int | None = None
        self._server_minute_limit: int | None = None
        self._server_daily_remaining: int | None = None
        self._server_minute_remaining: int | None = None
        self._server_minute_reset_at: float | None = None
        self._consecutive_limits = 0
        self._circuit_until = 0.0

    @property
    def physical_requests(self) -> int:
        """Number of outgoing attempts admitted in this process/run."""
        return self._run_used

    @asynccontextmanager
    async def serial(self) -> AsyncIterator[None]:
        """Allow only one in-flight request/retry chain across adapters."""
        async with self._serial_lock:
            yield

    async def admit(self) -> None:
        """Wait for a safe slot, or fail closed when a hard budget is spent."""
        while True:
            now = self._clock()
            if self._utc_day() != self._day:
                self._day = self._utc_day()
                self._daily_used = 0
                self._server_daily_remaining = None
            while self._minute_calls and now - self._minute_calls[0] >= 60:
                self._minute_calls.popleft()
            if now < self._circuit_until:
                raise ExternalServiceError("API_FOOTBALL_CIRCUIT_OPEN")
            if self._run_used >= self._run_budget:
                raise ExternalServiceError("API_FOOTBALL_RUN_BUDGET_EXHAUSTED")
            daily_ceiling = (
                min(self._daily_budget, self._server_daily_limit)
                if self._server_daily_limit is not None
                else self._daily_budget
            )
            if self._daily_used >= daily_ceiling or (
                self._server_daily_remaining is not None and self._server_daily_remaining <= 0
            ):
                raise ExternalServiceError("API_FOOTBALL_DAILY_BUDGET_EXHAUSTED")

            delay = 0.0
            if self._last_call_at is not None:
                delay = max(delay, self._last_call_at + self._min_interval - now)
            minute_ceiling = (
                min(self._minute_budget, self._server_minute_limit)
                if self._server_minute_limit is not None
                else self._minute_budget
            )
            if minute_ceiling == 0:
                raise ExternalServiceError("API_FOOTBALL_MINUTE_BUDGET_EXHAUSTED")
            if len(self._minute_calls) >= minute_ceiling:
                delay = max(delay, self._minute_calls[0] + 60 - now)
            if self._server_minute_remaining == 0:
                reset = self._server_minute_reset_at
                if reset is not None and now < reset:
                    delay = max(delay, reset - now)
                else:
                    self._server_minute_remaining = None
                    self._server_minute_reset_at = None
            if delay > 0:
                await self._sleep(delay)
                continue

            self._last_call_at = now
            self._minute_calls.append(now)
            self._daily_used += 1
            self._run_used += 1
            if self._server_daily_remaining is not None:
                self._server_daily_remaining -= 1
            if self._server_minute_remaining is not None:
                self._server_minute_remaining -= 1
            return

    def observe_headers(self, headers: Mapping[str, str]) -> None:
        """Use provider quota headers to tighten admission, never to relax it."""
        daily_limit = self._positive_or_zero(headers.get("x-ratelimit-requests-limit"))
        minute_limit = self._positive_or_zero(headers.get("x-ratelimit-limit"))
        daily = self._positive_or_zero(headers.get("x-ratelimit-requests-remaining"))
        minute = self._positive_or_zero(headers.get("x-ratelimit-remaining"))
        if daily_limit is not None:
            self._server_daily_limit = daily_limit
        if minute_limit is not None:
            self._server_minute_limit = minute_limit
        if daily is not None:
            self._server_daily_remaining = daily
        if minute is not None:
            self._server_minute_remaining = minute
            self._server_minute_reset_at = self._clock() + 60 if minute == 0 else None

    def note_success(self) -> None:
        """A usable payload, not merely HTTP 200, closes a failure streak."""
        self._consecutive_limits = 0

    def note_rate_limit(self, retry_delay: float | None) -> None:
        """Open a bounded circuit after repeated provider-level limit failures."""
        self._consecutive_limits += 1
        if self._consecutive_limits >= 2:
            self._circuit_until = self._clock() + max(self._cooldown, retry_delay or 0.0)

    @staticmethod
    def _positive_or_zero(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
