"""Clone-only Alembic ledger reconciliation and strict schema comparison.

This utility is intentionally disconnected from application startup. Mutating
commands reject non-loopback hosts and database names outside the disposable
``football_reconcile_`` namespace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

import asyncpg

CLONE_DATABASE_PREFIX = "football_reconcile_"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
JSON_DEFAULT_COLUMNS = (
    "supporting_evidence",
    "risks",
    "rejected_alternatives",
    "change_conditions",
)


class ReconciliationError(RuntimeError):
    """Raised when a clone fails a safety or semantic precondition."""


@dataclass(frozen=True)
class ForeignKeySpec:
    table: str
    actual_name: str
    expected_name: str
    local_columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: str = "NO ACTION"
    on_delete: str = "NO ACTION"
    deferrable: bool = False
    initially_deferred: bool = False


@dataclass(frozen=True)
class ForeignKeyAuditResult:
    table: str
    actual_name: str
    expected_name: str
    local_columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: str
    on_delete: str
    deferrable: bool
    initially_deferred: bool
    active_name: str | None
    semantics_match: bool
    requires_rename: bool
    details: str


FK_SPECS: tuple[ForeignKeySpec, ...] = (
    ForeignKeySpec(
        "seasons",
        "seasons_competition_id_fkey",
        "fk_seasons_competition",
        ("competition_id",),
        "competitions",
        ("id",),
    ),
    ForeignKeySpec(
        "fixtures",
        "fixtures_competition_id_fkey",
        "fk_fixtures_competition",
        ("competition_id",),
        "competitions",
        ("id",),
    ),
    ForeignKeySpec(
        "fixtures",
        "fixtures_season_id_fkey",
        "fk_fixtures_season",
        ("season_id",),
        "seasons",
        ("id",),
    ),
    ForeignKeySpec(
        "fixtures",
        "fixtures_home_team_id_fkey",
        "fk_fixtures_home_team",
        ("home_team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "fixtures",
        "fixtures_away_team_id_fkey",
        "fk_fixtures_away_team",
        ("away_team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "predictions",
        "predictions_fixture_id_fkey",
        "fk_predictions_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "value_bets",
        "value_bets_fixture_id_fkey",
        "fk_value_bets_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "value_bets",
        "value_bets_bookmaker_id_fkey",
        "fk_value_bets_bookmaker",
        ("bookmaker_id",),
        "bookmakers",
        ("id",),
    ),
    ForeignKeySpec(
        "decision_logs",
        "decision_logs_fixture_id_fkey",
        "fk_decision_logs_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "decision_logs",
        "decision_logs_value_bet_id_fkey",
        "fk_decision_logs_value_bet",
        ("value_bet_id",),
        "value_bets",
        ("id",),
    ),
    ForeignKeySpec(
        "odds_snapshots",
        "odds_snapshots_fixture_id_fkey",
        "fk_odds_snapshots_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "odds_snapshots",
        "odds_snapshots_bookmaker_id_fkey",
        "fk_odds_snapshots_bookmaker",
        ("bookmaker_id",),
        "bookmakers",
        ("id",),
    ),
    ForeignKeySpec(
        "team_match_statistics",
        "team_match_statistics_fixture_id_fkey",
        "fk_team_match_statistics_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "team_match_statistics",
        "team_match_statistics_team_id_fkey",
        "fk_team_match_statistics_team",
        ("team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "player_availability_observations",
        "player_availability_observations_fixture_id_fkey",
        "fk_player_availability_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "player_availability_observations",
        "player_availability_observations_team_id_fkey",
        "fk_player_availability_team",
        ("team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "players",
        "players_team_id_fkey",
        "fk_players_team",
        ("team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "player_availability_observations",
        "player_availability_observations_player_id_fkey",
        "fk_player_availability_player",
        ("player_id",),
        "players",
        ("id",),
    ),
    ForeignKeySpec(
        "lineups",
        "lineups_fixture_id_fkey",
        "fk_lineups_fixture",
        ("fixture_id",),
        "fixtures",
        ("id",),
    ),
    ForeignKeySpec(
        "lineups",
        "lineups_team_id_fkey",
        "fk_lineups_team",
        ("team_id",),
        "teams",
        ("id",),
    ),
    ForeignKeySpec(
        "lineup_players",
        "lineup_players_lineup_id_fkey",
        "fk_lineup_players_lineup",
        ("lineup_id",),
        "lineups",
        ("id",),
        on_delete="CASCADE",
    ),
    ForeignKeySpec(
        "lineup_players",
        "lineup_players_player_id_fkey",
        "fk_lineup_players_player",
        ("player_id",),
        "players",
        ("id",),
    ),
)

FK_INVENTORY_SQL = """
SELECT
    rel.relname AS table_name,
    con.conname AS constraint_name,
    array_agg(local_att.attname ORDER BY local_key.ordinality) AS local_columns,
    ref_rel.relname AS referenced_table,
    array_agg(ref_att.attname ORDER BY local_key.ordinality) AS referenced_columns,
    CASE con.confupdtype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
    END AS on_update,
    CASE con.confdeltype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
    END AS on_delete,
    con.condeferrable AS deferrable,
    con.condeferred AS initially_deferred,
    pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint AS con
