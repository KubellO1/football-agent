"""create append-only intelligence observation foundation

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = (
    "'OPEN_DATA', 'FREE_OFFICIAL_API', 'FREE_PUBLIC_DOWNLOAD', "
    "'PUBLIC_WEB_AUTOMATION_ALLOWED', 'MANUAL_USER_SUPPLIED', "
    "'RESEARCH_ONLY', 'REJECTED'"
)
STATUS = "'MATCHED', 'UNMAPPED', 'AMBIGUOUS', 'WEATHER_UNAVAILABLE', 'REJECTED'"
METHOD = "'EXACT_EXTERNAL_ID', 'EXACT_CANONICAL_NAME', 'MANUAL_REVIEWED', 'NONE'"


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _append_only(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_intelligence_mutation()"
    )


def upgrade() -> None:
    op.create_table(
        "intelligence_sources",
        _id(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("source_policy", sa.String(40), nullable=False),
        sa.Column("automation_allowed", sa.Boolean(), nullable=False),
        sa.Column("license_reference", sa.String(500), nullable=False),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"source_policy IN ({POLICY})", name="ck_intelligence_sources_policy"),
        sa.UniqueConstraint("code", name="uq_intelligence_sources_code"),
    )
    op.create_table(
        "intelligence_raw_payloads",
        _id(),
        sa.Column("natural_key", sa.String(64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False),
        sa.Column("etag", sa.String(500), nullable=True),
        sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_policy", sa.String(40), nullable=False),
        _created_at(),
        sa.CheckConstraint("byte_length >= 0", name="ck_intelligence_raw_payloads_byte_length"),
        sa.CheckConstraint(
            "raw_payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_intelligence_raw_payloads_sha256",
        ),
        sa.CheckConstraint(
            f"source_policy IN ({POLICY})", name="ck_intelligence_raw_payloads_policy"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["intelligence_sources.id"],
            name="fk_intelligence_raw_payloads_source",
        ),
        sa.UniqueConstraint("natural_key", name="uq_intelligence_raw_payloads_natural_key"),
    )
    op.create_index(
        "ix_intelligence_raw_payloads_hash",
        "intelligence_raw_payloads",
        ["raw_payload_hash"],
    )
    op.create_index(
        "ix_intelligence_raw_payloads_source_captured",
        "intelligence_raw_payloads",
        ["source_id", "captured_at"],
    )
    op.create_table(
        "intelligence_observations",
        _id(),
        sa.Column("observation_key", sa.String(64), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("source_record_id", sa.String(256), nullable=True),
        sa.Column("data_type", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("source_policy", sa.String(40), nullable=False),
        sa.Column("semantic_status", sa.String(40), nullable=False),
        sa.Column("capture_window", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "raw_payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_intelligence_observations_sha256",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at <= captured_at",
            name="ck_intelligence_observations_published_at",
        ),
        sa.CheckConstraint(
            "semantic_status IN ('VERIFIED', 'UNMAPPED', 'AMBIGUOUS', "
            "'WEATHER_UNAVAILABLE', 'REJECTED')",
            name="ck_intelligence_observations_semantic_status",
        ),
        sa.CheckConstraint(
            f"source_policy IN ({POLICY})", name="ck_intelligence_observations_policy"
        ),
        sa.CheckConstraint(
            "capture_window IN ('DAILY', 'T-24H', 'T-6H', 'T-90', 'T-60', " "'T-30', 'POST_MATCH')",
            name="ck_intelligence_observations_capture_window",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"], ["fixtures.id"], name="fk_intelligence_observations_fixture"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["intelligence_raw_payloads.id"],
            name="fk_intelligence_observations_raw_payload",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["intelligence_sources.id"],
            name="fk_intelligence_observations_source",
        ),
        sa.UniqueConstraint("observation_key", name="uq_intelligence_observations_key"),
    )
    op.create_index(
        "ix_intelligence_observations_fixture_type_captured",
        "intelligence_observations",
        ["fixture_id", "data_type", "captured_at"],
    )
    op.create_index(
        "ix_intelligence_observations_source_type_captured",
        "intelligence_observations",
        ["source_id", "data_type", "captured_at"],
    )

    op.create_table(
        "team_identity_mappings",
        _id(),
        sa.Column("mapping_key", sa.String(64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_team_id", sa.String(256), nullable=True),
        sa.Column("source_team_name", sa.String(160), nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.CheckConstraint(f"status IN ({STATUS})", name="ck_team_identity_mappings_status"),
        sa.CheckConstraint(f"method IN ({METHOD})", name="ck_team_identity_mappings_method"),
        sa.CheckConstraint(
            "(status = 'MATCHED' AND team_id IS NOT NULL) OR "
            "(status <> 'MATCHED' AND team_id IS NULL)",
            name="ck_team_identity_mappings_target",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"], ["competitions.id"], name="fk_team_identity_mappings_competition"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["intelligence_raw_payloads.id"],
            name="fk_team_identity_mappings_raw_payload",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["intelligence_sources.id"], name="fk_team_identity_mappings_source"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_team_identity_mappings_team"),
        sa.UniqueConstraint("mapping_key", name="uq_team_identity_mappings_key"),
    )
    op.create_index(
        "ix_team_identity_mappings_source_name",
        "team_identity_mappings",
        ["source_id", "source_team_name"],
    )
    op.create_table(
        "fixture_identity_mappings",
        _id(),
        sa.Column("mapping_key", sa.String(64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_fixture_id", sa.String(256), nullable=False),
        sa.Column("source_competition", sa.String(160), nullable=False),
        sa.Column("source_home_team", sa.String(160), nullable=False),
        sa.Column("source_away_team", sa.String(160), nullable=False),
        sa.Column("source_kickoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.CheckConstraint(f"status IN ({STATUS})", name="ck_fixture_identity_mappings_status"),
        sa.CheckConstraint(f"method IN ({METHOD})", name="ck_fixture_identity_mappings_method"),
        sa.CheckConstraint(
            "(status = 'MATCHED' AND fixture_id IS NOT NULL) OR "
            "(status <> 'MATCHED' AND fixture_id IS NULL)",
            name="ck_fixture_identity_mappings_target",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"], ["fixtures.id"], name="fk_fixture_identity_mappings_fixture"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["intelligence_raw_payloads.id"],
            name="fk_fixture_identity_mappings_raw_payload",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["intelligence_sources.id"], name="fk_fixture_identity_mappings_source"
        ),
        sa.UniqueConstraint("mapping_key", name="uq_fixture_identity_mappings_key"),
    )
    op.create_index(
        "ix_fixture_identity_mappings_source_fixture",
        "fixture_identity_mappings",
        ["source_id", "source_fixture_id"],
    )
    op.create_table(
        "venue_mappings",
        _id(),
        sa.Column("mapping_key", sa.String(64), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_venue_name", sa.String(200), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("coordinate_source", sa.String(120), nullable=True),
        sa.Column("coordinate_source_url", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.CheckConstraint(f"status IN ({STATUS})", name="ck_venue_mappings_status"),
        sa.CheckConstraint(f"method IN ({METHOD})", name="ck_venue_mappings_method"),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="ck_venue_mappings_coordinate_pair"
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_venue_mappings_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_venue_mappings_longitude",
        ),
        sa.CheckConstraint(
            "status <> 'MATCHED' OR "
            "(latitude IS NOT NULL AND coordinate_source_url IS NOT NULL)",
            name="ck_venue_mappings_provenance",
        ),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], name="fk_venue_mappings_fixture"),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["intelligence_raw_payloads.id"],
            name="fk_venue_mappings_raw_payload",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["intelligence_sources.id"], name="fk_venue_mappings_source"
        ),
        sa.UniqueConstraint("mapping_key", name="uq_venue_mappings_key"),
    )
    op.create_index(
        "ix_venue_mappings_fixture_captured",
        "venue_mappings",
        ["fixture_id", "captured_at"],
    )
    op.execute("""
        CREATE FUNCTION reject_intelligence_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'intelligence records are append-only';
        END;
        $$ LANGUAGE plpgsql
        """)
    for table in (
        "intelligence_raw_payloads",
        "intelligence_observations",
        "team_identity_mappings",
        "fixture_identity_mappings",
        "venue_mappings",
    ):
        _append_only(table)


def downgrade() -> None:
    for table in (
        "venue_mappings",
        "fixture_identity_mappings",
        "team_identity_mappings",
        "intelligence_observations",
        "intelligence_raw_payloads",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_intelligence_mutation()")

    op.drop_index("ix_venue_mappings_fixture_captured", table_name="venue_mappings")
    op.drop_table("venue_mappings")
    op.drop_index(
        "ix_fixture_identity_mappings_source_fixture",
        table_name="fixture_identity_mappings",
    )
    op.drop_table("fixture_identity_mappings")
    op.drop_index("ix_team_identity_mappings_source_name", table_name="team_identity_mappings")
    op.drop_table("team_identity_mappings")
    op.drop_index(
        "ix_intelligence_observations_source_type_captured",
        table_name="intelligence_observations",
    )
    op.drop_index(
        "ix_intelligence_observations_fixture_type_captured",
        table_name="intelligence_observations",
    )
    op.drop_table("intelligence_observations")
    op.drop_index(
        "ix_intelligence_raw_payloads_source_captured",
        table_name="intelligence_raw_payloads",
    )
    op.drop_index("ix_intelligence_raw_payloads_hash", table_name="intelligence_raw_payloads")
    op.drop_table("intelligence_raw_payloads")
    op.drop_table("intelligence_sources")
