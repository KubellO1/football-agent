"""create append-only team-intelligence persistence

Revision ID: team_intelligence_0023
Revises: settlement_void_0022
Create Date: 2026-10-11

The migration adds only the reviewed Team Intelligence evidence boundary.  It
does not connect evidence to model inputs or seed production observations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "team_intelligence_0023"
down_revision: str | None = "settlement_void_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "team_intelligence_raw_payloads",
    "team_intelligence_capture_receipts",
    "team_intelligence_observations",
)


def _create_source_registry() -> None:
    op.create_table(
        "team_intelligence_source_registry",
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("automation_allowed", sa.Boolean(), nullable=True),
        sa.Column("storage_allowed", sa.Boolean(), nullable=True),
        sa.Column("terms_url", sa.Text(), nullable=True),
        sa.Column("robots_url", sa.Text(), nullable=True),
        sa.Column("source_policy", sa.String(length=64), nullable=False),
        sa.Column("policy_reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('LEAGUE_SITE', 'CLUB_SITE', 'PROVIDER_API', 'MANUAL')",
            name="ck_ti_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'RESEARCH_ONLY', 'LICENSE_UNCLEAR', 'REJECTED')",
            name="ck_ti_source_status",
        ),
        sa.CheckConstraint(
            "source_policy IN ('OFFICIAL_PUBLIC_API', 'EXISTING_LICENSED_API', "
            "'PUBLIC_WEB_AUTOMATION_ALLOWED', 'MANUAL_OFFICIAL_EVIDENCE', "
            "'RESEARCH_ONLY', 'REJECTED')",
            name="ck_ti_source_policy",
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ti_source_policy_hash",
        ),
        sa.CheckConstraint(
            "NOT enabled OR (status = 'APPROVED' AND storage_allowed IS TRUE "
            "AND (source_type = 'MANUAL' OR automation_allowed IS TRUE))",
            name="ck_ti_source_enabled_policy",
        ),
        sa.PrimaryKeyConstraint("source_name"),
        sa.UniqueConstraint(
            "source_name",
            "source_policy",
            name="uq_ti_source_name_policy",
        ),
    )


def _create_evidence_tables() -> None:
    op.create_table(
        "team_intelligence_raw_payloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("source_policy", sa.String(length=64), nullable=False),
        sa.Column("first_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_ti_raw_payload_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["source_name", "source_policy"],
            [
                "team_intelligence_source_registry.source_name",
                "team_intelligence_source_registry.source_policy",
            ],
            name="fk_ti_raw_source_policy",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_name",
            "content_sha256",
            name="uq_ti_raw_source_content",
        ),
        sa.UniqueConstraint(
            "id",
            "source_name",
            "source_policy",
            name="uq_ti_raw_provenance",
        ),
    )

    op.create_table(
        "team_intelligence_capture_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "http_status >= 100 AND http_status <= 599",
            name="ck_ti_capture_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["team_intelligence_raw_payloads.id"],
            name="fk_ti_capture_raw_payload",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "raw_payload_id",
            "source_url",
            "captured_at",
            name="uq_ti_capture_natural",
        ),
        sa.UniqueConstraint(
            "id",
            "raw_payload_id",
            "source_url",
            "captured_at",
            name="uq_ti_capture_provenance",
        ),
    )

    op.create_table(
        "team_intelligence_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("semantic_key", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=True),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=False),
        sa.Column("capture_receipt_id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_title", sa.Text(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("original_quote", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("data_type", sa.String(length=64), nullable=False),
        sa.Column("evidence_level", sa.String(length=64), nullable=False),
        sa.Column("source_policy", sa.String(length=64), nullable=False),
        sa.Column("capture_window", sa.String(length=20), nullable=False),
        sa.Column("provider_team_id", sa.String(length=120), nullable=True),
        sa.Column("provider_player_id", sa.String(length=120), nullable=True),
        sa.Column("provider_fixture_id", sa.String(length=120), nullable=True),
        sa.Column(
            "normalized",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "semantic_key ~ '^[0-9a-f]{64}$'",
            name="ck_ti_observation_semantic_key",
        ),
        sa.CheckConstraint(
            "data_type IN ('INJURY', 'RECOVERY_STATUS', 'ILLNESS', 'SUSPENSION', "
            "'TRAINING_STATUS', 'SQUAD_CALLUP', 'SQUAD_OMISSION', 'CONFIRMED_LINEUP', "
            "'SUBSTITUTES', 'FORMATION', 'ROTATION_PLAN', 'MINUTES_RESTRICTION', "
            "'MANAGER_QUOTE', 'TACTICAL_CHANGE', 'PLAYER_RETURN', 'LATE_WITHDRAWAL', "
            "'INTERNATIONAL_DUTY', 'GENERAL_TEAM_NEWS')",
            name="ck_ti_observation_data_type",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('OFFICIAL_CONFIRMED', 'DIRECT_COACH_QUOTE', "
            "'OFFICIAL_MATCH_PAGE', 'OFFICIAL_MEDICAL_REPORT', "
            "'OFFICIAL_DISCIPLINARY_NOTICE', 'INFERRED', 'UNVERIFIED')",
            name="ck_ti_observation_evidence_level",
        ),
        sa.CheckConstraint(
            "source_policy IN ('OFFICIAL_PUBLIC_API', 'EXISTING_LICENSED_API', "
            "'PUBLIC_WEB_AUTOMATION_ALLOWED', 'MANUAL_OFFICIAL_EVIDENCE', "
            "'RESEARCH_ONLY', 'REJECTED')",
            name="ck_ti_observation_source_policy",
        ),
        sa.CheckConstraint(
            "capture_window IN ('DAILY', 'T-24H', 'T-6H', 'T-90', 'T-60', "
            "'T-30', 'T-15', 'POST_MATCH')",
            name="ck_ti_observation_capture_window",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at <= captured_at",
            name="ck_ti_observation_published_before_capture",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(normalized) = 'object'",
            name="ck_ti_observation_normalized_object",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_ti_observation_team",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_ti_observation_player",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"],
            ["fixtures.id"],
            name="fk_ti_observation_fixture",
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id", "source_name", "source_policy"],
            [
                "team_intelligence_raw_payloads.id",
                "team_intelligence_raw_payloads.source_name",
                "team_intelligence_raw_payloads.source_policy",
            ],
            name="fk_ti_obs_raw_provenance",
        ),
        sa.ForeignKeyConstraint(
            [
                "capture_receipt_id",
                "raw_payload_id",
                "source_url",
                "captured_at",
            ],
            [
                "team_intelligence_capture_receipts.id",
                "team_intelligence_capture_receipts.raw_payload_id",
                "team_intelligence_capture_receipts.source_url",
                "team_intelligence_capture_receipts.captured_at",
            ],
            name="fk_ti_obs_capture_provenance",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "semantic_key",
            name="uq_ti_observation_semantic_key",
        ),
    )

    op.create_index(
        "ix_ti_observation_fixture_window_capture",
        "team_intelligence_observations",
        ["fixture_id", "capture_window", "captured_at"],
    )
    op.create_index(
        "ix_ti_observation_team_type_capture",
        "team_intelligence_observations",
        ["team_id", "data_type", "captured_at"],
    )
    op.create_index(
        "ix_ti_observation_player_type_capture",
        "team_intelligence_observations",
        ["player_id", "data_type", "captured_at"],
        postgresql_where=sa.text("player_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ti_observation_source_fixture_capture",
        "team_intelligence_observations",
        ["source_name", "provider_fixture_id", "captured_at"],
    )


def _create_policy_and_append_only_triggers() -> None:
    op.execute("""
        CREATE FUNCTION team_intelligence_enforce_source_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            reviewed_source team_intelligence_source_registry%ROWTYPE;
        BEGIN
            SELECT * INTO reviewed_source
            FROM team_intelligence_source_registry
            WHERE source_name = NEW.source_name;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'team intelligence source is not registered: %', NEW.source_name
                    USING ERRCODE = '23503';
            END IF;
            IF reviewed_source.source_policy <> NEW.source_policy
               OR reviewed_source.status <> 'APPROVED'
               OR reviewed_source.enabled IS NOT TRUE
               OR reviewed_source.storage_allowed IS NOT TRUE
               OR (
                    reviewed_source.source_type <> 'MANUAL'
                    AND reviewed_source.automation_allowed IS NOT TRUE
               ) THEN
                RAISE EXCEPTION 'team intelligence source policy rejected: %', NEW.source_name
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """)
    for table in ("team_intelligence_raw_payloads", "team_intelligence_observations"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_source_policy
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION team_intelligence_enforce_source_policy()
            """)

    op.execute("""
        CREATE FUNCTION team_intelligence_enforce_capture_source_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            reviewed_source team_intelligence_source_registry%ROWTYPE;
        BEGIN
            SELECT s.* INTO reviewed_source
            FROM team_intelligence_raw_payloads r
            JOIN team_intelligence_source_registry s
              ON s.source_name = r.source_name
             AND s.source_policy = r.source_policy
            WHERE r.id = NEW.raw_payload_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'team intelligence capture source is not registered'
                    USING ERRCODE = '23503';
            END IF;
            IF reviewed_source.status <> 'APPROVED'
               OR reviewed_source.enabled IS NOT TRUE
               OR reviewed_source.storage_allowed IS NOT TRUE
               OR (
                    reviewed_source.source_type <> 'MANUAL'
                    AND reviewed_source.automation_allowed IS NOT TRUE
               ) THEN
                RAISE EXCEPTION 'team intelligence capture source policy rejected: %',
                    reviewed_source.source_name
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """)
    op.execute("""
        CREATE TRIGGER trg_team_intelligence_capture_receipts_source_policy
        BEFORE INSERT ON team_intelligence_capture_receipts
        FOR EACH ROW EXECUTE FUNCTION team_intelligence_enforce_capture_source_policy()
        """)

    op.execute("""
        CREATE FUNCTION team_intelligence_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table % rejects %', TG_TABLE_NAME, TG_OP
                USING ERRCODE = '55000';
        END;
        $$
        """)
    for table in _IMMUTABLE_TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_reject_change
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION team_intelligence_reject_mutation()
            """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_reject_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION team_intelligence_reject_mutation()
            """)


def upgrade() -> None:
    _create_source_registry()
    _create_evidence_tables()
    _create_policy_and_append_only_triggers()


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_change ON {table}")
    for table in reversed(("team_intelligence_raw_payloads", "team_intelligence_observations")):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_source_policy ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_team_intelligence_capture_receipts_source_policy "
        "ON team_intelligence_capture_receipts"
    )

    op.drop_table("team_intelligence_observations")
    op.drop_table("team_intelligence_capture_receipts")
    op.drop_table("team_intelligence_raw_payloads")
    op.drop_table("team_intelligence_source_registry")
    op.execute("DROP FUNCTION IF EXISTS team_intelligence_reject_mutation()")
    op.execute("DROP FUNCTION IF EXISTS team_intelligence_enforce_capture_source_policy()")
    op.execute("DROP FUNCTION IF EXISTS team_intelligence_enforce_source_policy()")
