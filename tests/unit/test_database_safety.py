"""测试数据库保护规则单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.database_safety import require_test_database_url, run_guarded_metadata_operation


class FakeConnection:
    def __init__(self, actual_database: str) -> None:
        self.actual_database = actual_database
        self.queries: list[str] = []
        self.operations: list[object] = []

    async def scalar(self, query: object) -> str:
        self.queries.append(str(query))
        return self.actual_database

    async def run_sync(self, operation: object) -> None:
        self.operations.append(operation)


def test_requires_explicit_test_database_url() -> None:
    with pytest.raises(RuntimeError, match="is required"):
        require_test_database_url({})


def test_rejects_application_database_name() -> None:
    environ = {"TEST_DATABASE_URL": "postgresql+asyncpg://football:secret@postgres:5432/football"}

    with pytest.raises(RuntimeError, match="protected database"):
        require_test_database_url(environ)


def test_does_not_fall_back_to_production_database_url() -> None:
    environ = {"DATABASE_URL": "postgresql+asyncpg://football:secret@postgres:5432/football"}

    with pytest.raises(RuntimeError, match="is required"):
        require_test_database_url(environ)


def test_rejects_malformed_database_url() -> None:
    with pytest.raises(RuntimeError, match="valid SQLAlchemy URL"):
        require_test_database_url({"TEST_DATABASE_URL": "not a database url"})


def test_accepts_isolated_test_database() -> None:
    dsn = "postgresql+asyncpg://football:secret@postgres:5432/football_test"

    assert require_test_database_url({"TEST_DATABASE_URL": dsn}) == dsn


@pytest.mark.asyncio
async def test_missing_test_url_fails_before_metadata_mutation() -> None:
    connection = FakeConnection("football_test")

    with pytest.raises(RuntimeError, match="is required"):
        dsn = require_test_database_url({})
        await run_guarded_metadata_operation(connection, dsn=dsn, operation=object())

    assert connection.queries == []
    assert connection.operations == []


@pytest.mark.asyncio
async def test_production_test_url_fails_before_metadata_mutation() -> None:
    connection = FakeConnection("football")

    with pytest.raises(RuntimeError, match="protected database"):
        dsn = require_test_database_url(
            {"TEST_DATABASE_URL": "postgresql+asyncpg://football:secret@postgres:5432/football"}
        )
        await run_guarded_metadata_operation(connection, dsn=dsn, operation=object())

    assert connection.queries == []
    assert connection.operations == []


@pytest.mark.asyncio
async def test_connected_production_database_fails_before_metadata_mutation() -> None:
    connection = FakeConnection("football")
    dsn = "postgresql+asyncpg://football:secret@postgres:5432/football_test"

    with pytest.raises(RuntimeError, match="protected database"):
        await run_guarded_metadata_operation(connection, dsn=dsn, operation=object())

    assert connection.queries == ["SELECT current_database()"]
    assert connection.operations == []


@pytest.mark.asyncio
async def test_connected_database_must_match_explicit_test_url() -> None:
    connection = FakeConnection("other_test")
    dsn = "postgresql+asyncpg://football:secret@postgres:5432/football_test"

    with pytest.raises(RuntimeError, match="does not match"):
        await run_guarded_metadata_operation(connection, dsn=dsn, operation=object())

    assert connection.operations == []


@pytest.mark.asyncio
async def test_guard_allows_metadata_mutation_on_matching_test_database() -> None:
    connection = FakeConnection("football_integration_test")
    operation = object()
    dsn = "postgresql+asyncpg://football:secret@postgres:5432/football_integration_test"

    await run_guarded_metadata_operation(connection, dsn=dsn, operation=operation)

    assert connection.operations == [operation]


def test_integration_suite_has_no_application_database_fallback() -> None:
    integration_root = Path(__file__).parents[1] / "integration"
    offenders = [
        path.relative_to(integration_root).as_posix()
        for path in integration_root.rglob("*.py")
        if "get_settings().sqlalchemy_dsn" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_all_integration_metadata_ddl_uses_identity_guard() -> None:
    integration_root = Path(__file__).parents[1] / "integration"
    offenders: list[str] = []
    for path in integration_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "run_sync(Base.metadata.create_all)" in source:
            offenders.append(f"{path.name}:create_all")
        if "run_sync(Base.metadata.drop_all)" in source:
            offenders.append(f"{path.name}:drop_all")

    assert offenders == []


def test_ci_verifies_connected_test_database_before_alembic_upgrade() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    guard_position = workflow.index("python -m tests.database_safety")
    migration_position = workflow.index("alembic upgrade head")

    assert guard_position < migration_position
