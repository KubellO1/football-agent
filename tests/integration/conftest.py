"""集成测试的共享 fixture。

提供一个针对真实 Postgres 的 async session：建表 → 交出 session → 删表，
保证测试间隔离。需要可用的数据库（Docker/CI），本地无 DB 时这些测试会被跳过
或失败——因此统一标注 @pytest.mark.integration。

DSN 优先取环境变量 TEST_DATABASE_URL，回退到应用 settings；请指向**测试库**，
不要指向生产库（本 fixture 会 drop_all）。

由于 0003 起 fixtures 有指向 competitions/teams 的外键，写入比赛前需先写入
参考数据，故提供 reference_ids / persisted_fixture 两个 helper fixture。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.database.base import Base
from app.models.entities.fixture import Fixture
from app.repositories.sqlalchemy import models  # noqa: F401  导入以注册 ORM 表到 metadata
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import CompetitionORM, TeamORM


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().sqlalchemy_dsn


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
            kickoff=datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc),
        )
    )
