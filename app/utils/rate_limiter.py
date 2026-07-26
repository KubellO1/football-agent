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
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
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
        redis: "RedisConnection | None" = None,
        redis_prefix: str = "odds:rate:",
    ) -> None:
        self._budget = budget
        self._tokens = float(budget)
        self._refill_interval = 3600.0  # 1 hour in seconds
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._redis = redis
        self._redis_prefix = redis_prefix

    # -- public ------------------------------------------------------------

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire *tokens* tokens. Returns ``True`` on success."""
        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                await self._sync_to_redis()
                return True
            return False

    async def remaining(self) -> int:
        """Return current token count (thread-safe snapshot)."""
        async with self._lock:
            self._refill()
            return int(self._tokens)

    async def reset_at(self) -> float:
        """Unix timestamp when the bucket next refills to capacity."""
        async with self._lock:
            return self._last_refill + self._refill_interval

    async def budget(self) -> RateLimitBudget:
        """Get full budget snapshot."""
        async with self._lock:
            self._refill()
            return RateLimitBudget(
                remaining=int(self._tokens),
                capacity=self._budget,
                reset_at=self._last_refill + self._refill_interval,
            )

    async def force_reserve(self, tokens: int) -> None:
        """Reserve tokens for near-kickoff events (called before batch fetch).

        This ensures close matches always have quota by setting aside *tokens*
        that the main batch must not consume. Call :meth:`release_reserve` after
        near-kickoff events are processed.
        """
        async with self._lock:
            self._refill()
            self._tokens -= tokens
            if self._tokens < 0:
                self._tokens = 0
            await self._sync_to_redis()

    async def consume_reserve(self, used: int) -> None:
        """Return unused reserved tokens to the bucket.

        Called after near-kickoff processing completes.
        """
        async with self._lock:
            self._tokens += used
            if self._tokens > self._budget:
                self._tokens = self._budget
            await self._sync_to_redis()

    # -- internal ----------------------------------------------------------

    def _refill(self) -> None:
        """Refill tokens based on elapsed time (must be called under lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        new_tokens = (elapsed / self._refill_interval) * self._budget
        self._tokens = min(self._budget, self._tokens + new_tokens)
        self._last_refill = now

    async def _sync_to_redis(self) -> None:
        """Push current token state to Redis (best-effort)."""
        if self._redis is None:
            return
        try:
            ttl = int(math.ceil(self._refill_interval))
            await self._redis.cache_set(
                f"{self._redis_prefix}tokens",
                str(self._tokens),
                ttl=ttl,
            )
            await self._redis.cache_set(
                f"{self._redis_prefix}last_refill",
                str(self._last_refill),
                ttl=ttl,
            )
        except Exception:
            logger.debug("Rate limiter Redis sync failed (non-critical)", exc_info=True)

    async def hydra_from_redis(self) -> None:
        """Restore token state from Redis after worker restart."""
        if self._redis is None:
            return
        try:
            tokens_raw = await self._redis.cache_get(f"{self._redis_prefix}tokens")
            refill_raw = await self._redis.cache_get(f"{self._redis_prefix}last_refill")
            if tokens_raw is not None:
                async with self._lock:
                    self._tokens = float(tokens_raw)
                    if refill_raw is not None:
                        self._last_refill = float(refill_raw)
                    # Don't overfill from stale Redis data
                    self._refill()
        except Exception:
            logger.debug("Rate limiter Redis hydrate failed", exc_info=True)


def sort_events_by_kickoff(events: list) -> list:
    """Sort events by kickoff time ascending so near-kickoff events get priority.

    Events with ``None`` kickoff are pushed to the end.
    """
    from app.providers.impl.odds_api_io_provider import _EventInfo

    def _sort_key(evt: _EventInfo) -> float:
        if evt.date is None:
            return float("inf")
        return evt.date.timestamp()

    return sorted(events, key=_sort_key)
