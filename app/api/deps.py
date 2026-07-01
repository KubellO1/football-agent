"""Per-request FastAPI dependencies.

These pull infrastructure from the DI container and expose it as request-scoped
dependencies. Endpoints depend on the typed aliases (``SessionDep``,
``RedisDep``) rather than importing concrete connections directly.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import container


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional DB session from the container's Database."""
    async with container.database.session() as session:
        yield session


async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """Yield a Redis client from the container's connection pool."""
    client = container.redis.client()
    try:
        yield client
    finally:
        await client.aclose()


# Typed dependency aliases for endpoint signatures.
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]

__all__ = ["get_db_session", "get_redis", "SessionDep", "RedisDep"]
