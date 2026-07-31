"""比赛级阵容同步 endpoint 的接线测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_fixture_squad_ingestion_service
from app.api.v1.endpoints.sync import router
from app.config.settings import Settings, get_settings
from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.services.fixture_squad_ingestion import FixtureSquadIngestionReport
from app.services.player_squad_ingestion import PlayerSquadIngestionReport

if TYPE_CHECKING:
    from app.core.exceptions import AppError


def _team_report(team_external_id: str) -> PlayerSquadIngestionReport:
    return PlayerSquadIngestionReport(
        source="api-football",
        team_external_id=team_external_id,
        records_received=20,
        records_created=10,
        records_updated=2,
        records_unchanged=8,
    )


class _FakeFixtureSquadService:
    def __init__(self, *, error: AppError | None = None) -> None:
        self.fixture_external_ids: list[str] = []
        self._error = error

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> FixtureSquadIngestionReport:
        self.fixture_external_ids.append(fixture_external_id)
        if self._error is not None:
            raise self._error
        return FixtureSquadIngestionReport(
            source="api-football",
            fixture_external_id=fixture_external_id,
            home_team=_team_report("home-1"),
            away_team=_team_report("away-1"),
        )


def _client(
    *,
    configured_token: str,
    error: AppError | None = None,
) -> tuple[TestClient, _FakeFixtureSquadService]:
    app = FastAPI()
    app.include_router(router)
    service = _FakeFixtureSquadService(error=error)
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_sync_token=configured_token,
    )
    app.dependency_overrides[get_fixture_squad_ingestion_service] = lambda: service
    return TestClient(app), service


@pytest.mark.unit
def test_fixture_squad_sync_fails_closed_without_configured_token() -> None:
    client, service = _client(configured_token="")

    response = client.post(
        "/internal/sync/fixture-squads/fixture-1",
        headers={"X-Internal-Sync-Token": "anything"},
    )

    assert response.status_code == 503
    assert service.fixture_external_ids == []


@pytest.mark.unit
def test_fixture_squad_sync_rejects_invalid_token() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/fixture-squads/fixture-1",
        headers={"X-Internal-Sync-Token": "wrong-token"},
    )

    assert response.status_code == 403
    assert service.fixture_external_ids == []


@pytest.mark.unit
def test_fixture_squad_sync_returns_nested_reports_and_totals() -> None:
    client, service = _client(configured_token="expected-token")

    response = client.post(
        "/internal/sync/fixture-squads/fixture-1",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "api-football",
        "fixture_external_id": "fixture-1",
        "home_team": {
            "source": "api-football",
            "team_external_id": "home-1",
            "records_received": 20,
            "records_created": 10,
            "records_updated": 2,
            "records_unchanged": 8,
        },
        "away_team": {
            "source": "api-football",
            "team_external_id": "away-1",
            "records_received": 20,
            "records_created": 10,
            "records_updated": 2,
            "records_unchanged": 8,
        },
        "records_received": 40,
        "records_created": 20,
        "records_updated": 4,
        "records_unchanged": 16,
    }
    assert service.fixture_external_ids == ["fixture-1"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ExternalServiceError("upstream failed"), 502),
        (NotFoundError("fixture missing"), 404),
        (ValidationError("identity conflicted"), 409),
    ],
)
def test_fixture_squad_sync_maps_service_errors(
    error: AppError,
    expected_status: int,
) -> None:
    client, service = _client(configured_token="expected-token", error=error)

    response = client.post(
        "/internal/sync/fixture-squads/fixture-1",
        headers={"X-Internal-Sync-Token": "expected-token"},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(error)}
    assert service.fixture_external_ids == ["fixture-1"]
