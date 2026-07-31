"""API-Football 球队阵容适配器单元测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.providers.impl.api_football_player_squad_provider import (
    ApiFootballPlayerSquadProvider,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

CAPTURED_AT = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


def _payload(*, response: list[object], results: int | None = None) -> dict:
    return {
        "errors": [],
        "results": len(response) if results is None else results,
        "response": response,
    }


def _squad(*, players: list[object] | None = None, team_id: int = 33) -> dict:
    return {
        "team": {"id": team_id, "name": "Test Team"},
        "players": (
            players
            if players is not None
            else [
                {
                    "id": 99,
                    "name": "Test Player",
                    "age": 25,
                    "number": 8,
                    "position": "Midfielder",
                },
            ]
        ),
    }


@asynccontextmanager
async def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[ApiFootballPlayerSquadProvider]:
    client = httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    provider = ApiFootballPlayerSquadProvider(
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
async def test_provider_parses_complete_team_squad() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/players/squads"
        assert request.url.params["team"] == "33"
        return httpx.Response(200, json=_payload(response=[_squad()]))

    async with _provider(handler) as provider:
        batch = await provider.get_team_squad(team_external_id=" 33 ")

    assert batch.source == "api-football"
    assert batch.team_external_id == "33"
    assert batch.captured_at == CAPTURED_AT
    assert batch.response_complete is True
    assert batch.request_reference == "/players/squads?team=33"
    assert len(batch.records) == 1
    assert batch.records[0].player_external_id == "99"
    assert batch.records[0].player_name == "Test Player"
    assert batch.records[0].raw_position == "Midfielder"


@pytest.mark.unit
async def test_provider_distinguishes_complete_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[]))

    async with _provider(handler) as provider:
        batch = await provider.get_team_squad(team_external_id="33")

    assert batch.response_complete is True
    assert batch.records == []


@pytest.mark.unit
async def test_provider_rejects_inconsistent_result_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[_squad()], results=2))

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="result count"):
            await provider.get_team_squad(team_external_id="33")


@pytest.mark.unit
async def test_provider_rejects_team_identity_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[_squad(team_id=44)]))

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="does not match request"):
            await provider.get_team_squad(team_external_id="33")


@pytest.mark.unit
async def test_provider_rejects_missing_player_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        player = {"name": "Test Player", "position": "Midfielder"}
        return httpx.Response(200, json=_payload(response=[_squad(players=[player])]))

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="player.id"):
            await provider.get_team_squad(team_external_id="33")


@pytest.mark.unit
async def test_provider_rejects_duplicate_player_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        player = {"id": 99, "name": "Test Player", "position": "Midfielder"}
        return httpx.Response(
            200,
            json=_payload(response=[_squad(players=[player, player])]),
        )

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="duplicate player ids"):
            await provider.get_team_squad(team_external_id="33")


@pytest.mark.unit
async def test_provider_rejects_application_level_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": {"team": "invalid team"}, "response": []},
        )

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="application-level errors"):
            await provider.get_team_squad(team_external_id="33")


@pytest.mark.unit
async def test_provider_propagates_upstream_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    async with _provider(handler) as provider:
        with pytest.raises(ExternalServiceError, match="INVALID_API_KEY"):
            await provider.get_team_squad(team_external_id="33")
