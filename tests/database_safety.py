"""集成测试数据库的安全校验。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

if TYPE_CHECKING:
    from collections.abc import Mapping


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

    if database is None or not database.lower().endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must target a database whose name ends with '_test'")
    return dsn