JOIN pg_class AS rel ON rel.oid = con.conrelid
JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
JOIN pg_class AS ref_rel ON ref_rel.oid = con.confrelid
JOIN unnest(con.conkey) WITH ORDINALITY AS local_key(attnum, ordinality) ON true
JOIN unnest(con.confkey) WITH ORDINALITY AS ref_key(attnum, ordinality)
    ON ref_key.ordinality = local_key.ordinality
JOIN pg_attribute AS local_att
    ON local_att.attrelid = rel.oid AND local_att.attnum = local_key.attnum
JOIN pg_attribute AS ref_att
    ON ref_att.attrelid = ref_rel.oid AND ref_att.attnum = ref_key.attnum
WHERE ns.nspname = 'public' AND con.contype = 'f'
GROUP BY rel.relname, con.conname, ref_rel.relname, con.confupdtype,
         con.confdeltype, con.condeferrable, con.condeferred, con.oid
ORDER BY rel.relname, con.conname
"""


def normalize_dsn(dsn: str, *, require_clone: bool) -> tuple[str, str, str]:
    """Validate a DSN and return an asyncpg DSN, host and database name."""
    asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(asyncpg_dsn)
    database = parsed.path.lstrip("/")
    host = parsed.hostname or ""
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ReconciliationError("PostgreSQL DSN required")
    if require_clone and (
        host not in LOOPBACK_HOSTS or not database.startswith(CLONE_DATABASE_PREFIX)
    ):
        raise ReconciliationError(
            "mutating reconciliation is restricted to loopback disposable clone databases"
        )
    return asyncpg_dsn, host, database


def quote_identifier(identifier: str) -> str:
    """Quote a trusted PostgreSQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def normalize_sql(value: str | None) -> str | None:
    """Normalize catalog SQL formatting without changing semantics."""
    if value is None:
        return None
    return re.sub(r"\s+", " ", value.strip()).lower()


def normalize_constraint_sql(value: str | None) -> str | None:
    """Normalize catalog-equivalent text casts in CHECK expressions.

    PostgreSQL prints equivalent varchar-array coercions differently depending
    on whether the constraint came from SQLAlchemy metadata or Alembic DDL.
    Column types are compared independently, so removing these no-op text casts
    preserves the constraint semantics while avoiding a catalog-rendering false
    positive.
    """
    normalized = normalize_sql(value)
    if normalized is None:
        return None
    normalized = re.sub(r"::character varying(?:\(\d+\))?", "", normalized)
    normalized = normalized.replace("::text[]", "")
    return normalized.replace("::text", "")


def normalize_catalog_char(value: Any) -> str:
    """Return PostgreSQL internal single-character flags as text."""
    if isinstance(value, bytes):
        return value.decode("ascii")
    return str(value)


def _row_semantics(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(row["local_columns"]),
        row["referenced_table"],
        tuple(row["referenced_columns"]),
        row["on_update"],
        row["on_delete"],
        row["deferrable"],
        row["initially_deferred"],
    )


def _spec_semantics(spec: ForeignKeySpec) -> tuple[Any, ...]:
    return (
        spec.local_columns,
        spec.referenced_table,
        spec.referenced_columns,
        spec.on_update,
        spec.on_delete,
        spec.deferrable,
        spec.initially_deferred,
    )


