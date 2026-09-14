"""Verify Alembic 0022 append-only guards on an explicitly isolated database."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

TABLES = (
    "intelligence_raw_payloads",
    "intelligence_observations",
    "team_identity_mappings",
    "fixture_identity_mappings",
    "venue_mappings",
)


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("TEST_DATABASE_URL is required")
    database = make_url(value).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("append-only verification requires a database ending in _test")
    return value


async def _mutation_is_rejected(connection, statement: str) -> bool:
    savepoint = await connection.begin_nested()
    try:
        await connection.execute(text(statement))
    except DBAPIError as exc:
        await savepoint.rollback()
        return "intelligence records are append-only" in str(exc.orig)
    await savepoint.rollback()
    return False


async def main() -> None:
    engine = create_async_engine(_database_url())
    ids = {name: uuid4() for name in ("source", "raw", "observation", "team", "fixture", "venue")}
    token = uuid4().hex
    results: dict[str, dict[str, bool]] = {}
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0022":
                raise RuntimeError(f"expected Alembic 0022, found {revision}")
            fixture_id = await connection.scalar(
                text("SELECT id FROM fixtures ORDER BY id LIMIT 1")
            )
            if fixture_id is None:
                raise RuntimeError("clone must contain at least one fixture")

            await connection.commit()
            transaction = await connection.begin()
            await connection.execute(
                text(
                    "INSERT INTO intelligence_sources "
                    "(id, code, display_name, base_url, source_policy, automation_allowed, "
                    "license_reference) VALUES "
                    "(:id, :code, 'Task 060', 'https://example.test', 'OPEN_DATA', false, "
                    "'https://example.test/license')"
                ),
                {"id": ids["source"], "code": f"task060-{token}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO intelligence_raw_payloads "
                    "(id, natural_key, source_id, source_url, captured_at, raw_payload_hash, "
                    "media_type, byte_length, storage_uri, source_policy) VALUES "
                    "(:id, :key, :source, 'https://example.test/raw', now(), :hash, "
                    "'application/json', 2, 'sha256://task060', 'OPEN_DATA')"
                ),
                {
                    "id": ids["raw"],
                    "key": "a" * 64,
                    "source": ids["source"],
                    "hash": "b" * 64,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO intelligence_observations "
                    "(id, observation_key, fixture_id, source_id, raw_payload_id, source_url, "
                    "data_type, observed_at, captured_at, raw_payload_hash, parser_version, "
                    "schema_version, source_policy, semantic_status, capture_window, payload) "
                    "VALUES (:id, :key, :fixture, :source, :raw, 'https://example.test/raw', "
                    "'FIXTURE', now(), now(), :hash, 'task060-v1', '1', 'OPEN_DATA', "
                    "'VERIFIED', 'DAILY', '{}'::json)"
                ),
                {
                    "id": ids["observation"],
                    "key": "c" * 64,
                    "fixture": fixture_id,
                    "source": ids["source"],
                    "raw": ids["raw"],
                    "hash": "b" * 64,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO team_identity_mappings "
                    "(id, mapping_key, source_id, source_team_name, status, method, captured_at) "
                    "VALUES (:id, :key, :source, 'Unknown', 'UNMAPPED', 'NONE', now())"
                ),
                {"id": ids["team"], "key": "d" * 64, "source": ids["source"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO fixture_identity_mappings "
                    "(id, mapping_key, source_id, source_fixture_id, source_competition, "
                    "source_home_team, source_away_team, source_kickoff, status, method, "
                    "captured_at) VALUES (:id, :key, :source, 'unknown', 'Unknown', 'Home', "
                    "'Away', now(), 'UNMAPPED', 'NONE', now())"
                ),
                {"id": ids["fixture"], "key": "e" * 64, "source": ids["source"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO venue_mappings "
                    "(id, mapping_key, fixture_id, source_id, source_venue_name, status, "
                    "method, captured_at) VALUES (:id, :key, :fixture, :source, 'Unknown', "
                    "'WEATHER_UNAVAILABLE', 'NONE', now())"
                ),
                {
                    "id": ids["venue"],
                    "key": "f" * 64,
                    "fixture": fixture_id,
                    "source": ids["source"],
                },
            )

            for table in TABLES:
                row_id = ids[
                    {
                        "intelligence_raw_payloads": "raw",
                        "intelligence_observations": "observation",
                        "team_identity_mappings": "team",
                        "fixture_identity_mappings": "fixture",
                        "venue_mappings": "venue",
                    }[table]
                ]
                results[table] = {
                    "update_rejected": await _mutation_is_rejected(
                        connection,
                        f"UPDATE {table} SET id = id WHERE id = '{row_id}'",
                    ),
                    "delete_rejected": await _mutation_is_rejected(
                        connection,
                        f"DELETE FROM {table} WHERE id = '{row_id}'",
                    ),
                }
            await transaction.rollback()
    finally:
        await engine.dispose()

    if not all(all(checks.values()) for checks in results.values()):
        raise RuntimeError(f"append-only verification failed: {results}")
    print(json.dumps({"revision": "0022", "append_only": results}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
