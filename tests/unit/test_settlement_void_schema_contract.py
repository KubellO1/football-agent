"""Schema and migration-graph contracts for terminal settlement VOID support."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.repositories.sqlalchemy.models import SettlementORM

if TYPE_CHECKING:
    from types import ModuleType


def _script_directory() -> ScriptDirectory:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "alembic"))
    return ScriptDirectory.from_config(config)


def _migration_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "settlement_void_0022.py"
    spec = importlib.util.spec_from_file_location("settlement_void_0022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bind_returning_scalar(value: int) -> Mock:
    result = Mock()
    result.scalar_one.return_value = value
    bind = Mock()
    bind.execute.return_value = result
    return bind


@pytest.mark.unit
def test_settlement_void_metadata_is_minimal_and_constrained() -> None:
    table = SettlementORM.__table__

    assert table.c.score_home.nullable is True
    assert table.c.score_away.nullable is True
    assert table.c.void_reason_code.nullable is True
    assert table.c.void_reason_code.type.length == 64

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names == {
        "ck_settlements_result_terminal",
        "ck_settlements_void_shape",
    }
    unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints["uq_settlements_value_bet"] == ["value_bet_id"]


@pytest.mark.unit
def test_active_void_revision_is_linear_and_historical_0022_is_unresolvable() -> None:
    script = _script_directory()

    assert script.get_heads() == ["settlement_void_0022"]
    assert script.get_revision("settlement_void_0022").down_revision == "0021"
    with pytest.raises(CommandError, match="Can't locate revision identified by '0022'"):
        script.get_revision("0022")


@pytest.mark.unit
def test_upgrade_preflight_refuses_incompatible_rows_before_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration_module()
    add_column = Mock()
    monkeypatch.setattr(migration.op, "get_bind", Mock(return_value=_bind_returning_scalar(1)))
    monkeypatch.setattr(migration.op, "add_column", add_column)

    with pytest.raises(RuntimeError, match="existing settlement rows"):
        migration.upgrade()

    add_column.assert_not_called()


@pytest.mark.unit
def test_downgrade_refuses_void_rows_before_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration_module()
    drop_constraint = Mock()
    monkeypatch.setattr(migration.op, "get_bind", Mock(return_value=_bind_returning_scalar(1)))
    monkeypatch.setattr(migration.op, "drop_constraint", drop_constraint)

    with pytest.raises(RuntimeError, match="terminal VOID rows exist"):
        migration.downgrade()

    drop_constraint.assert_not_called()
