"""WeatherAPI 供应商事件边界的单元测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from app.providers.impl.weather_provider import WeatherApiProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    ResponseHandler = Callable[[httpx.Request], httpx.Response]


def _client(handler: ResponseHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )


def _provider(client: httpx.AsyncClient) -> WeatherApiProvider:
    return WeatherApiProvider(
        api_key="test-key",
        base_url="https://example.test",
        timeout_seconds=5.0,
        max_retries=0,
        backoff_base_seconds=0.0,
        client=client,
    )


@pytest.mark.unit
async def test_get_sports_events_keeps_only_object_entries() -> None:
    valid_event = {
        "stadium": "Emirates Stadium",
        "start": "2026-07-27 18:00",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sports.json"
        assert request.url.params["key"] == "test-key"
        assert request.url.params["q"] == "football"
        return httpx.Response(
            200,
            json={"football": [valid_event, "invalid", 7, ["invalid"]]},
        )

    async with _client(handler) as client:
        result = await _provider(client).get_sports_events()

    assert result == [valid_event]


@pytest.mark.unit
async def test_get_sports_events_uses_football_fallback() -> None:
    football_event = {"stadium": "Anfield"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"football": [football_event]})

    async with _client(handler) as client:
        result = await _provider(client).get_sports_events(sport="soccer")

    assert result == [football_event]


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"football": {"stadium": "invalid-container"}},
        {"football": None},
    ],
)
async def test_get_sports_events_rejects_invalid_containers(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        result = await _provider(client).get_sports_events()

    assert result == []


@pytest.mark.unit
async def test_get_sports_events_returns_empty_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with _client(handler) as client:
        result = await _provider(client).get_sports_events()

    assert result == []
