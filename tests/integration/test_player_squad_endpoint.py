"""球队阵容内部同步端点的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_player_squad_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer
from app.repositories.sqlalchemy.models import TeamORM
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyTeamRepository
from app.services.player_squad_ingestion import PlayerSquadIngestionService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE = "api-football"
TEAM_EXTERNAL_ID = "33"


class _FakePlayerSquadProvider(PlayerSquadProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def get_team_squad(
        self,
        *,
        team_external_id: str,
    ) -> ProviderSquadBatch:
        self.calls += 1
        return ProviderSquadBatch(
            source=SOURCE,
            team_external_id=team_external_id,
            captured_at=datetime(2026, 7, 31, 10, 0, tzinfo=UTC),
            response_complete=True,
            records=[
                ProviderSquadPlayer(
                    player_external_id="player-1",
                    player_name="Goalkeeper One",
                    raw_position="Goalkeeper",
                ),
                ProviderSquadPlayer(
                    player_external_id="player-2",
                    player_name="Forward Two",
                    raw_position="Attacker",
                ),
            ],
            request_reference="/players/squads?team=33",
        )


@pytest.mark.integration
async def test_protected_endpoint_persists_squad_idempotently(
    db_session: AsyncSession,
) -> None:
    team_id = uuid4()
    db_session.add(
        TeamORM(
            id=team_id,
            name="Test Team",
            external_source=SOURCE,
            external_id=TEAM_EXTERNAL_ID,
        ),
    )
    await db_session.flush()

    provider = _FakePlayerSquadProvider()
    service = PlayerSquadIngestionService(
        provider=provider,
        teams=SqlAlchemyTeamRepository(db_session),
        players=SqlAlchemyPlayerRepository(db_session),
        source=SOURCE,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_sync_token="expected-token",
    )
    app.dependency_overrides[get_player_squad_ingestion_service] = lambda: service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            f"/internal/sync/player-squads/{TEAM_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )
        second = await client.post(
            f"/internal/sync/player-squads/{TEAM_EXTERNAL_ID}",
            headers={"X-Internal-Sync-Token": "expected-token"},
        )

    assert first.status_code == 200
    assert first.json() == {
        "source": SOURCE,
        "team_external_id": TEAM_EXTERNAL_ID,
        "records_received": 2,
        "records_created": 2,
        "records_updated": 0,
        "records_unchanged": 0,
    }
    assert second.status_code == 200
    assert second.json()["records_created"] == 0
    assert second.json()["records_unchanged"] == 2
    assert provider.calls == 2

    players = await SqlAlchemyPlayerRepository(db_session).list_by_team(team_id)
    assert {player.external_id for player in players} == {"player-1", "player-2"}
