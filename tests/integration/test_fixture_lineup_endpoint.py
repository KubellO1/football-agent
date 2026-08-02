"""比赛官方阵容同步端点的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_fixture_lineup_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.models.entities.enums import PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.player import Player
from app.models.value_objects.decision import EvidenceLevel
from app.providers.interfaces.fixture_lineup_provider import FixtureLineupProvider
from app.providers.schemas.fixture_lineup import (
    ProviderFixtureLineupBatch,
    ProviderLineupPlayer,
    ProviderTeamLineup,
)
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.lineup_repository import SqlAlchemyLineupRepository
from app.repositories.sqlalchemy.models import CompetitionORM, TeamORM
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyTeamRepository
from app.services.fixture_lineup_ingestion import FixtureLineupIngestionService

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE = "api-football"
FIXTURE_EXTERNAL_ID = "fixture-1"
HOME_EXTERNAL_ID = "home-1"
AWAY_EXTERNAL_ID = "away-1"
CAPTURED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class _FakeFixtureLineupProvider(FixtureLineupProvider):
    def __init__(self, batch: ProviderFixtureLineupBatch) -> None:
        self._batch = batch
        self.fixture_external_ids: list[str] = []

    async def get_fixture_lineups(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderFixtureLineupBatch:
        self.fixture_external_ids.append(fixture_external_id)
        return self._batch


async def _persist_players(
    session: AsyncSession,
    team_id: UUID,
    *,
    prefix: str,
) -> list[Player]:
    repository = SqlAlchemyPlayerRepository(session)
    return [
        await repository.add(
            Player(
                name=f"{prefix} Player {index}",
                position=(PlayerPosition.GOALKEEPER if index == 0 else PlayerPosition.DEFENDER),
                team_id=team_id,
                external_source=SOURCE,
                external_id=f"{prefix}-{index}",
            ),
        )
        for index in range(13)
    ]


def _team_lineup(team_external_id: str, players: list[Player]) -> ProviderTeamLineup:
    records = [
        ProviderLineupPlayer(
            player_external_id=player.external_id or "",
            player_name=player.name,
            raw_position=player.position.value,
        )
        for player in players
    ]
    return ProviderTeamLineup(
        team_external_id=team_external_id,
        formation="4-2-3-1",
        starting=records[:11],
        substitutes=records[11:],
    )


@pytest.mark.integration
async def test_fixture_lineup_endpoint_persists_idempotent_snapshots(
    db_session: AsyncSession,
) -> None:
    competition_id, home_team_id, away_team_id = uuid4(), uuid4(), uuid4()
    db_session.add_all(
        [
            CompetitionORM(id=competition_id, name="Test Competition", country="Test Country"),
            TeamORM(
                id=home_team_id,
                name="Home Team",
                external_source=SOURCE,
                external_id=HOME_EXTERNAL_ID,
            ),
            TeamORM(
                id=away_team_id,
                name="Away Team",
                external_source=SOURCE,
                external_id=AWAY_EXTERNAL_ID,
            ),
        ],
    )
    await db_session.flush()
    fixture = await SqlAlchemyFixtureRepository(db_session).add(
        Fixture(
            competition_id=competition_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff=datetime(2026, 8, 2, 18, 0, tzinfo=UTC),
            external_source=SOURCE,
            external_id=FIXTURE_EXTERNAL_ID,
        ),
    )
    home_players = await _persist_players(db_session, home_team_id, prefix="home")
    away_players = await _persist_players(db_session, away_team_id, prefix="away")
    batch = ProviderFixtureLineupBatch(
        source=SOURCE,
        fixture_external_id=FIXTURE_EXTERNAL_ID,
        captured_at=CAPTURED_AT,
        response_complete=True,
        lineups=[
            _team_lineup(HOME_EXTERNAL_ID, home_players),
            _team_lineup(AWAY_EXTERNAL_ID, away_players),
        ],
        request_reference=f"/fixtures/lineups?fixture={FIXTURE_EXTERNAL_ID}",
    )
    provider = _FakeFixtureLineupProvider(batch)
    service = FixtureLineupIngestionService(
        provider=provider,
        fixtures=SqlAlchemyFixtureRepository(db_session),
        teams=SqlAlchemyTeamRepository(db_session),
        players=SqlAlchemyPlayerRepository(db_session),
        lineups=SqlAlchemyLineupRepository(db_session),
        source=SOURCE,
        evidence_level=EvidenceLevel.B,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_sync_token="expected-token",
    )
    app.dependency_overrides[get_fixture_lineup_ingestion_service] = lambda: service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            f"/internal/sync/fixture-lineups/{FIXTURE_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )
        second = await client.post(
            f"/internal/sync/fixture-lineups/{FIXTURE_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )

    assert first.status_code == 200
    assert first.json()["lineups_created"] == 2
    assert first.json()["players_received"] == 26
    assert second.status_code == 200
    assert second.json()["lineups_created"] == 0
    assert second.json()["lineups_unchanged"] == 2
    assert provider.fixture_external_ids == [FIXTURE_EXTERNAL_ID, FIXTURE_EXTERNAL_ID]
    assert len(await SqlAlchemyLineupRepository(db_session).list_by_fixture(fixture.id)) == 2
