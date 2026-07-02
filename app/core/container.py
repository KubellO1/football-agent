"""Dependency-injection composition root.

The container is the single place where infrastructure resources are constructed
from configuration and their lifecycle is managed. Application code (endpoints,
services) receives these dependencies rather than importing concrete instances,
which keeps wiring out of business logic and makes implementations swappable and
testable.

Domain bindings (repositories, providers, services) are registered here as they
are implemented; for now the container owns the Postgres and Redis connections.
"""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.database.redis import RedisConnection
from app.database.session import Database

logger = get_logger(__name__)


class Container:
    """Composition root: owns infrastructure singletons and their lifecycle."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings: Settings = settings or get_settings()
        self._database: Database | None = None
        self._redis: RedisConnection | None = None
        self._bindings: dict[type, Any] = {}

    # --- lifecycle ---------------------------------------------------------
    def init_resources(self) -> None:
        """Construct infrastructure resources from settings (call on startup)."""
        if self._database is None:
            self._database = Database(
                self._settings.sqlalchemy_dsn,
                echo=self._settings.debug,
            )
        if self._redis is None:
            self._redis = RedisConnection(self._settings.redis_dsn)

        # AI reasoning engine. Lazy import keeps the core container decoupled
        # from the Anthropic SDK; bound to its interface for injection.
        from app.agents import ReasoningEngine, build_reasoning_agent

        self.register(ReasoningEngine, build_reasoning_agent(self._settings))

        # 无状态的分析组件注册为单例（可在测试中替换）。惰性导入避免顶层耦合。
        from app.services.daily_selection import DailySelectionService
        from app.services.modeling import MatchModel
        from app.services.models.ensemble import EnsembleMatchModel
        from app.services.recommendation_gate import RecommendationGate

        self.register(MatchModel, EnsembleMatchModel())
        self.register(RecommendationGate, RecommendationGate())
        self.register(DailySelectionService, DailySelectionService())

        logger.info("Container resources initialized")

    async def shutdown_resources(self) -> None:
        """Dispose infrastructure resources (call on shutdown)."""
        if self._database is not None:
            await self._database.dispose()
            self._database = None
        if self._redis is not None:
            await self._redis.dispose()
            self._redis = None
        logger.info("Container resources disposed")

    # --- accessors ---------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def database(self) -> Database:
        if self._database is None:
            raise RuntimeError("Container not initialized: call init_resources() first")
        return self._database

    @property
    def redis(self) -> RedisConnection:
        if self._redis is None:
            raise RuntimeError("Container not initialized: call init_resources() first")
        return self._redis

    # --- generic interface bindings (for repos/providers/services later) ---
    def register(self, interface: type, instance: Any) -> None:
        self._bindings[interface] = instance

    def resolve(self, interface: type) -> Any:
        if interface not in self._bindings:
            raise KeyError(f"No binding registered for {interface!r}")
        return self._bindings[interface]


# Application-wide container. Resources are initialized during app lifespan.
container = Container()
