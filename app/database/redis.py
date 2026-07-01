"""Async Redis infrastructure.

``RedisConnection`` owns a connection pool for a single DSN and hands out
clients bound to it. Managed by the DI container / app lifespan.
"""

from __future__ import annotations

import redis.asyncio as redis


class RedisConnection:
    """Owns a Redis connection pool and produces clients bound to it."""

    def __init__(self, dsn: str) -> None:
        self._pool: redis.ConnectionPool = redis.ConnectionPool.from_url(
            dsn,
            decode_responses=True,
        )

    def client(self) -> redis.Redis:
        """Return a client bound to the shared pool."""
        return redis.Redis(connection_pool=self._pool)

    async def check(self) -> bool:
        """Connectivity probe used by readiness checks."""
        client = self.client()
        try:
            await client.ping()
            return True
        finally:
            await client.aclose()

    async def dispose(self) -> None:
        """Disconnect the pool (called on shutdown)."""
        await self._pool.disconnect()
