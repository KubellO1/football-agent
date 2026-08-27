from __future__ import annotations

from copy import deepcopy

import pytest
from scripts.alembic_ledger_reconciliation import (
    FK_SPECS,
    ReconciliationError,
    evaluate_foreign_keys,
    normalize_constraint_sql,
    normalize_dsn,
    normalize_sql,
    schema_diff,
)


def _row_for(index: int, *, canonical_name: bool = False) -> dict[str, object]:
    spec = FK_SPECS[index]
    return {
        "table_name": spec.table,
        "constraint_name": spec.expected_name if canonical_name else spec.actual_name,
        "local_columns": list(spec.local_columns),
        "referenced_table": spec.referenced_table,
        "referenced_columns": list(spec.referenced_columns),
        "on_update": spec.on_update,
        "on_delete": spec.on_delete,
        "deferrable": spec.deferrable,
        "initially_deferred": spec.initially_deferred,
        "definition": "test definition",
    }


def test_inventory_defines_all_22_known_fk_renames() -> None:
    assert len(FK_SPECS) == 22
    assert len({(spec.table, spec.actual_name) for spec in FK_SPECS}) == 22
    assert len({(spec.table, spec.expected_name) for spec in FK_SPECS}) == 22


def test_only_lineup_parent_fk_uses_cascade_delete() -> None:
    cascading = [spec.expected_name for spec in FK_SPECS if spec.on_delete == "CASCADE"]
    assert cascading == ["fk_lineup_players_lineup"]


def test_legacy_fk_names_with_exact_semantics_are_renameable() -> None:
    results = evaluate_foreign_keys([_row_for(index) for index in range(len(FK_SPECS))])

    assert all(result.semantics_match for result in results)
    assert all(result.requires_rename for result in results)


def test_canonical_fk_names_are_idempotent_noops() -> None:
    results = evaluate_foreign_keys(
        [_row_for(index, canonical_name=True) for index in range(len(FK_SPECS))]
    )

    assert all(result.semantics_match for result in results)
    assert not any(result.requires_rename for result in results)


def test_semantic_difference_blocks_fk_rename() -> None:
    rows = [_row_for(index) for index in range(len(FK_SPECS))]
    rows[0]["on_delete"] = "CASCADE"

    results = evaluate_foreign_keys(rows)

    assert not results[0].semantics_match
    assert results[0].details == "foreign-key semantics differ"


def test_missing_fk_is_reported_as_mismatch() -> None:
    rows = [_row_for(index) for index in range(1, len(FK_SPECS))]

    results = evaluate_foreign_keys(rows)

    assert not results[0].semantics_match
    assert results[0].details == "constraint is missing"


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://football@postgres:5432/football",
        "postgresql+asyncpg://football@127.0.0.1:5432/football",
        "sqlite:///football_reconcile_clone",
    ],
)
def test_mutating_dsn_guard_rejects_production_or_non_postgres(dsn: str) -> None:
    with pytest.raises(ReconciliationError):
        normalize_dsn(dsn, require_clone=True)


def test_mutating_dsn_guard_accepts_loopback_disposable_clone() -> None:
    normalized, host, database = normalize_dsn(
        "postgresql+asyncpg://football@127.0.0.1:55432/football_reconcile_r1",
        require_clone=True,
    )

    assert normalized.startswith("postgresql://")
    assert host == "127.0.0.1"
    assert database == "football_reconcile_r1"


def test_sql_normalization_is_whitespace_and_case_stable() -> None:
    assert normalize_sql("  FOREIGN   KEY (fixture_id)\nREFERENCES fixtures(id) ") == (
        "foreign key (fixture_id) references fixtures(id)"
    )


def test_constraint_normalization_accepts_equivalent_varchar_array_casts() -> None:
    metadata_form = (
        "CHECK (status::text = ANY "
        "(ARRAY['predicted'::character varying::text, "
        "'confirmed'::character varying::text]))"
    )
    migration_form = (
        "CHECK (status::text = ANY "
        "(ARRAY['predicted'::character varying, "
        "'confirmed'::character varying]::text[]))"
    )

    assert normalize_constraint_sql(metadata_form) == normalize_constraint_sql(migration_form)


def test_schema_diff_reports_missing_unexpected_and_divergent() -> None:
    expected = {
        "tables": {"a": {"kind": "r"}, "b": {"kind": "r"}},
        "columns": {"a.id": {"type": "uuid"}},
    }
    actual = deepcopy(expected)
    del actual["tables"]["b"]
    actual["tables"]["c"] = {"kind": "r"}
    actual["columns"]["a.id"] = {"type": "text"}

    result = schema_diff(actual, expected)

    assert result["missing"] == 1
    assert result["unexpected"] == 1
    assert result["divergent"] == 1
    assert result["exact"] is False


def test_schema_diff_exact_is_deterministic() -> None:
    inventory = {"tables": {"a": {"kind": "r"}}, "columns": {}}

    first = schema_diff(deepcopy(inventory), deepcopy(inventory))
    second = schema_diff(deepcopy(inventory), deepcopy(inventory))

    assert first == second
    assert first["exact"] is True
