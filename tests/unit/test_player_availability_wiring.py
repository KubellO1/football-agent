"""球员可用性 Provider、DI 与内部采集端点的接线测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_player_availability_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.providers import build_player_availability_provider
from app.providers.impl.api_football_player_availability_provider import (
    ApiFootballPlayerAvailabilityProvider,
)
from app.services.player_availability_ingestion import AvailabilityIngestionReport


class _FakeIngestionService:
    def __init__(self) -> None:
        self.fixture_external_ids: list[str] = []

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> AvailabilityIngestionReport:
        self.fixture_external_ids.append(fixture_external_id)
        return AvailabilityIngestionReport(
            source="api-football",
            fixture_external_id=fixture_external_id,
            records_received=3,
            records_created=2,
            duplicates_ignored=1,
        )


def _client(*, configured_token: str) -> tuple[TestClient, _FakeIngestionService]:
    app = FastAPI()
    app.include_router(router)
    service = _FakeIngestionService()
    settings = Settings(internal_sync_token=configured_token)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_player_availability_ingestion_service] = lambda: service
    return TestClient(app), service


@pytest.mark.unit
async def test_provider_factory_builds_api_football_adapter() -> None:
    provider = build_player_availability_provider(
        Settings(api_football_key="test-key"),
    )
    try:
        assert isinstance(provider, ApiFootballPlayerAvailabilityProvider)
    finally:
        await provider.aclose()


@pytest.mark.unit
def test_internal_sync_fails_closed_when_token_is_not_configured() -> None:
    client, service = _client(configured_token="")

    response = client.post(
        "/internal/sync/player-availability/123",
        headers={"X-Internal-Sync-Token": "anything"},
    )

    assert response.status_code == 503
    assert service.fixture_external_ids == []


@pytest.mark.unit
def test_internal_sync_rejects_invalid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/player-availability/123",
        headers={"X-Internal-Sync-Token": "wrong-token"},
    )

    assert response.status_code == 403
    assert service.fixture_external_ids == []


@pytest.mark.unit
def test_internal_sync_persists_fixture_availability_with_valid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/player-availability/123",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "api-football",
        "fixture_external_id": "123",
        "records_received": 3,
        "records_created": 2,
        "duplicates_ignored": 1,
    }
    assert service.fixture_external_ids == ["123"]
