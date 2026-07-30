"""球员可用性观察仓储的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.models.entities.player_availability import PlayerAvailabilityObservation
from app.models.value_objects.availability import AvailabilitySource, AvailabilityStatus
from app.models.value_objects.decision import EvidenceLevel
from app.repositories.sqlalchemy.player_availability_repository import (
    SqlAlchemyPlayerAvailabilityObservationRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture


def _observation(
    fixture_id: UUID,
    team_id: UUID,
    player_id: UUID,
    captured_at: datetime,
    *,
    source: str = "official-club",
    status: AvailabilityStatus = AvailabilityStatus.OUT,
    evidence_level: EvidenceLevel = EvidenceLevel.A,
) -> PlayerAvailabilityObservation:
    return PlayerAvailabilityObservation(
        fixture_id=fixture_id,
        team_id=team_id,
        player_id=player_id,
        status=status,
        source=AvailabilitySource(
            name=source,
            evidence_level=evidence_level,
            reference="https://example.com/team-news",
        ),
        captured_at=captured_at,
        source_updated_at=captured_at - timedelta(minutes=2),
        reason="hamstring injury",
        expected_return=date(2026, 8, 15),
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip_preserves_observation(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    entity = _observation(
        persisted_fixture.id,
        reference_ids[1],
        uuid4(),
        datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
    )

    loaded = await repository.get((await repository.add(entity)).id)

    assert loaded is not None
    assert loaded.id == entity.id
    assert loaded.fixture_id == persisted_fixture.id
    assert loaded.team_id == reference_ids[1]
    assert loaded.player_id == entity.player_id
    assert loaded.status is AvailabilityStatus.OUT
    assert loaded.source == entity.source
    assert loaded.source_updated_at == entity.source_updated_at
    assert loaded.reason == "hamstring injury"
    assert loaded.expected_return == date(2026, 8, 15)


@pytest.mark.integration
async def test_roundtrip_preserves_unknown_as_unknown(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    entity = _observation(
        persisted_fixture.id,
        reference_ids[1],
        uuid4(),
        datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        status=AvailabilityStatus.UNKNOWN,
        evidence_level=EvidenceLevel.E,
    )

    loaded = await repository.get((await repository.add(entity)).id)

    assert loaded is not None
    assert loaded.status is AvailabilityStatus.UNKNOWN
    assert loaded.has_known_status is False
    assert loaded.rules_player_out is False


@pytest.mark.integration
async def test_add_if_absent_uses_natural_key(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    player_id = uuid4()
    captured_at = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    first = _observation(
        persisted_fixture.id,
        reference_ids[1],
        player_id,
        captured_at,
    )
    duplicate = _observation(
        persisted_fixture.id,
        reference_ids[1],
        player_id,
        captured_at,
    )

    assert await repository.add_if_absent(first) is True
    assert await repository.add_if_absent(duplicate) is False
    assert len(await repository.list_by_fixture(persisted_fixture.id)) == 1


@pytest.mark.integration
async def test_list_by_fixture_filters_all_boundaries(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    home_id, away_id = reference_ids[1], reference_ids[2]
    target_player, other_player = uuid4(), uuid4()
    base = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    await repository.add(
        _observation(persisted_fixture.id, home_id, target_player, base),
    )
    await repository.add(
        _observation(
            persisted_fixture.id,
            home_id,
            target_player,
            base + timedelta(minutes=10),
        ),
    )
    await repository.add(
        _observation(
            persisted_fixture.id,
            home_id,
            target_player,
            base + timedelta(minutes=2),
            source="professional-stats",
            evidence_level=EvidenceLevel.B,
        ),
    )
    await repository.add(
        _observation(
            persisted_fixture.id,
            away_id,
            other_player,
            base + timedelta(minutes=1),
        ),
    )

    rows = await repository.list_by_fixture(
        persisted_fixture.id,
        team_id=home_id,
        player_id=target_player,
        source="official-club",
        as_of=base + timedelta(minutes=5),
    )

    assert [row.captured_at for row in rows] == [base]


@pytest.mark.integration
async def test_get_latest_by_source_respects_as_of(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    player_id = uuid4()
    base = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    await repository.add(
        _observation(
            persisted_fixture.id,
            reference_ids[1],
            player_id,
            base,
            status=AvailabilityStatus.DOUBTFUL,
        ),
    )
    await repository.add(
        _observation(
            persisted_fixture.id,
            reference_ids[1],
            player_id,
            base + timedelta(minutes=10),
            status=AvailabilityStatus.OUT,
        ),
    )

    historical = await repository.get_latest_by_source(
        persisted_fixture.id,
        player_id,
        "official-club",
        as_of=base + timedelta(minutes=5),
    )
    latest = await repository.get_latest_by_source(
        persisted_fixture.id,
        player_id,
        "official-club",
    )

    assert historical is not None
    assert historical.status is AvailabilityStatus.DOUBTFUL
    assert latest is not None
    assert latest.status is AvailabilityStatus.OUT


@pytest.mark.integration
async def test_queries_reject_naive_as_of(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    repository = SqlAlchemyPlayerAvailabilityObservationRepository(db_session)
    naive = datetime(2026, 7, 31, 12, 0)

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await repository.list_by_fixture(persisted_fixture.id, as_of=naive)
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await repository.get_latest_by_source(
            persisted_fixture.id,
            uuid4(),
            "official-club",
            as_of=naive,
        )
