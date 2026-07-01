"""Skeleton smoke tests — verify the app wires together, no business logic."""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.main import create_app


@pytest.mark.unit
def test_app_factory_builds() -> None:
    app = create_app()
    assert app.title


@pytest.mark.unit
def test_settings_dsns() -> None:
    settings = Settings()
    assert settings.sqlalchemy_dsn.startswith("postgresql+asyncpg://")
    assert settings.redis_dsn.startswith("redis://")
