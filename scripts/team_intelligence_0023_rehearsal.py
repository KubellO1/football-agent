"""Rehearse Team Intelligence 0023 on an unmistakably disposable PostgreSQL DB."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.repositories.sqlalchemy.team_intelligence_repository import (
    SqlAlchemyTeamIntelligenceRepository,
)
from app.team_intelligence.conflicts import resolve_conflicts
from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    RawPayload,
    SourcePolicy,
    canonical_json,
)
from app.team_intelligence.persistence import CaptureWrite, SourceRegistration
from app.team_intelligence.sources import load_source_registry

TASK_ID = "TASK-20261011-086"
BASE_REVISION = "settlement_void_0022"
HEAD_REVISION = "team_intelligence_0023"
NAMESPACE = UUID("5a6c3867-6e17-5787-8a31-41ce267ee803")
TEAM_TABLES = (
    "team_intelligence_source_registry",
    "team_intelligence_raw_payloads",
    "team_intelligence_capture_receipts",
    "team_intelligence_observations",
)


def _dsn() -> str:
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("TEST_DATABASE_URL is required; production fallback is forbidden")
    url = make_url(value)
    database = (url.database or "").casefold()
    if database == "football" or not database.endswith("_test"):
        raise RuntimeError("rehearsal database must be disposable and end with '_test'")
    return value


def _alembic_config(dsn: str) -> Config:
    root = Path(__file__).resolve().parents[1]
    os.environ["DATABASE_URL"] = dsn
    get_settings.cache_clear()
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def _migrate(config: Config, revision: str) -> None:
    command.upgrade(config, revision)


def _downgrade(config: Config, revision: str) -> None:
    command.downgrade(config, revision)


def _uuid(kind: str, value: object) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{value}")


async def _seed_baseline(dsn: str, fixture_count: int) -> list[dict[str, Any]]:
    engine = create_async_engine(dsn)
    competition_id = _uuid("competition", "five-league-canary")
    fixtures: list[dict[str, Any]] = []
    async with engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO competitions
                    (id, name, country, tier, external_source, external_id)
                VALUES
                    (:id, 'Isolated Five-League Canary', 'Test', 1,
                     'team-intelligence-rehearsal', 'competition-1')
                """),
            {"id": competition_id},
        )
        for index in range(fixture_count):
            home_id = _uuid("team", f"home-{index}")
            away_id = _uuid("team", f"away-{index}")
            fixture_id = _uuid("fixture", index)
            await connection.execute(
                text("""
                    INSERT INTO teams (id, name, external_source, external_id)
                    VALUES
                        (:home_id, :home_name, 'team-intelligence-rehearsal', :home_external),
                        (:away_id, :away_name, 'team-intelligence-rehearsal', :away_external)
                    """),
                {
                    "home_id": home_id,
                    "home_name": f"Canary Home {index + 1}",
                    "home_external": f"home-{index}",
                    "away_id": away_id,
                    "away_name": f"Canary Away {index + 1}",
                    "away_external": f"away-{index}",
                },
            )
            kickoff = datetime(2026, 10, 12, 12, 0, tzinfo=UTC) + timedelta(hours=index)
            await connection.execute(
                text("""
                    INSERT INTO fixtures
                        (id, competition_id, home_team_id, away_team_id, kickoff, status,
                         external_source, external_id)
                    VALUES
                        (:id, :competition_id, :home_id, :away_id, :kickoff, 'NS',
                         'team-intelligence-rehearsal', :external_id)
                    """),
                {
                    "id": fixture_id,
                    "competition_id": competition_id,
                    "home_id": home_id,
                    "away_id": away_id,
                    "kickoff": kickoff,
                    "external_id": f"fixture-{index}",
                },
            )
            fixtures.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": home_id,
                    "provider_fixture_id": f"fixture-{index}",
                    "provider_team_id": f"home-{index}",
                }
            )
    await engine.dispose()
    return fixtures


