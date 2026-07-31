"""比赛级阵容同步 endpoint 的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_fixture_squad_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.models.entities.fixture import Fixture
from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import CompetitionORM, TeamORM
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyTeamRepository
from app.services.fixture_squad_ingestion import FixtureSquadIngestionService
from app.services.player_squad_ingestion import PlayerSquadIngestionService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE = "api-football"
FIXTURE_EXTERNAL_ID = "fixture-1"
HOME_EXTERNAL_ID = "home-1"
AWAY_EXTERNAL_ID = "away-1"


class _FakePlayerSquadProvider(PlayerSquadProvider):
    def __init__(self) -> None:
        self.team_external_ids: list[str] = []

    async def get_team_squad(
        self,
        *,
        team_external_id: str,
    ) -> ProviderSquadBatch:
        self.team_external_ids.append(team_external_id)
        return ProviderSquadBatch(
            source=SOURCE,
            team_external_id=team_external_id,
            captured_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            response_complete=True,
            records=[
                ProviderSquadPlayer(
                    player_external_id=f"{team_external_id}-player",
                    player_name=f"{team_external_id} Player",
                    raw_position="Midfielder",
                ),
            ],
            request_reference=f"/players/squads?team={team_external_id}",
        )


@pytest.mark.integration
async def test_protected_fixture_endpoint_persists_both_squads_idempotently(
    db_session: AsyncSession,
) -> None:
    competition_id = uuid4()
    home_team_id = uuid4()
    away_team_id = uuid4()
    db_session.add_all(
        [
            CompetitionORM(
                id=competition_id,
                name="Test Competition",
                country="Test Country",
            ),
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
    await SqlAlchemyFixtureRepository(db_session).add(
        Fixture(
            competition_id=competition_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff=datetime(2026, 8, 2, 18, 0, tzinfo=UTC),
            external_source=SOURCE,
            external_id=FIXTURE_EXTERNAL_ID,
        ),
    )

    provider = _FakePlayerSquadProvider()
    teams = SqlAlchemyTeamRepository(db_session)
    squad_service = PlayerSquadIngestionService(
        provider=provider,
        teams=teams,
        players=SqlAlchemyPlayerRepository(db_session),
        source=SOURCE,
    )
    fixture_service = FixtureSquadIngestionService(
        fixtures=SqlAlchemyFixtureRepository(db_session),
        teams=teams,
        squads=squad_service,
        source=SOURCE,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_sync_token="expected-token",
    )
    app.dependency_overrides[get_fixture_squad_ingestion_service] = lambda: fixture_service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            f"/internal/sync/fixture-squads/{FIXTURE_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )
        second = await client.post(
            f"/internal/sync/fixture-squads/{FIXTURE_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )

    assert first.status_code == 200
    assert first.json()["records_received"] == 2
    assert first.json()["records_created"] == 2
    assert first.json()["home_team"]["team_external_id"] == HOME_EXTERNAL_ID
    assert first.json()["away_team"]["team_external_id"] == AWAY_EXTERNAL_ID
    assert second.status_code == 200
    assert second.json()["records_created"] == 0
    assert second.json()["records_unchanged"] == 2
    assert provider.team_external_ids == [
        HOME_EXTERNAL_ID,
        AWAY_EXTERNAL_ID,
        HOME_EXTERNAL_ID,
        AWAY_EXTERNAL_ID,
    ]

    players = SqlAlchemyPlayerRepository(db_session)
    home_players = await players.list_by_team(home_team_id)
    away_players = await players.list_by_team(away_team_id)
    assert [player.external_id for player in home_players] == ["home-1-player"]
    assert [player.external_id for player in away_players] == ["away-1-player"]
