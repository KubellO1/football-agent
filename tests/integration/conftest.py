"""集成测试的共享 fixture。

提供一个针对真实 Postgres 的 async session：建表 → 交出 session → 删表，
保证测试间隔离。需要可用的数据库（Docker/CI），本地无 DB 时这些测试会被跳过
或失败——因此统一标注 @pytest.mark.integration。

DSN 优先取环境变量 TEST_DATABASE_URL，回退到应用 settings；请指向**测试库**，
不要指向生产库（本 fixture 会 drop_all）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.database.base import Base
from app.repositories.sqlalchemy import models  # noqa: F401  导入以注册 ORM 表到 metadata


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