def evaluate_foreign_keys(rows: list[dict[str, Any]]) -> list[ForeignKeyAuditResult]:
    """Evaluate all 22 drifted FKs before any rename is permitted."""
    by_key = {(row["table_name"], row["constraint_name"]): row for row in rows}
    results: list[ForeignKeyAuditResult] = []
    for spec in FK_SPECS:
        actual = by_key.get((spec.table, spec.actual_name))
        expected = by_key.get((spec.table, spec.expected_name))
        if actual is not None and expected is not None:
            results.append(
                ForeignKeyAuditResult(
                    spec.table,
                    spec.actual_name,
                    spec.expected_name,
                    spec.local_columns,
                    spec.referenced_table,
                    spec.referenced_columns,
                    spec.on_update,
                    spec.on_delete,
                    spec.deferrable,
                    spec.initially_deferred,
                    None,
                    False,
                    False,
                    "both legacy and canonical constraint names exist",
                )
            )
            continue
        active = expected or actual
        if active is None:
            results.append(
                ForeignKeyAuditResult(
                    spec.table,
                    spec.actual_name,
                    spec.expected_name,
                    spec.local_columns,
                    spec.referenced_table,
                    spec.referenced_columns,
                    spec.on_update,
                    spec.on_delete,
                    spec.deferrable,
                    spec.initially_deferred,
                    None,
                    False,
                    False,
                    "constraint is missing",
                )
            )
            continue
        matches = _row_semantics(active) == _spec_semantics(spec)
        results.append(
            ForeignKeyAuditResult(
                spec.table,
                spec.actual_name,
                spec.expected_name,
                spec.local_columns,
                spec.referenced_table,
                spec.referenced_columns,
                spec.on_update,
                spec.on_delete,
                spec.deferrable,
                spec.initially_deferred,
                active["constraint_name"],
                matches,
                matches and actual is not None,
                "semantic match" if matches else "foreign-key semantics differ",
            )
        )
    return results


