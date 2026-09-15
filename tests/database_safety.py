"""集成测试数据库的安全校验。"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncConnection


PROTECTED_DATABASE_NAMES = frozenset({"football", "postgres", "template0", "template1"})


def _require_unmistakable_test_database_name(database: str | None) -> str:
    normalized = (database or "").strip().lower()
    if normalized in PROTECTED_DATABASE_NAMES:
        raise RuntimeError(
            f"refusing destructive test operation on protected database {normalized!r}"
        )
    if not normalized.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must target a database whose name ends with '_test'")
    return normalized


def require_test_database_url(environ: Mapping[str, str]) -> str:
    """返回经过校验的测试 DSN；危险配置直接失败，不回退到应用数据库。"""
    dsn = environ.get("TEST_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "TEST_DATABASE_URL is required for integration tests; "
            "refusing to fall back to the application database"
        )

    try:
        database = make_url(dsn).database
    except ArgumentError as exc:
        raise RuntimeError("TEST_DATABASE_URL is not a valid SQLAlchemy URL") from exc

    _require_unmistakable_test_database_name(database)
    return dsn


async def run_guarded_metadata_operation(
    connection: AsyncConnection,
    *,
    dsn: str,
    operation: Callable[..., None],
) -> None:
    """Run metadata DDL only after verifying the connected database is the requested test DB."""
    expected_database = _require_unmistakable_test_database_name(make_url(dsn).database)
    actual_database = await connection.scalar(text("SELECT current_database()"))
    actual_database = _require_unmistakable_test_database_name(actual_database)
    if actual_database != expected_database:
        raise RuntimeError(
            "connected database does not match TEST_DATABASE_URL: "
            f"expected {expected_database!r}, got {actual_database!r}"
        )
    await connection.run_sync(operation)


async def verify_test_database_connection(environ: Mapping[str, str]) -> None:
    """Verify the explicit test DSN and the identity of the database it reaches."""
    dsn = require_test_database_url(environ)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            expected_database = _require_unmistakable_test_database_name(make_url(dsn).database)
            actual_database = await connection.scalar(text("SELECT current_database()"))
            actual_database = _require_unmistakable_test_database_name(actual_database)
            if actual_database != expected_database:
                raise RuntimeError(
                    "connected database does not match TEST_DATABASE_URL: "
                    f"expected {expected_database!r}, got {actual_database!r}"
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_test_database_connection(os.environ))
