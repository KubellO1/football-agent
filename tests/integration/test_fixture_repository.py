"""SqlAlchemyFixtureRepository 的集成测试（需真实 Postgres）。

覆盖：add + get 往返、按开赛时间窗查询。使用 reference_ids 满足 fixtures 的
外键约束（competitions/teams）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.value_objects.score import MatchResult, Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _fixture(kickoff: datetime, ids: tuple[UUID, UUID, UUID], *, finished: bool = False) -> Fixture:
    competition_id, home_team_id, away_team_id = ids
    return Fixture(
        competition_id=competition_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        kickoff=kickoff,
        status=MatchStatus.FINISHED if finished else MatchStatus.SCHEDULED,
        score=Score(home=2, away=1) if finished else None,
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    fixture = _fixture(datetime(2026, 7, 2, 18, 0, tzinfo=UTC), reference_ids, finished=True)

    saved = await repo.add(fixture)
    got = await repo.get(saved.id)

    assert got is not None
    assert got.id == fixture.id
    assert got.status is MatchStatus.FINISHED
    assert got.score is not None
    assert (got.score.home, got.score.away) == (2, 1)
    assert got.result is MatchResult.HOME


@pytest.mark.integration
async def test_scheduled_fixture_has_no_score(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    saved = await repo.add(_fixture(datetime(2026, 7, 3, 18, 0, tzinfo=UTC), reference_ids))
    got = await repo.get(saved.id)

    assert got is not None
    assert got.score is None
    assert got.result is None


@pytest.mark.integration
async def test_list_by_kickoff_window_filters_and_orders(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    base = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    await repo.add(_fixture(base - timedelta(days=1), reference_ids))
    await repo.add(_fixture(base + timedelta(hours=5), reference_ids))
    await repo.add(_fixture(base + timedelta(hours=1), reference_ids))
    await repo.add(_fixture(base + timedelta(days=2), reference_ids))

    result = await repo.list_by_kickoff_window(base, base + timedelta(hours=12))

    assert len(result) == 2
    assert result[0].kickoff < result[1].kickoff


@pytest.mark.integration
async def test_update_changes_only_mutable_match_state(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    original = _fixture(datetime(2026, 7, 2, 18, 0, tzinfo=UTC), reference_ids)
    original.external_source = "test-provider"
    original.external_id = "fixture-1"
    saved = await repo.add(original)

    changed = Fixture(
        id=saved.id,
        competition_id=saved.competition_id,
        season_id=saved.season_id,
        home_team_id=saved.home_team_id,
        away_team_id=saved.away_team_id,
        kickoff=saved.kickoff + timedelta(hours=1),
        status=MatchStatus.FINISHED,
        score=Score(home=3, away=1),
        external_source=saved.external_source,
        external_id=saved.external_id,
    )

    updated = await repo.update(changed)

    assert updated.kickoff == changed.kickoff
    assert updated.status is MatchStatus.FINISHED
    assert updated.score == Score(home=3, away=1)
    assert updated.competition_id == saved.competition_id
    assert updated.season_id == saved.season_id
    assert updated.home_team_id == saved.home_team_id
    assert updated.away_team_id == saved.away_team_id
    assert updated.external_source == saved.external_source
    assert updated.external_id == saved.external_id


@pytest.mark.integration
async def test_update_rejects_fixture_identity_changes(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    original = _fixture(datetime(2026, 7, 2, 18, 0, tzinfo=UTC), reference_ids)
    original.external_source = "test-provider"
    original.external_id = "fixture-1"
    saved = await repo.add(original)

    conflicting = Fixture(
        id=saved.id,
        competition_id=uuid4(),
        season_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=saved.kickoff,
        external_source="other-provider",
        external_id="other-fixture",
    )

    with pytest.raises(ValueError) as exc_info:
        await repo.update(conflicting)

    message = str(exc_info.value)
    assert "competition_id" in message
    assert "season_id" in message
    assert "home_team_id" in message
    assert "away_team_id" in message
    assert "external_source" in message
    assert "external_id" in message

    unchanged = await repo.get(saved.id)
    assert unchanged is not None
    assert unchanged.competition_id == saved.competition_id
    assert unchanged.season_id == saved.season_id
    assert unchanged.home_team_id == saved.home_team_id
    assert unchanged.away_team_id == saved.away_team_id
    assert unchanged.external_source == saved.external_source
    assert unchanged.external_id == saved.external_id
