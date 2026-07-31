"""球队阵容 Provider、DI 与内部同步端点的接线测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_player_squad_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.providers import build_player_squad_provider
from app.providers.impl.api_football_player_squad_provider import (
    ApiFootballPlayerSquadProvider,
)
from app.services.player_squad_ingestion import PlayerSquadIngestionReport

if TYPE_CHECKING:
    from app.core.exceptions import AppError


class _FakeIngestionService:
    def __init__(self, *, error: AppError | None = None) -> None:
        self.team_external_ids: list[str] = []
        self._error = error

    async def sync_team(
        self,
        *,
        team_external_id: str,
    ) -> PlayerSquadIngestionReport:
        self.team_external_ids.append(team_external_id)
        if self._error is not None:
            raise self._error
        return PlayerSquadIngestionReport(
            source="api-football",
            team_external_id=team_external_id,
            records_received=4,
            records_created=2,
            records_updated=1,
            records_unchanged=1,
        )


def _client(
    *,
    configured_token: str,
    error: AppError | None = None,
) -> tuple[TestClient, _FakeIngestionService]:
    app = FastAPI()
    app.include_router(router)
    service = _FakeIngestionService(error=error)
    settings = Settings(internal_sync_token=configured_token)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_player_squad_ingestion_service] = lambda: service
    return TestClient(app), service


@pytest.mark.unit
async def test_provider_factory_builds_api_football_squad_adapter() -> None:
    provider = build_player_squad_provider(Settings(api_football_key="test-key"))
    try:
        assert isinstance(provider, ApiFootballPlayerSquadProvider)
    finally:
        await provider.aclose()


@pytest.mark.unit
def test_squad_sync_fails_closed_when_token_is_not_configured() -> None:
    client, service = _client(configured_token="")

    response = client.post(
        "/internal/sync/player-squads/33",
        headers={"X-Internal-Sync-Token": "anything"},
    )

    assert response.status_code == 503
    assert service.team_external_ids == []


@pytest.mark.unit
def test_squad_sync_rejects_invalid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/player-squads/33",
        headers={"X-Internal-Sync-Token": "wrong-token"},
    )

    assert response.status_code == 403
    assert service.team_external_ids == []


@pytest.mark.unit
def test_squad_sync_returns_persistence_report_with_valid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/player-squads/33",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "api-football",
        "team_external_id": "33",
        "records_received": 4,
        "records_created": 2,
        "records_updated": 1,
        "records_unchanged": 1,
    }
    assert service.team_external_ids == ["33"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ExternalServiceError("upstream failed"), 502),
        (NotFoundError("team missing"), 404),
        (ValidationError("batch conflicted"), 409),
    ],
)
def test_squad_sync_maps_service_errors(
    error: AppError,
    expected_status: int,
) -> None:
    client, service = _client(configured_token="expected-token", error=error)

    response = client.post(
        "/internal/sync/player-squads/33",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(error)}
    assert service.team_external_ids == ["33"]
