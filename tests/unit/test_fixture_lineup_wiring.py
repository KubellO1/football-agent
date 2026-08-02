"""比赛官方阵容 Provider、DI 与受保护同步端点的接线测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_fixture_lineup_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.providers import build_fixture_lineup_provider
from app.providers.impl.api_football_fixture_lineup_provider import (
    ApiFootballFixtureLineupProvider,
)
from app.services.fixture_lineup_ingestion import FixtureLineupIngestionReport

if TYPE_CHECKING:
    from app.core.exceptions import AppError


class _FakeIngestionService:
    def __init__(self, *, error: AppError | None = None) -> None:
        self.fixture_external_ids: list[str] = []
        self._error = error

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> FixtureLineupIngestionReport:
        self.fixture_external_ids.append(fixture_external_id)
        if self._error is not None:
            raise self._error
        return FixtureLineupIngestionReport(
            source="api-football",
            fixture_external_id=fixture_external_id,
            lineups_received=2,
            players_received=36,
            lineups_created=2,
            lineups_unchanged=0,
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
    app.dependency_overrides[get_fixture_lineup_ingestion_service] = lambda: service
    return TestClient(app), service


@pytest.mark.unit
async def test_provider_factory_builds_api_football_fixture_lineup_adapter() -> None:
    provider = build_fixture_lineup_provider(Settings(api_football_key="test-key"))
    try:
        assert isinstance(provider, ApiFootballFixtureLineupProvider)
    finally:
        await provider.aclose()


@pytest.mark.unit
def test_fixture_lineup_sync_rejects_invalid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/fixture-lineups/123",
        headers={"X-Internal-Sync-Token": "wrong-token"},
    )

    assert response.status_code == 403
    assert service.fixture_external_ids == []


@pytest.mark.unit
def test_fixture_lineup_sync_returns_audit_report() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/fixture-lineups/123",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "api-football",
        "fixture_external_id": "123",
        "lineups_received": 2,
        "players_received": 36,
        "lineups_created": 2,
        "lineups_unchanged": 0,
    }
    assert service.fixture_external_ids == ["123"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ExternalServiceError("upstream failed"), 502),
        (NotFoundError("fixture missing"), 404),
        (ValidationError("identity mismatch"), 409),
    ],
)
def test_fixture_lineup_sync_maps_application_errors(
    error: AppError,
    expected_status: int,
) -> None:
    client, service = _client(configured_token="expected-token", error=error)

    response = client.post(
        "/internal/sync/fixture-lineups/123",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(error)}
    assert service.fixture_external_ids == ["123"]
