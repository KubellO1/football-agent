"""赔率快照仓储的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.odds import Odds
from app.repositories.sqlalchemy.models import BookmakerORM
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture

LOW_BOOKMAKER_ID = UUID("00000000-0000-0000-0000-000000000001")
HIGH_BOOKMAKER_ID = UUID("00000000-0000-0000-0000-000000000002")


async def _add_bookmakers(session: AsyncSession) -> None:
    session.add_all(
        [
            BookmakerORM(id=LOW_BOOKMAKER_ID, name="Alpha"),
            BookmakerORM(id=HIGH_BOOKMAKER_ID, name="Beta"),
        ]
    )
    await session.flush()


def _snapshot(
    fixture_id: UUID,
    bookmaker_id: UUID,
    captured_at: datetime,
    *,
    market: MarketType = MarketType.MATCH_RESULT,
    code: str = "home",
    line: float | None = None,
) -> OddsSnapshot:
    return OddsSnapshot(
        fixture_id=fixture_id,
        bookmaker_id=bookmaker_id,
        selection=Selection(market=market, code=code, line=line),
        odds=Odds(decimal=Decimal("2.10")),
        captured_at=captured_at,
    )


@pytest.mark.integration
async def test_list_by_fixture_as_of_includes_boundary_and_excludes_future(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    await _add_bookmakers(db_session)
    repo = SqlAlchemyOddsSnapshotRepository(db_session)
    boundary = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    before = boundary - timedelta(minutes=1)
    after = boundary + timedelta(microseconds=1)

    await repo.add(_snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, after))
    await repo.add(_snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, before))
    await repo.add(_snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, boundary))

    rows = await repo.list_by_fixture(persisted_fixture.id, as_of=boundary)

    assert [row.captured_at for row in rows] == [before, boundary]


@pytest.mark.integration
async def test_list_by_fixture_without_as_of_preserves_all_snapshots(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    await _add_bookmakers(db_session)
    repo = SqlAlchemyOddsSnapshotRepository(db_session)
    base = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    await repo.add(_snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, base))
    await repo.add(
        _snapshot(
            persisted_fixture.id,
            LOW_BOOKMAKER_ID,
            base + timedelta(minutes=1),
        )
    )

    rows = await repo.list_by_fixture(persisted_fixture.id)

    assert len(rows) == 2


@pytest.mark.integration
async def test_list_by_fixture_has_deterministic_order(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    await _add_bookmakers(db_session)
    repo = SqlAlchemyOddsSnapshotRepository(db_session)
    captured_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    snapshots = [
        _snapshot(persisted_fixture.id, HIGH_BOOKMAKER_ID, captured_at, code="home"),
        _snapshot(
            persisted_fixture.id,
            LOW_BOOKMAKER_ID,
            captured_at,
            market=MarketType.OVER_UNDER,
            code="over",
            line=3.5,
        ),
        _snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, captured_at, code="home"),
        _snapshot(persisted_fixture.id, LOW_BOOKMAKER_ID, captured_at, code="away"),
        _snapshot(
            persisted_fixture.id,
            LOW_BOOKMAKER_ID,
            captured_at,
            market=MarketType.OVER_UNDER,
            code="over",
            line=2.5,
        ),
    ]
    for snapshot in reversed(snapshots):
        await repo.add(snapshot)

    rows = await repo.list_by_fixture(persisted_fixture.id)

    assert [
        (
            row.bookmaker_id,
            row.selection.market,
            row.selection.code,
            row.selection.line,
        )
        for row in rows
    ] == [
        (LOW_BOOKMAKER_ID, MarketType.MATCH_RESULT, "away", None),
        (LOW_BOOKMAKER_ID, MarketType.MATCH_RESULT, "home", None),
        (LOW_BOOKMAKER_ID, MarketType.OVER_UNDER, "over", 2.5),
        (LOW_BOOKMAKER_ID, MarketType.OVER_UNDER, "over", 3.5),
        (HIGH_BOOKMAKER_ID, MarketType.MATCH_RESULT, "home", None),
    ]


@pytest.mark.integration
async def test_list_by_fixture_rejects_naive_as_of(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyOddsSnapshotRepository(db_session)

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await repo.list_by_fixture(
            persisted_fixture.id,
            as_of=datetime(2026, 7, 29, 12, 0),
        )
