"""Aggregate router for API v1.

Individual endpoint routers are included here. As features land (fixtures,
odds, recommendations), their routers are added to this file only.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import analysis, fixtures, health, review, sync

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(analysis.router)
api_router.include_router(sync.router)
api_router.include_router(fixtures.router)
api_router.include_router(review.router)
