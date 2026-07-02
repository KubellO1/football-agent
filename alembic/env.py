"""Alembic migration environment (async).

Pulls the DSN from application settings and targets ``Base.metadata`` so
``--autogenerate`` sees all ORM models. ORM models must be imported before
autogenerate runs (added as they are implemented).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from alembic import context

from app.config.settings import get_settings
from app.database.base import Base

# ORM 模型模块必须在此导入，其表才会注册到 Base.metadata 供 autogenerate 使用。
# 新增聚合的 ORM 模型后，在此追加导入即可。
from app.repositories.sqlalchemy import models  # noqa: E402, F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the async DSN from settings (no secrets in alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
