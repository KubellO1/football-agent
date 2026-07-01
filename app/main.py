"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config.settings import get_settings
from app.core.container import container
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize DI container resources on startup, dispose on shutdown."""
    settings = get_settings()
    logger.info("Starting %s (env=%s)", settings.app_name, settings.environment)
    container.init_resources()
    try:
        yield
    finally:
        logger.info("Shutting down; disposing container resources")
        await container.shutdown_resources()


def create_app() -> FastAPI:
    """Application factory — builds and configures the FastAPI instance."""
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
