"""Schema contracts for the Team Intelligence 0023 persistence boundary."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.repositories.sqlalchemy.models import (
    TeamIntelligenceCaptureReceiptORM,
    TeamIntelligenceObservationORM,
    TeamIntelligenceRawPayloadORM,
    TeamIntelligenceSourceORM,
)


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config)


def test_team_intelligence_revision_is_linear_successor_to_settlement_void() -> None:
    script = _script_directory()

    assert script.get_heads() == ["team_intelligence_0023"]
    assert script.get_revision("team_intelligence_0023").down_revision == ("settlement_void_0022")


def test_team_intelligence_metadata_contains_only_approved_four_tables() -> None:
    assert {
        TeamIntelligenceSourceORM.__tablename__,
        TeamIntelligenceRawPayloadORM.__tablename__,
        TeamIntelligenceCaptureReceiptORM.__tablename__,
        TeamIntelligenceObservationORM.__tablename__,
    } == {
        "team_intelligence_source_registry",
        "team_intelligence_raw_payloads",
        "team_intelligence_capture_receipts",
        "team_intelligence_observations",
    }


def test_observation_semantic_identity_excludes_captured_at() -> None:
    table = TeamIntelligenceObservationORM.__table__
    unique = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique["uq_ti_observation_semantic_key"] == ("semantic_key",)
    assert "captured_at" not in unique["uq_ti_observation_semantic_key"]


def test_observation_requires_raw_and_capture_provenance() -> None:
    table = TeamIntelligenceObservationORM.__table__
    foreign_keys = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys["fk_ti_obs_raw_provenance"] == (
        "raw_payload_id",
        "source_name",
        "source_policy",
    )
    assert foreign_keys["fk_ti_obs_capture_provenance"] == (
        "capture_receipt_id",
        "raw_payload_id",
        "source_url",
        "captured_at",
    )
    assert all(
        table.c[column].nullable is False
        for column in (
            "raw_payload_id",
            "capture_receipt_id",
            "source_name",
            "source_url",
            "source_policy",
            "captured_at",
        )
    )


def test_source_registry_fails_closed_when_enabled() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in TeamIntelligenceSourceORM.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    enabled_policy = checks["ck_ti_source_enabled_policy"]
    assert "status = 'APPROVED'" in enabled_policy
    assert "storage_allowed IS TRUE" in enabled_policy
    assert "automation_allowed IS TRUE" in enabled_policy


def test_migration_defines_database_append_only_and_policy_triggers() -> None:
    migration = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "team_intelligence_0023.py"
    ).read_text(encoding="utf-8")

    for table in (
        "team_intelligence_raw_payloads",
        "team_intelligence_capture_receipts",
        "team_intelligence_observations",
    ):
        assert f'"{table}"' in migration
    assert "trg_{table}_reject_change" in migration
    assert "trg_{table}_reject_truncate" in migration
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "BEFORE TRUNCATE" in migration
    assert "team_intelligence_enforce_source_policy" in migration
    assert "team_intelligence_enforce_capture_source_policy" in migration
    assert "trg_team_intelligence_capture_receipts_source_policy" in migration
