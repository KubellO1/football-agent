"""API-Football 比赛官方阵容适配器单元测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.providers.impl.api_football_fixture_lineup_provider import (
    ApiFootballFixtureLineupProvider,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

CAPTURED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _player(player_id: int) -> dict:
    return {
        "player": {
            "id": player_id,
            "name": f"Player {player_id}",
            "number": player_id % 100,
            "pos": "M",
            "grid": "2:1",
        },
    }


def _team_lineup(*, team_id: int, first_player_id: int) -> dict:
    return {
        "team": {"id": team_id, "name": f"Team {team_id}"},
        "formation": "4-3-3",
        "startXI": [_player(value) for value in range(first_player_id, first_player_id + 11)],
        "substitutes": [
            _player(value) for value in range(first_player_id + 11, first_player_id + 18)
        ],
    }


def _payload(*, response: list[object], results: int | None = None) -> dict:
    return {
        "errors": [],
        "results": len(response) if results is None else results,
        "paging": {"current": 1, "total": 1},
        "response": response,
    }


@asynccontextmanager
async def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[ApiFootballFixtureLineupProvider]:
    client = httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    provider = ApiFootballFixtureLineupProvider(
        api_key="test-key",
        base_url="https://example.test",
        timeout_seconds=5.0,
        max_retries=0,
        backoff_base_seconds=0.0,
        client=client,
        clock=lambda: CAPTURED_AT,
    )
    try:
        yield provider
    finally:
        await provider.aclose()


@pytest.mark.unit
async def test_provider_parses_complete_fixture_lineups() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fixtures/lineups"
        assert request.url.params["fixture"] == "1234"
        return httpx.Response(
            200,
            json=_payload(
                response=[
                    _team_lineup(team_id=10, first_player_id=100),
                    _team_lineup(team_id=20, first_player_id=200),
                ],
            ),
        )

    async with _provider(handler) as provider:
        batch = await provider.get_fixture_lineups(fixture_external_id=" 1234 ")

    assert batch.source == "api-football"
    assert batch.fixture_external_id == "1234"
    assert batch.captured_at == CAPTURED_AT
    assert batch.response_complete is True
    assert batch.request_reference == "/fixtures/lineups?fixture=1234"
    assert len(batch.lineups) == 2
    assert batch.lineups[0].team_external_id == "10"
    assert batch.lineups[0].formation == "4-3-3"
    assert len(batch.lineups[0].starting) == 11
    assert len(batch.lineups[0].substitutes) == 7
    assert batch.lineups[0].starting[0].raw_position == "M"
    assert batch.lineups[0].starting[0].grid_position == "2:1"


@pytest.mark.unit
async def test_provider_distinguishes_unpublished_lineups() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[]))

    async with _provider(handler) as provider:
        batch = await provider.get_fixture_lineups(fixture_external_id="1234")

    assert batch.response_complete is True
    assert batch.lineups == []


@pytest.mark.unit
async def test_provider_marks_missing_paging_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = _payload(response=[])
        payload.pop("paging")
        return httpx.Response(200, json=payload)

    async with _provider(handler) as provider:
        batch = await provider.get_fixture_lineups(fixture_external_id="1234")

    assert batch.response_complete is False


@pytest.mark.unit
async def test_provider_rejects_inconsistent_result_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[], results=2))

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="result count"):
            await provider.get_fixture_lineups(fixture_external_id="1234")


@pytest.mark.unit
async def test_provider_rejects_single_team_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(response=[_team_lineup(team_id=10, first_player_id=100)]),
        )

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="batch failed validation"):
            await provider.get_fixture_lineups(fixture_external_id="1234")


@pytest.mark.unit
async def test_provider_rejects_missing_player_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        home = _team_lineup(team_id=10, first_player_id=100)
        home["startXI"][0] = {"player": {"name": "Missing ID", "pos": "M"}}
        return httpx.Response(
            200,
            json=_payload(
                response=[home, _team_lineup(team_id=20, first_player_id=200)],
            ),
        )

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="player.id"):
            await provider.get_fixture_lineups(fixture_external_id="1234")


@pytest.mark.unit
async def test_provider_rejects_duplicate_player_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        home = _team_lineup(team_id=10, first_player_id=100)
        away = _team_lineup(team_id=20, first_player_id=200)
        away["startXI"][0] = home["startXI"][0]
        return httpx.Response(200, json=_payload(response=[home, away]))

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="batch failed validation"):
            await provider.get_fixture_lineups(fixture_external_id="1234")


@pytest.mark.unit
async def test_provider_rejects_application_level_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": {"fixture": "invalid fixture"}, "response": []},
        )

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="application-level errors"):
            await provider.get_fixture_lineups(fixture_external_id="1234")


@pytest.mark.unit
async def test_provider_propagates_upstream_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="INVALID_API_KEY"):
            await provider.get_fixture_lineups(fixture_external_id="1234")
