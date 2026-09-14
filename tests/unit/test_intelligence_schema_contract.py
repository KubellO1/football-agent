from pathlib import Path

from sqlalchemy import UniqueConstraint

from app.repositories.sqlalchemy.models import (
    FixtureIdentityMappingORM,
    IntelligenceObservationORM,
    IntelligenceRawPayloadORM,
    IntelligenceSourceORM,
    TeamIdentityMappingORM,
    VenueMappingORM,
)


def _unique_columns(model: type, name: str) -> list[str]:
    constraint = next(
        constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name == name
    )
    return [column.name for column in constraint.columns]


def test_observation_foundation_declares_stable_natural_keys() -> None:
    assert _unique_columns(IntelligenceSourceORM, "uq_intelligence_sources_code") == ["code"]
    assert _unique_columns(
        IntelligenceRawPayloadORM, "uq_intelligence_raw_payloads_natural_key"
    ) == ["natural_key"]
    assert _unique_columns(IntelligenceObservationORM, "uq_intelligence_observations_key") == [
        "observation_key"
    ]
    assert _unique_columns(TeamIdentityMappingORM, "uq_team_identity_mappings_key") == [
        "mapping_key"
    ]
    assert _unique_columns(FixtureIdentityMappingORM, "uq_fixture_identity_mappings_key") == [
        "mapping_key"
    ]
    assert _unique_columns(VenueMappingORM, "uq_venue_mappings_key") == ["mapping_key"]


def test_0022_migration_installs_append_only_guards() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0022_create_intelligence_observation_foundation.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0022"' in migration
    assert 'down_revision: str | None = "0021"' in migration
    assert "CREATE FUNCTION reject_intelligence_mutation()" in migration
    for table in (
        "intelligence_raw_payloads",
        "intelligence_observations",
        "team_identity_mappings",
        "fixture_identity_mappings",
        "venue_mappings",
    ):
        assert f'"{table}"' in migration
