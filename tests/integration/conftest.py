"""集成测试的共享 fixture。

提供一个针对真实 Postgres 的 async session：建表 → 交出 session → 删表，
保证测试间隔离。需要可用的数据库（Docker/CI），本地无 DB 时这些测试会被跳过
或失败——因此统一标注 @pytest.mark.integration。

DSN 必须由环境变量 TEST_DATABASE_URL 显式提供，且数据库名必须以 ``_test``
结尾。缺失或不安全时立即失败，绝不回退到应用数据库。

由于 0003 起 fixtures 有指向 competitions/teams 的外键，写入比赛前需先写入
参考数据，故提供 reference_ids / persisted_fixture 两个 helper fixture。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.entities.enums import PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.player import Player
from app.repositories.sqlalchemy import models  # noqa: F401  导入以注册 ORM 表到 metadata
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import CompetitionORM, TeamORM
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from tests.database_safety import require_test_database_url

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID


def _test_dsn() -> str:
    return require_test_database_url(os.environ)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """建表、交出 session、删表（函数级隔离）。"""
    engine = create_async_engine(_test_dsn())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def reference_ids(db_session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """预先写入满足外键的赛事与两支球队，返回 (competition_id, home_team_id, away_team_id)。"""
    comp_id, home_id, away_id = uuid4(), uuid4(), uuid4()
    db_session.add_all(
        [
            CompetitionORM(id=comp_id, name="测试联赛", country="测试国"),
            TeamORM(id=home_id, name="主队"),
            TeamORM(id=away_id, name="客队"),
        ]
    )
    await db_session.flush()
    return comp_id, home_id, away_id


@pytest_asyncio.fixture
async def persisted_fixture(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> Fixture:
    """写入一场比赛（满足外键），返回领域实体。"""
    comp_id, home_id, away_id = reference_ids
    repo = SqlAlchemyFixtureRepository(db_session)
    return await repo.add(
        Fixture(
            competition_id=comp_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff=datetime(2026, 7, 3, 18, 0, tzinfo=UTC),
        )
    )


@pytest_asyncio.fixture
async def persisted_player(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> Player:
    """写入一名带稳定外部身份的主队球员。"""
    return await SqlAlchemyPlayerRepository(db_session).add(
        Player(
            name="测试球员",
            position=PlayerPosition.FORWARD,
            team_id=reference_ids[1],
            external_source="test-provider",
            external_id="player-1",
        )
    )