async def _existing_snapshot(dsn: str) -> dict[str, dict[str, object]]:
    engine = create_async_engine(dsn)
    result: dict[str, dict[str, object]] = {}
    async with engine.connect() as connection:
        tables = (await connection.execute(text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                      AND table_name <> 'alembic_version'
                      AND table_name NOT LIKE 'team_intelligence_%'
                    ORDER BY table_name
                    """))).scalars()
        for table in tables:
            safe_table = '"' + table.replace('"', '""') + '"'
            count = (
                await connection.execute(text(f"SELECT count(*) FROM {safe_table}"))
            ).scalar_one()
            digest = (
                await connection.execute(
                    text(
                        f"SELECT md5(COALESCE(string_agg(row_value, '|' ORDER BY row_value), '')) "
                        f"FROM (SELECT row_to_json(t)::text AS row_value FROM {safe_table} t) s"
                    )
                )
            ).scalar_one()
            result[table] = {"count": count, "hash": digest}
    await engine.dispose()
    return result


async def _schema_hash(dsn: str) -> str:
    engine = create_async_engine(dsn)
    components: list[tuple[object, ...]] = []
    async with engine.connect() as connection:
        for query in (
            """
            SELECT table_name, column_name, ordinal_position, data_type, udt_name,
                   is_nullable, COALESCE(column_default, '')
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name LIKE 'team_intelligence_%'
            ORDER BY table_name, ordinal_position
            """,
            """
            SELECT c.conrelid::regclass::text, c.conname, c.contype,
                   pg_get_constraintdef(c.oid, true)
            FROM pg_constraint c
            WHERE c.conrelid::regclass::text LIKE 'team_intelligence_%'
            ORDER BY 1, 2
            """,
            """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename LIKE 'team_intelligence_%'
            ORDER BY tablename, indexname
            """,
            """
            SELECT event_object_table, trigger_name, action_timing, event_manipulation,
                   action_statement
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
              AND event_object_table LIKE 'team_intelligence_%'
            ORDER BY event_object_table, trigger_name, event_manipulation
            """,
        ):
            rows = (await connection.execute(text(query))).all()
            components.extend(tuple(row) for row in rows)
    await engine.dispose()
    return hashlib.sha256(canonical_json({"schema": components})).hexdigest()


async def _register_sources(
    session: AsyncSession,
    *,
    reviewed_at: datetime,
) -> dict[str, int]:
    repository = SqlAlchemyTeamIntelligenceRepository(session)
    inserted = 0
    enabled = 0
    for definition in load_source_registry():
        should_enable = definition.status.value == "APPROVED"
        inserted += await repository.register_source(
            SourceRegistration(
                definition=definition,
                policy_reviewed_at=reviewed_at,
                parser_version="team-intelligence-0023-canary-v1",
                enabled=should_enable,
            )
        )
        enabled += should_enable
    return {"registered": inserted, "enabled": enabled}


def _api_capture(
    fixture: dict[str, Any], *, captured_at: datetime
) -> tuple[CaptureWrite, Observation]:
    provider_fixture_id = str(fixture["provider_fixture_id"])
    payload_body = canonical_json(
        {
            "fixture": provider_fixture_id,
            "team": fixture["provider_team_id"],
            "status": "out",
        }
    )
    payload = RawPayload(
        source_name="API-Football",
        source_url=("https://v3.football.api-sports.io/injuries?fixture=" + provider_fixture_id),
        captured_at=captured_at,
        content=payload_body,
        media_type="application/json",
        source_policy=SourcePolicy.EXISTING_LICENSED_API,
        response_headers={"etag": f'"{provider_fixture_id}"'},
    )
    observation = Observation(
        team_id=str(fixture["team_id"]),
        player_id=None,
        fixture_id=str(fixture["fixture_id"]),
        source_name="API-Football",
        source_url=payload.source_url,
        published_at=captured_at - timedelta(minutes=5),
        captured_at=captured_at,
        original_title=None,
        original_text="Provider reports unavailable",
        original_quote=None,
        language="en",
        parser_version="team-intelligence-0023-canary-v1",
        raw_payload_hash=payload.sha256,
        data_type=DataType.INJURY,
        evidence_level=EvidenceLevel.UNVERIFIED,
        source_policy=SourcePolicy.EXISTING_LICENSED_API,
        capture_window=CaptureWindow.DAILY,
        normalized={"availability_status": "out"},
        provider_team_id=str(fixture["provider_team_id"]),
        provider_fixture_id=provider_fixture_id,
    )
    return (
        CaptureWrite(
            payload=payload,
            object_uri=f"file:///isolated/api-football/{payload.sha256}.json",
            http_status=200,
            automated=True,
        ),
        observation,
    )


def _manual_conflict(
    fixture: dict[str, Any],
    *,
    captured_at: datetime,
) -> tuple[CaptureWrite, Observation]:
    payload = RawPayload(
        source_name="Manual official evidence",
        source_url="https://club.example.test/medical/fixture-0",
        captured_at=captured_at,
        content=b"Official medical update: player returned to training.",
        media_type="text/plain",
        source_policy=SourcePolicy.MANUAL_OFFICIAL_EVIDENCE,
    )
    observation = Observation(
        team_id=str(fixture["team_id"]),
        player_id=None,
        fixture_id=str(fixture["fixture_id"]),
        source_name=payload.source_name,
        source_url=payload.source_url,
        published_at=captured_at - timedelta(minutes=2),
        captured_at=captured_at,
        original_title="Medical update",
        original_text="Player returned to training",
        original_quote="returned to training",
        language="en",
        parser_version="manual-official-evidence-v1",
        raw_payload_hash=payload.sha256,
        data_type=DataType.INJURY,
        evidence_level=EvidenceLevel.OFFICIAL_MEDICAL_REPORT,
        source_policy=SourcePolicy.MANUAL_OFFICIAL_EVIDENCE,
        capture_window=CaptureWindow.DAILY,
        normalized={"availability_status": "returned"},
        provider_team_id=str(fixture["provider_team_id"]),
        provider_fixture_id=str(fixture["provider_fixture_id"]),
    )
    return (
        CaptureWrite(
            payload=payload,
            object_uri=f"file:///isolated/manual/{payload.sha256}.txt",
            http_status=200,
            automated=False,
        ),
        observation,
    )


async def _run_canary(
    dsn: str,
    fixtures: list[dict[str, Any]],
) -> dict[str, object]:
    engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    base_time = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    captures = [
        _api_capture(fixture, captured_at=base_time + timedelta(minutes=index))
        for index, fixture in enumerate(fixtures)
    ]
    captures.append(_manual_conflict(fixtures[0], captured_at=base_time + timedelta(minutes=20)))
    async with factory() as session:
        registry = await _register_sources(session, reviewed_at=base_time)
        repository = SqlAlchemyTeamIntelligenceRepository(session)
        inserted = 0
        for capture, observation in captures:
            result = await repository.append_capture(capture, (observation,))
            inserted += result.observations_inserted

        replay_inserted = 0
        replay_duplicates = 0
        for capture, observation in captures:
            result = await repository.append_capture(capture, (observation,))
            replay_inserted += result.observations_inserted
            replay_duplicates += result.observation_duplicates

        first_capture, first_observation = captures[0]
        later_payload = replace(
            first_capture.payload,
            captured_at=first_capture.payload.captured_at + timedelta(minutes=30),
        )
        later_result = await repository.append_capture(
            replace(first_capture, payload=later_payload),
            (replace(first_observation, captured_at=later_payload.captured_at),),
        )
        persisted_first = await repository.list_by_fixture(fixtures[0]["fixture_id"])
        await session.commit()

    async with engine.connect() as connection:
        observation_count = (
            await connection.execute(text("SELECT count(*) FROM team_intelligence_observations"))
        ).scalar_one()
        receipt_count = (
            await connection.execute(
                text("SELECT count(*) FROM team_intelligence_capture_receipts")
            )
        ).scalar_one()
        duplicate_count = (await connection.execute(text("""
                    SELECT count(*) FROM (
                        SELECT semantic_key
                        FROM team_intelligence_observations
                        GROUP BY semantic_key HAVING count(*) > 1
                    ) duplicates
                    """))).scalar_one()
        orphan_count = (await connection.execute(text("""
                    SELECT count(*)
                    FROM team_intelligence_observations o
                    LEFT JOIN team_intelligence_raw_payloads r ON r.id = o.raw_payload_id
                    LEFT JOIN team_intelligence_capture_receipts c
                      ON c.id = o.capture_receipt_id
                     AND c.raw_payload_id = o.raw_payload_id
                     AND c.source_url = o.source_url
                     AND c.captured_at = o.captured_at
                    LEFT JOIN teams t ON t.id = o.team_id
                    LEFT JOIN fixtures f ON f.id = o.fixture_id
                    WHERE r.id IS NULL OR c.id IS NULL OR t.id IS NULL
                       OR (o.fixture_id IS NOT NULL AND f.id IS NULL)
                    """))).scalar_one()
        provenance_count = (await connection.execute(text("""
                    SELECT count(*)
                    FROM team_intelligence_observations o
                    JOIN team_intelligence_raw_payloads r ON r.id = o.raw_payload_id
                    JOIN team_intelligence_capture_receipts c ON c.id = o.capture_receipt_id
                    JOIN team_intelligence_source_registry s ON s.source_name = o.source_name
                    WHERE o.source_url = c.source_url
                      AND o.captured_at = c.captured_at
                      AND o.raw_payload_id = c.raw_payload_id
                      AND o.source_name = r.source_name
                      AND o.source_policy = r.source_policy
                      AND o.source_policy = s.source_policy
                    """))).scalar_one()
    await engine.dispose()
    conflict = resolve_conflicts(persisted_first)[0]
    return {
        "fixtures": len(fixtures),
        "registry": registry,
        "observations_inserted": inserted,
        "observations": observation_count,
        "capture_receipts": receipt_count,
        "replay_inserted": replay_inserted,
        "replay_duplicates": replay_duplicates,
        "later_capture_observation_inserted": later_result.observations_inserted,
        "later_capture_receipt_inserted": later_result.capture_receipt_inserted,
        "semantic_duplicates": duplicate_count,
        "orphans": orphan_count,
        "provenance_percent": (
            100.0 if observation_count == 0 else 100.0 * provenance_count / observation_count
        ),
        "conflict_preserved": conflict.conflict and len(conflict.observation_ids) == 2,
    }


async def _mutation_guard_checks(dsn: str) -> dict[str, bool]:
    engine = create_async_engine(dsn)

    async def rejected(statement: str) -> bool:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(statement))
            except DBAPIError:
                await transaction.rollback()
                return True
            await transaction.rollback()
            return False

    async def disabled_capture_rejected() -> bool:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                raw_id = (await connection.execute(text("""
                            SELECT id
                            FROM team_intelligence_raw_payloads
                            WHERE source_name = 'api-football'
                            ORDER BY first_captured_at
                            LIMIT 1
                            """))).scalar_one()
                await connection.execute(text("""
                        UPDATE team_intelligence_source_registry
                        SET enabled = false
                        WHERE source_name = 'api-football'
                        """))
                await connection.execute(
                    text("""
                        INSERT INTO team_intelligence_capture_receipts
                            (id, raw_payload_id, source_url, captured_at, http_status)
                        VALUES
                            ('00000000-0000-0000-0000-000000000002'::uuid,
                             :raw_id,
                             'https://example.test/disabled-capture',
                             '2026-10-11T15:00:00+00:00'::timestamptz,
                             200)
                        """),
                    {"raw_id": raw_id},
                )
            except DBAPIError:
                await transaction.rollback()
                return True
            await transaction.rollback()
            return False

    results = {
        "update_rejected": await rejected(
            "UPDATE team_intelligence_observations SET language = 'fr'"
        ),
        "delete_rejected": await rejected("DELETE FROM team_intelligence_capture_receipts"),
        "truncate_rejected": await rejected("TRUNCATE team_intelligence_raw_payloads CASCADE"),
        "disabled_source_insert_rejected": await rejected("""
            INSERT INTO team_intelligence_raw_payloads
                (id, source_name, source_url, content_sha256, media_type, object_uri,
                 source_policy, first_captured_at)
            SELECT
                '00000000-0000-0000-0000-000000000001'::uuid,
                source_name,
                'https://example.test/rejected',
                repeat('0', 64),
                'application/json',
                'file:///isolated/rejected.json',
                source_policy,
                now()
            FROM team_intelligence_source_registry
            WHERE status IN ('LICENSE_UNCLEAR', 'REJECTED')
            ORDER BY source_name
            LIMIT 1
            """),
        "disabled_capture_insert_rejected": await disabled_capture_rejected(),
    }
    await engine.dispose()
    return results


async def _storage(dsn: str, fixture_count: int) -> dict[str, object]:
    engine = create_async_engine(dsn)
    async with engine.connect() as connection:
        rows = (await connection.execute(text("""
                    SELECT c.relname, pg_total_relation_size(c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                      AND c.relname LIKE 'team_intelligence_%'
                    ORDER BY c.relname
                    """))).all()
    await engine.dispose()
    by_table: dict[str, int] = {str(row[0]): int(row[1]) for row in rows}
    total = sum(by_table.values())
    per_fixture = total / fixture_count if fixture_count else 0
    return {
        "database_bytes": total,
        "table_bytes": by_table,
        "bytes_per_canary_fixture": round(per_fixture, 2),
        "estimated_1000_fixtures_bytes": round(per_fixture * 1000),
        "raw_payload_bytes_external": True,
    }


async def _revision(dsn: str) -> str:
    engine = create_async_engine(dsn)
    async with engine.connect() as connection:
        value = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
    await engine.dispose()
    return str(value)


async def _team_row_count(dsn: str) -> int:
    engine = create_async_engine(dsn)
    async with engine.connect() as connection:
        count = (await connection.execute(text("""
                    SELECT count(*)
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name LIKE 'team_intelligence_%'
                    """))).scalar_one()
    await engine.dispose()
    return int(count)


def _run(label: str, output: Path, fixture_count: int) -> dict[str, object]:
    if not 1 <= fixture_count <= 10:
        raise ValueError("fixture_count must be between 1 and 10")
    dsn = _dsn()
    config = _alembic_config(dsn)

    _migrate(config, BASE_REVISION)
    fixtures = asyncio.run(_seed_baseline(dsn, fixture_count))
    baseline_before = asyncio.run(_existing_snapshot(dsn))

    _migrate(config, HEAD_REVISION)
    schema_hash_first = asyncio.run(_schema_hash(dsn))
    canary = asyncio.run(_run_canary(dsn, fixtures))
    mutation_guards = asyncio.run(_mutation_guard_checks(dsn))
    storage = asyncio.run(_storage(dsn, fixture_count))

    _downgrade(config, BASE_REVISION)
    baseline_after_downgrade = asyncio.run(_existing_snapshot(dsn))
    tables_after_downgrade = asyncio.run(_team_row_count(dsn))

    _migrate(config, HEAD_REVISION)
    schema_hash_second = asyncio.run(_schema_hash(dsn))
    baseline_after_reupgrade = asyncio.run(_existing_snapshot(dsn))
    revision = asyncio.run(_revision(dsn))
    command.check(config)

    result: dict[str, object] = {
        "task_id": TASK_ID,
        "label": label,
        "database_identity": "DISPOSABLE_TEST_DATABASE",
        "fixture_count": fixture_count,
        "upgrade": f"{BASE_REVISION}->{HEAD_REVISION}",
        "downgrade": f"{HEAD_REVISION}->{BASE_REVISION}",
        "reupgrade": f"{BASE_REVISION}->{HEAD_REVISION}",
        "revision": revision,
        "schema_hash_first": schema_hash_first,
        "schema_hash_second": schema_hash_second,
        "schema_hash_match": schema_hash_first == schema_hash_second,
        "baseline_after_downgrade_match": baseline_before == baseline_after_downgrade,
        "baseline_after_reupgrade_match": baseline_before == baseline_after_reupgrade,
        "team_tables_after_downgrade": tables_after_downgrade,
        "canary": canary,
        "append_only_guards": mutation_guards,
        "storage": storage,
        "alembic_check": "clean",
        "production_db_reads": 0,
        "production_db_writes": 0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=int, default=10)
    args = parser.parse_args()
    result = _run(args.label, args.output, args.fixtures)
    print(
        json.dumps(
            {
                "label": result["label"],
                "revision": result["revision"],
                "schema_hash_match": result["schema_hash_match"],
                "baseline_after_reupgrade_match": result["baseline_after_reupgrade_match"],
                "canary": result["canary"],
                "append_only_guards": result["append_only_guards"],
                "production_db_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
