"""Health / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import RedisDep, SessionDep  # noqa: TC001 - FastAPI 会在运行时解析依赖注解

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep, cache: RedisDep) -> dict[str, str]:
    """Readiness probe — verifies Postgres and Redis connectivity."""
    await session.execute(text("SELECT 1"))
    await cache.ping()
    return {"status": "ready", "database": "ok", "redis": "ok"}
