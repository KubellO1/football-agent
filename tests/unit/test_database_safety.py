"""测试数据库保护规则单测。"""

from __future__ import annotations

import pytest

from tests.database_safety import require_test_database_url


def test_requires_explicit_test_database_url() -> None:
    with pytest.raises(RuntimeError, match="is required"):
        require_test_database_url({})


def test_rejects_application_database_name() -> None:
    environ = {"TEST_DATABASE_URL": "postgresql+asyncpg://football:secret@postgres:5432/football"}

    with pytest.raises(RuntimeError, match="ends with '_test'"):
        require_test_database_url(environ)


def test_rejects_malformed_database_url() -> None:
    with pytest.raises(RuntimeError, match="valid SQLAlchemy URL"):
        require_test_database_url({"TEST_DATABASE_URL": "not a database url"})


def test_accepts_isolated_test_database() -> None:
    dsn = "postgresql+asyncpg://football:secret@postgres:5432/football_test"

    assert require_test_database_url({"TEST_DATABASE_URL": dsn}) == dsn
