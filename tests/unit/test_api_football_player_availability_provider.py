"""API-Football 球员可用性适配器单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.providers.impl.api_football_player_availability_provider import (
    ApiFootballPlayerAvailabilityProvider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

CAPTURED_AT = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


def _payload(*, response: list[object], current: int = 1, total: int = 1) -> dict:
    return {
        "errors": [],
        "paging": {"current": current, "total": total},
        "response": response,
    }


def _record() -> dict:
    return {
        "player": {"id": 99, "name": "Test Player"},
        "team": {"id": 33, "name": "Test Team"},
        "fixture": {"id": 1234},
        "type": "Questionable",
        "reason": "Muscle injury",
    }


def _provider(handler: Callable[[httpx.Request], httpx.Response]):
    client = httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    return ApiFootballPlayerAvailabilityProvider(
        api_key="test-key",
        base_url="https://example.test",
        timeout_seconds=5.0,
        max_retries=0,
        backoff_base_seconds=0.0,
        client=client,
        clock=lambda: CAPTURED_AT,
    )


@pytest.mark.unit
async def test_provider_parses_complete_fixture_availability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/injuries"
        assert request.url.params["fixture"] == "1234"
        return httpx.Response(200, json=_payload(response=[_record()]))

    provider = _provider(handler)
    batch = await provider.get_fixture_availability(fixture_external_id=" 1234 ")

    assert batch.source == "api-football"
    assert batch.fixture_external_id == "1234"
    assert batch.captured_at == CAPTURED_AT
    assert batch.response_complete is True
    assert batch.request_reference == "/injuries?fixture=1234"
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.team_external_id == "33"
    assert record.player_external_id == "99"
    assert record.player_name == "Test Player"
    assert record.raw_status == "Questionable"
    assert record.reason == "Muscle injury"


@pytest.mark.unit
async def test_provider_distinguishes_complete_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[]))

    batch = await _provider(handler).get_fixture_availability(
        fixture_external_id="1234",
    )

    assert batch.response_complete is True
    assert batch.records == []


@pytest.mark.unit
async def test_provider_marks_incomplete_paging() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(response=[_record()], current=1, total=2),
        )

    batch = await _provider(handler).get_fixture_availability(
        fixture_external_id="1234",
    )

    assert batch.response_complete is False
    assert len(batch.records) == 1


@pytest.mark.unit
async def test_provider_rejects_missing_player_identity() -> None:
    record = _record()
    record["player"] = {"name": "Test Player"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[record]))

    with pytest.raises(ExternalServiceError, match="player.id"):
        await _provider(handler).get_fixture_availability(
            fixture_external_id="1234",
        )


@pytest.mark.unit
async def test_provider_rejects_fixture_identity_conflict() -> None:
    record = _record()
    record["fixture"] = {"id": 9999}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(response=[record]))

    with pytest.raises(ExternalServiceError, match="does not match request"):
        await _provider(handler).get_fixture_availability(
            fixture_external_id="1234",
        )


@pytest.mark.unit
async def test_provider_rejects_application_level_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": {"fixture": "invalid fixture"}, "response": []},
        )

    with pytest.raises(ExternalServiceError, match="application-level errors"):
        await _provider(handler).get_fixture_availability(
            fixture_external_id="1234",
        )


@pytest.mark.unit
async def test_provider_propagates_upstream_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(ExternalServiceError, match="INVALID_API_KEY"):
        await _provider(handler).get_fixture_availability(
            fixture_external_id="1234",
        )
