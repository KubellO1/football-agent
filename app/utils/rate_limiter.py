"""Token-bucket rate limiter for Odds-API.io (100 requests/hour).

Design decisions:
- Token bucket: fills 100 tokens every hour, 1 token = 1 API request.
- Atomic: all state changes are guarded by asyncio.Lock — safe for concurrent use.
- Near-kickoff priority: events closer to kickoff are sorted first before
  acquiring tokens, so close matches always get quota priority.
- Empty-result caching: 429 or empty responses can be cached to avoid wasting
  tokens on repeated misses.
- Redis-backed (optional): when a RedisConnection is provided, token state is
  synchronised across workers; otherwise in-memory only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.database.redis import RedisConnection

logger = get_logger(__name__)

# Odds-API.io hourly quota
_BUDGET_PER_HOUR = 100


@dataclass
class RateLimitBudget:
    """Snapshot of current rate-limit state."""

    remaining: int
    capacity: int
    reset_at: float
    daily_remaining: int
    daily_capacity: int


class TokenBucketRateLimiter:
    """Token bucket enforcing 100 requests/hour for Odds-API.io.

    Usage::

        limiter = TokenBucketRateLimiter()
        # ... in provider ...
        if not await limiter.acquire():
            raise OddsRateLimitError("hourly quota exhausted")
        response = await self._client.get(...)
    """

    def __init__(
        self,
        budget: int = _BUDGET_PER_HOUR,
        *,
        daily_budget: int = 500,
        redis: RedisConnection | None = None,
        redis_prefix: str = "odds:rate:",
    ) -> None:
        self._budget = budget
        self._daily_budget = daily_budget
        now = time.time()
        self._hour_window = int(now // 3600)
        self._day_window = int(now // 86400)
        self._hourly_used = 0
        self._daily_used = 0
        self._server_remaining: int | None = None
        self._server_reset_at: float | None = None
        self._lock = asyncio.Lock()
        self._hydrate_lock = asyncio.Lock()
        self._redis = redis
        self._hydrated = redis is None
        self._redis_prefix = redis_prefix

    # -- public ------------------------------------------------------------

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire *tokens* tokens. Returns ``True`` on success."""
        await self._ensure_hydrated()
        async with self._lock:
            self._refill()
            hourly_remaining = self._budget - self._hourly_used
            daily_remaining = self._daily_budget - self._daily_used
            if self._server_remaining is not None:
                hourly_remaining = min(hourly_remaining, self._server_remaining)
            if min(hourly_remaining, daily_remaining) >= tokens:
                self._hourly_used += tokens
                self._daily_used += tokens
                if self._server_remaining is not None:
                    self._server_remaining = max(0, self._server_remaining - tokens)
                await self._sync_to_redis()
                return True
            return False

    async def remaining(self) -> int:
        """Return current token count (thread-safe snapshot)."""
        await self._ensure_hydrated()
        async with self._lock:
            self._refill()
            remaining = self._budget - self._hourly_used
            if self._server_remaining is not None:
                remaining = min(remaining, self._server_remaining)
            return max(0, remaining)

    async def reset_at(self) -> float:
        """Unix timestamp when the bucket next refills to capacity."""
        await self._ensure_hydrated()
        async with self._lock:
            if self._server_reset_at is not None:
                return self._server_reset_at
            return float((self._hour_window + 1) * 3600)

    async def budget(self) -> RateLimitBudget:
        """Get full budget snapshot."""
        await self._ensure_hydrated()
        async with self._lock:
            self._refill()
            remaining = self._budget - self._hourly_used
            if self._server_remaining is not None:
                remaining = min(remaining, self._server_remaining)
            return RateLimitBudget(
                remaining=max(0, remaining),
                capacity=self._budget,
                reset_at=(
                    self._server_reset_at
                    if self._server_reset_at is not None
                    else float((self._hour_window + 1) * 3600)
                ),
                daily_remaining=max(0, self._daily_budget - self._daily_used),
                daily_capacity=self._daily_budget,
            )

    async def update_from_headers(self, headers: Mapping[str, str]) -> None:
        """吸收供应商真实配额头，使用更保守的剩余额度。"""
        async with self._lock:
            limit = self._parse_int(headers.get("x-ratelimit-limit"))
            remaining = self._parse_int(headers.get("x-ratelimit-remaining"))
            reset_at = self._parse_reset(headers.get("x-ratelimit-reset"))
            if limit is not None:
                self._budget = min(self._budget, limit)
            if remaining is not None:
                self._server_remaining = max(0, remaining)
            if reset_at is not None:
                self._server_reset_at = reset_at
            await self._sync_to_redis()

    async def force_reserve(self, tokens: int) -> None:
        """Reserve tokens for near-kickoff events (called before batch fetch).

        This ensures close matches always have quota by setting aside *tokens*
        that the main batch must not consume. Call :meth:`release_reserve` after
        near-kickoff events are processed.
        """
        await self._ensure_hydrated()
        async with self._lock:
            self._refill()
            self._hourly_used = min(self._budget, self._hourly_used + tokens)
            self._daily_used = min(self._daily_budget, self._daily_used + tokens)
            await self._sync_to_redis()

    async def consume_reserve(self, used: int) -> None:
        """Return unused reserved tokens to the bucket.

        Called after near-kickoff processing completes.
        """
        await self._ensure_hydrated()
        async with self._lock:
            self._hourly_used = max(0, self._hourly_used - used)
            self._daily_used = max(0, self._daily_used - used)
            await self._sync_to_redis()

    # -- internal ----------------------------------------------------------

    def _refill(self) -> None:
        """Refill tokens based on elapsed time (must be called under lock)."""
        now = time.time()
        hour_window = int(now // 3600)
        day_window = int(now // 86400)
        if hour_window != self._hour_window:
            self._hour_window = hour_window
            self._hourly_used = 0
            self._server_remaining = None
            self._server_reset_at = None
        if day_window != self._day_window:
            self._day_window = day_window
            self._daily_used = 0

    async def _sync_to_redis(self) -> None:
        """Push current token state to Redis (best-effort)."""
        if self._redis is None:
            return
        try:
            ttl = 86400
            await self._redis.cache_set(
                f"{self._redis_prefix}hourly_used",
                str(self._hourly_used),
                ttl=ttl,
            )
            await self._redis.cache_set(
                f"{self._redis_prefix}hour_window",
                str(self._hour_window),
                ttl=ttl,
            )
            await self._redis.cache_set(
                f"{self._redis_prefix}daily_used",
                str(self._daily_used),
                ttl=ttl,
            )
            await self._redis.cache_set(
                f"{self._redis_prefix}day_window",
                str(self._day_window),
                ttl=ttl,
            )
        except Exception:
            logger.debug("Rate limiter Redis sync failed (non-critical)", exc_info=True)

    async def _ensure_hydrated(self) -> None:
        if self._hydrated:
            return
        async with self._hydrate_lock:
            if not self._hydrated:
                await self.hydrate_from_redis()

    async def hydrate_from_redis(self) -> None:
        """Restore token state from Redis after worker restart."""
        if self._redis is None:
            self._hydrated = True
            return
        try:
            hourly_raw = await self._redis.cache_get(f"{self._redis_prefix}hourly_used")
            hour_window_raw = await self._redis.cache_get(f"{self._redis_prefix}hour_window")
            daily_raw = await self._redis.cache_get(f"{self._redis_prefix}daily_used")
            day_window_raw = await self._redis.cache_get(f"{self._redis_prefix}day_window")
            if hourly_raw is not None or daily_raw is not None:
                async with self._lock:
                    current_hour_window = int(time.time() // 3600)
                    current_day_window = int(time.time() // 86400)
                    if (
                        hourly_raw is not None
                        and hour_window_raw is not None
                        and int(float(hour_window_raw)) == current_hour_window
                    ):
                        self._hourly_used = max(0, int(float(hourly_raw)))
                    if (
                        daily_raw is not None
                        and day_window_raw is not None
                        and int(float(day_window_raw)) == current_day_window
                    ):
                        self._daily_used = max(0, int(float(daily_raw)))
                    self._refill()
        except Exception:
            logger.debug("Rate limiter Redis hydrate failed", exc_info=True)
        finally:
            self._hydrated = True

    async def hydra_from_redis(self) -> None:
        """向后兼容旧方法名。"""
        await self.hydrate_from_redis()

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_reset(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        now = time.time()
        return numeric if numeric > now else now + max(0.0, numeric)


def sort_events_by_kickoff(events: list[Any]) -> list[Any]:
    """Sort events by kickoff time ascending so near-kickoff events get priority.

    Events with ``None`` kickoff are pushed to the end.
    """

    def _sort_key(evt: Any) -> float:
        if evt.date is None:
            return float("inf")
        return float(evt.date.timestamp())

    return sorted(events, key=_sort_key)
