"""球队单场统计快照仓储的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.value_objects.statistics import TeamMatchMetrics
from app.repositories.sqlalchemy.team_match_statistics_repository import (
    SqlAlchemyTeamMatchStatisticsRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture


def _snapshot(
    fixture_id: UUID,
    team_id: UUID,
    captured_at: datetime,
    *,
    source: str = "api-football",
    xg: float | None = 1.4,
) -> TeamMatchStatistics:
    return TeamMatchStatistics(
        fixture_id=fixture_id,
        team_id=team_id,
        source=source,
        captured_at=captured_at,
        source_updated_at=captured_at - timedelta(minutes=1),
        metrics=TeamMatchMetrics(
            xg=xg,
            xg_against=0.8 if xg is not None else None,
            shots=12 if xg is not None else None,
            shots_on_target=5 if xg is not None else None,
            possession_percentage=54.2 if xg is not None else None,
            ppda=9.7 if xg is not None else None,
            big_chances=3 if xg is not None else None,
            goalkeeper_saves=2 if xg is not None else None,
            set_piece_shots=3 if xg is not None else None,
            headed_shots=2 if xg is not None else None,
            conversion_rate=0.16 if xg is not None else None,
        ),
        is_final=True,
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip_preserves_metrics(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyTeamMatchStatisticsRepository(db_session)
    captured_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    entity = _snapshot(persisted_fixture.id, reference_ids[1], captured_at)

    saved = await repo.add(entity)
    loaded = await repo.get(saved.id)

    assert loaded is not None
    assert loaded.id == entity.id
    assert loaded.fixture_id == persisted_fixture.id
    assert loaded.team_id == reference_ids[1]
    assert loaded.source == "api-football"
    assert loaded.metrics == entity.metrics
    assert loaded.source_updated_at == entity.source_updated_at
    assert loaded.is_final is True


@pytest.mark.integration
async def test_roundtrip_preserves_unknown_metrics_as_none(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyTeamMatchStatisticsRepository(db_session)
    entity = _snapshot(
        persisted_fixture.id,
        reference_ids[1],
        datetime(2026, 7, 28, 18, 0, tzinfo=UTC),
        xg=None,
    )

    loaded = await repo.get((await repo.add(entity)).id)

    assert loaded is not None
    assert loaded.metrics == TeamMatchMetrics()


@pytest.mark.integration
async def test_add_if_absent_uses_natural_key(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyTeamMatchStatisticsRepository(db_session)
    captured_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    first = _snapshot(persisted_fixture.id, reference_ids[1], captured_at)
    duplicate = _snapshot(persisted_fixture.id, reference_ids[1], captured_at)
    duplicate.id = uuid4()

    assert await repo.add_if_absent(first) is True
    assert await repo.add_if_absent(duplicate) is False
    assert len(await repo.list_by_fixture(persisted_fixture.id)) == 1


@pytest.mark.integration
async def test_list_by_fixture_filters_source_team_and_as_of(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyTeamMatchStatisticsRepository(db_session)
    home_id, away_id = reference_ids[1], reference_ids[2]
    base = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    await repo.add(_snapshot(persisted_fixture.id, home_id, base))
    await repo.add(_snapshot(persisted_fixture.id, home_id, base + timedelta(minutes=5)))
    await repo.add(
        _snapshot(
            persisted_fixture.id,
            home_id,
            base + timedelta(minutes=2),
            source="sportmonks",
        )
    )
    await repo.add(_snapshot(persisted_fixture.id, away_id, base + timedelta(minutes=1)))

    rows = await repo.list_by_fixture(
        persisted_fixture.id,
        team_id=home_id,
        source="api-football",
        as_of=base + timedelta(minutes=3),
    )

    assert [row.captured_at for row in rows] == [base]


@pytest.mark.integration
async def test_get_latest_by_source_respects_as_of(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyTeamMatchStatisticsRepository(db_session)
    team_id = reference_ids[1]
    base = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    await repo.add(_snapshot(persisted_fixture.id, team_id, base, xg=1.0))
    await repo.add(
        _snapshot(
            persisted_fixture.id,
            team_id,
            base + timedelta(minutes=10),
            xg=1.8,
        )
    )

    historical = await repo.get_latest_by_source(
        persisted_fixture.id,
        team_id,
        "api-football",
        as_of=base + timedelta(minutes=5),
    )
    latest = await repo.get_latest_by_source(
        persisted_fixture.id,
        team_id,
        "api-football",
    )

    assert historical is not None
    assert historical.metrics.xg == 1.0
    assert latest is not None
    assert latest.metrics.xg == 1.8