def schema_diff(
    actual: dict[str, dict[str, dict[str, Any]]],
    expected: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Return deterministic missing, unexpected and divergent schema objects."""
    categories: dict[str, Any] = {}
    total_missing = 0
    total_unexpected = 0
    total_divergent = 0
    for category in sorted(set(actual) | set(expected)):
        actual_items = actual.get(category, {})
        expected_items = expected.get(category, {})
        missing = sorted(set(expected_items) - set(actual_items))
        unexpected = sorted(set(actual_items) - set(expected_items))
        divergent = [
            {
                "key": key,
                "actual": actual_items[key],
                "expected": expected_items[key],
            }
            for key in sorted(set(actual_items) & set(expected_items))
            if actual_items[key] != expected_items[key]
        ]
        categories[category] = {
            "missing": missing,
            "unexpected": unexpected,
            "divergent": divergent,
        }
        total_missing += len(missing)
        total_unexpected += len(unexpected)
        total_divergent += len(divergent)
    return {
        "missing": total_missing,
        "unexpected": total_unexpected,
        "divergent": total_divergent,
        "exact": total_missing == total_unexpected == total_divergent == 0,
        "categories": categories,
    }


async def fetch_foreign_keys(connection: asyncpg.Connection[Any]) -> list[dict[str, Any]]:
    rows = await connection.fetch(FK_INVENTORY_SQL)
    return [dict(row) for row in rows]


async def collect_schema_inventory(
    connection: asyncpg.Connection[Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Collect strict public-schema objects relevant to migrations 0001-0020."""
    tables = await connection.fetch("""
        SELECT rel.relname AS name, rel.relkind, rel.relpersistence
        FROM pg_class AS rel
        JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public' AND rel.relkind IN ('r', 'p')
        ORDER BY rel.relname
        """)
    columns = await connection.fetch("""
        SELECT rel.relname AS table_name, att.attname AS column_name,
               format_type(att.atttypid, att.atttypmod) AS data_type,
               att.attnotnull AS not_null,
               pg_get_expr(def.adbin, def.adrelid) AS default_expression,
               att.attidentity AS identity_kind,
               att.attgenerated AS generated_kind
        FROM pg_attribute AS att
        JOIN pg_class AS rel ON rel.oid = att.attrelid
        JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
        LEFT JOIN pg_attrdef AS def
            ON def.adrelid = att.attrelid AND def.adnum = att.attnum
        WHERE ns.nspname = 'public' AND rel.relkind IN ('r', 'p')
          AND att.attnum > 0 AND NOT att.attisdropped
        ORDER BY rel.relname, att.attnum
        """)
    indexes = await connection.fetch("""
        SELECT table_rel.relname AS table_name, index_rel.relname AS index_name,
               pg_get_indexdef(idx.indexrelid) AS definition,
               idx.indisunique, idx.indisprimary, idx.indisvalid, idx.indisready,
               idx.indnullsnotdistinct
        FROM pg_index AS idx
        JOIN pg_class AS table_rel ON table_rel.oid = idx.indrelid
        JOIN pg_class AS index_rel ON index_rel.oid = idx.indexrelid
        JOIN pg_namespace AS ns ON ns.oid = table_rel.relnamespace
        WHERE ns.nspname = 'public'
        ORDER BY table_rel.relname, index_rel.relname
        """)
    constraints = await connection.fetch("""
        SELECT rel.relname AS table_name, con.conname AS constraint_name,
               con.contype, pg_get_constraintdef(con.oid, true) AS definition,
               con.condeferrable, con.condeferred, con.convalidated
        FROM pg_constraint AS con
        JOIN pg_class AS rel ON rel.oid = con.conrelid
        JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public'
        ORDER BY rel.relname, con.conname
        """)
    sequences = await connection.fetch("""
        SELECT rel.relname AS name, seq.seqstart, seq.seqincrement, seq.seqmin,
               seq.seqmax, seq.seqcache, seq.seqcycle
        FROM pg_sequence AS seq
        JOIN pg_class AS rel ON rel.oid = seq.seqrelid
        JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public'
        ORDER BY rel.relname
        """)

    return {
        "tables": {
            row["name"]: {
                "relkind": row["relkind"],
                "persistence": row["relpersistence"],
            }
            for row in tables
        },
        "columns": {
            f'{row["table_name"]}.{row["column_name"]}': {
                "type": row["data_type"],
                "nullable": not row["not_null"],
                "default": normalize_sql(row["default_expression"]),
                "identity": normalize_catalog_char(row["identity_kind"]),
                "generated": normalize_catalog_char(row["generated_kind"]),
            }
            for row in columns
        },
        "indexes": {
            f'{row["table_name"]}.{row["index_name"]}': {
                "definition": normalize_sql(row["definition"]),
                "unique": row["indisunique"],
                "primary": row["indisprimary"],
                "valid": row["indisvalid"],
                "ready": row["indisready"],
                "nulls_not_distinct": row["indnullsnotdistinct"],
            }
            for row in indexes
        },
        "constraints": {
            f'{row["table_name"]}.{row["constraint_name"]}': {
                "type": normalize_catalog_char(row["contype"]),
                "definition": normalize_constraint_sql(row["definition"]),
                "deferrable": row["condeferrable"],
                "initially_deferred": row["condeferred"],
                "validated": row["convalidated"],
            }
            for row in constraints
        },
        "sequences": {
            row["name"]: {
                "start": row["seqstart"],
                "increment": row["seqincrement"],
                "minimum": row["seqmin"],
                "maximum": row["seqmax"],
                "cache": row["seqcache"],
                "cycle": row["seqcycle"],
            }
            for row in sequences
        },
    }


async def audit_foreign_keys(dsn: str) -> dict[str, Any]:
    asyncpg_dsn, host, database = normalize_dsn(dsn, require_clone=True)
    connection = await asyncpg.connect(asyncpg_dsn)
    try:
        results = evaluate_foreign_keys(await fetch_foreign_keys(connection))
    finally:
        await connection.close()
    return {
        "host": host,
        "database": database,
        "count": len(results),
        "all_semantics_match": all(result.semantics_match for result in results),
        "results": [asdict(result) for result in results],
    }


async def reconcile_clone(dsn: str) -> dict[str, Any]:
    """Transactionally rename verified FKs and restore four JSON defaults."""
    asyncpg_dsn, host, database = normalize_dsn(dsn, require_clone=True)
    connection = await asyncpg.connect(asyncpg_dsn)
    renamed: list[dict[str, str]] = []
    defaults: list[dict[str, str]] = []
    try:
        async with connection.transaction():
            results = evaluate_foreign_keys(await fetch_foreign_keys(connection))
            failures = [result for result in results if not result.semantics_match]
            if failures:
                raise ReconciliationError(
                    "foreign-key semantic verification failed: "
                    + ", ".join(f"{item.table}.{item.expected_name}" for item in failures)
                )

            default_rows = await connection.fetch(
                """
                SELECT att.attname AS column_name,
                       format_type(att.atttypid, att.atttypmod) AS data_type,
                       pg_get_expr(def.adbin, def.adrelid) AS default_expression
                FROM pg_attribute AS att
                JOIN pg_class AS rel ON rel.oid = att.attrelid
                JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
                LEFT JOIN pg_attrdef AS def
                    ON def.adrelid = att.attrelid AND def.adnum = att.attnum
                WHERE ns.nspname = 'public' AND rel.relname = 'decision_logs'
                  AND att.attname = ANY($1::text[])
                  AND att.attnum > 0 AND NOT att.attisdropped
                ORDER BY att.attname
                """,
                list(JSON_DEFAULT_COLUMNS),
            )
            by_column = {row["column_name"]: dict(row) for row in default_rows}
            if set(by_column) != set(JSON_DEFAULT_COLUMNS):
                raise ReconciliationError("one or more decision_logs JSON columns are missing")
            for column in JSON_DEFAULT_COLUMNS:
                row = by_column[column]
                current = normalize_sql(row["default_expression"])
                if row["data_type"] != "json" or current not in {None, "'[]'::json"}:
                    raise ReconciliationError(
                        f"unexpected default/type for decision_logs.{column}: "
                        f"type={row['data_type']} default={current}"
                    )

            for spec, result in zip(FK_SPECS, results, strict=True):
                if result.requires_rename:
                    await connection.execute(
                        f"ALTER TABLE {quote_identifier(spec.table)} "
                        f"RENAME CONSTRAINT {quote_identifier(spec.actual_name)} "
                        f"TO {quote_identifier(spec.expected_name)}"
                    )
                    renamed.append(
                        {
                            "table": spec.table,
                            "from": spec.actual_name,
                            "to": spec.expected_name,
                        }
                    )

            for column in JSON_DEFAULT_COLUMNS:
                current = normalize_sql(by_column[column]["default_expression"])
                if current is None:
                    await connection.execute(
                        "ALTER TABLE decision_logs "
                        f"ALTER COLUMN {quote_identifier(column)} SET DEFAULT '[]'::json"
                    )
                    defaults.append({"column": f"decision_logs.{column}", "default": "'[]'::json"})
    finally:
        await connection.close()
    return {
        "host": host,
        "database": database,
        "renamed_constraints": renamed,
        "restored_defaults": defaults,
        "idempotent_noop": not renamed and not defaults,
    }


async def compare_databases(actual_dsn: str, expected_dsn: str) -> dict[str, Any]:
    actual_url, _, actual_database = normalize_dsn(actual_dsn, require_clone=True)
    expected_url, _, expected_database = normalize_dsn(expected_dsn, require_clone=True)
    actual_connection = await asyncpg.connect(actual_url)
    expected_connection = await asyncpg.connect(expected_url)
    try:
        actual = await collect_schema_inventory(actual_connection)
        expected = await collect_schema_inventory(expected_connection)
    finally:
        await actual_connection.close()
        await expected_connection.close()
    return {
        "actual_database": actual_database,
        "expected_database": expected_database,
        **schema_diff(actual, expected),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("audit-fks", "reconcile"):
        command = subparsers.add_parser(name)
        command.add_argument("--database-url", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--actual-url", required=True)
    compare.add_argument("--expected-url", required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "audit-fks":
        return await audit_foreign_keys(args.database_url)
    if args.command == "reconcile":
        return await reconcile_clone(args.database_url)
    if args.command == "compare":
        return await compare_databases(args.actual_url, args.expected_url)
    raise AssertionError(f"unsupported command: {args.command}")


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except ReconciliationError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"status": "success", **result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
