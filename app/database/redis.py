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

    async def cache_get(self, key: str) -> str | None:
        """Retrieve a cached value by key. Returns ``None`` on cache miss."""
        client = self.client()
        try:
            value = await client.get(key)
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return value
        finally:
            await client.aclose()

    async def cache_set(self, key: str, value: str, ttl: int = 300) -> None:
        """Set a cached value with TTL in seconds (default 5 minutes).

        ``value`` is stored as-is (caller is responsible for serialisation).
        """
        client = self.client()
        try:
            await client.set(key, value, ex=ttl)
        finally:
            await client.aclose()

    async def cache_delete(self, key: str) -> None:
        """Delete a cached key. No-op if the key does not exist."""
        client = self.client()
        try:
            await client.delete(key)
        finally:
            await client.aclose()
