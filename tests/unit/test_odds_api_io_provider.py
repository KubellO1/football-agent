"""Unit tests for OddsApiIoProvider.

Uses ``httpx.MockTransport`` to simulate Odds-API.io v3 responses without
hitting the real API. Follows the same pattern as ``tests/unit/test_providers.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.providers.impl.odds_api_io_provider import (
    OddsApiIoProvider,
    OddsAuthError,
    OddsProviderError,
    OddsRateLimitError,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# Odds-API.io real response shapes:
#   GET /events → list of event dicts (not {"data": [...]})
#   GET /odds   → dict with "bookmakers" as dict of {bk_name: [market_list]}


def _make_event(
    event_id: str,
    home: str,
    away: str,
    commence: str,
    status: str = "pending",
) -> dict:
    """Shape of one item in Odds-API.io GET /events response list."""
    return {
        "id": event_id,
        "home": home,
        "away": away,
        "date": commence,
        "status": status,
        "league": {"name": "Premier League", "slug": "epl"},
    }


def _make_odds_response(
    event_id: str,
    home: str,
    away: str,
    date: str,
    home_price: float = 2.10,
    draw_price: float = 3.40,
    away_price: float = 3.25,
    bookmaker: str = "10BET",
) -> dict:
    """Shape of Odds-API.io GET /odds response for a single event."""
    return {
        "id": event_id,
        "home": home,
        "away": away,
        "date": date,
        "bookmakers": {
            bookmaker: [
                {
                    "name": "ML",
                    "updatedAt": "2026-07-22T10:00:00Z",
                    "odds": [
                        {
                            "home": str(home_price),
                            "draw": str(draw_price),
                            "away": str(away_price),
                        }
                    ],
                }
            ]
        },
    }


_OK_EVENT = _make_event("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")
_OK_EVENTS_LIST = [_OK_EVENT]  # /events returns a list
_OK_ODDS_PAYLOAD = _make_odds_response("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")


def _provider_kwargs(client: httpx.AsyncClient) -> dict:
    return {
        "api_key": "test-api-key",
        "base_url": "https://example.test",
        "timeout_seconds": 5.0,
        "max_retries": 1,
        "backoff_base_seconds": 0.0,
        "client": client,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_odds_empty_data_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    assert result == []


@pytest.mark.anyio
async def test_get_odds_parses_single_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/events" in path:
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        if "/odds" in path:
            return httpx.Response(200, json=_OK_ODDS_PAYLOAD)
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")

    assert len(result) == 1
    odds = result[0]
    assert odds.provider_id == "ev-1"
    assert odds.home_team == "Arsenal"
    assert odds.away_team == "Chelsea"
    assert odds.commence_time == datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    assert len(odds.bookmakers) == 1
    bm = odds.bookmakers[0]
    assert bm.bookmaker_key == "10BET"
    assert bm.market == "1x2"
    assert len(bm.outcomes) == 3
    assert all(isinstance(o.price, float) for o in bm.outcomes)
    outcome_names = {o.name for o in bm.outcomes}
    assert outcome_names == {"Arsenal", "Draw", "Chelsea"}


@pytest.mark.anyio
async def test_get_odds_filters_non_1x2_markets() -> None:
    """Events with only Asian handicap / corners should be skipped."""
    non_1x2_event = _make_event("ev-no-1x2", "Team A", "Team B", "2026-07-23T14:00:00Z")
    non_1x2_odds = {
        "id": "ev-no-1x2",
        "home": "Team A",
        "away": "Team B",
        "date": "2026-07-23T14:00:00Z",
        "bookmakers": {
            "10BET": [
                {"name": "asian_handicap", "odds": []},
                {"name": "corners", "odds": []},
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/events" in path:
            return httpx.Response(200, json=[non_1x2_event])
        if "/odds" in path:
            return httpx.Response(200, json=non_1x2_odds)
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    assert result == []


@pytest.mark.anyio
async def test_get_odds_missing_draw_market_skipped() -> None:
    """Event with 1X2 market but no Draw outcome → no usable data."""
    no_draw_event = _make_event("ev-no-draw", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")
    no_draw_odds = {
        "id": "ev-no-draw",
        "home": "Arsenal",
        "away": "Chelsea",
        "date": "2026-07-23T14:00:00Z",
        "bookmakers": {
            "10BET": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": "2.10", "away": "3.25"},
                    ],
                }
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/events" in path:
            return httpx.Response(200, json=[no_draw_event])
        if "/odds" in path:
            return httpx.Response(200, json=no_draw_odds)
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    assert result == []


@pytest.mark.anyio
async def test_get_odds_multiple_events() -> None:
    ev1 = _make_event("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")
    ev2 = _make_event("ev-2", "Liverpool", "Man City", "2026-07-23T16:00:00Z")
    odds1 = _make_odds_response("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")
    odds2 = _make_odds_response("ev-2", "Liverpool", "Man City", "2026-07-23T16:00:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/events" in path:
            return httpx.Response(200, json=[ev1, ev2])
        if "/odds" in path:
            params = dict(request.url.params)
            eid = params.get("eventId", "")
            if eid == "ev-1":
                return httpx.Response(200, json=odds1)
            return httpx.Response(200, json=odds2)
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    assert len(result) == 2
    assert {r.provider_id for r in result} == {"ev-1", "ev-2"}


@pytest.mark.anyio
async def test_get_odds_401_with_quota_raises_auth_error() -> None:
    """401 with OUT_OF_USAGE body → OddsAuthError.

    Note: the provider's _handle_get_events_error currently classifies
    any 401 (quota or auth) as OddsAuthError (checks "401" in message).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"message": "OUT_OF_USAGE"},
            headers={"x-requests-remaining": "0"},
        )

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsAuthError):
        await provider.get_odds(sport="soccer_epl")


@pytest.mark.anyio
async def test_get_odds_401_auth_raises_auth_error() -> None:
    """Plain 401 without quota wording → OddsAuthError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsAuthError):
        await provider.get_odds(sport="soccer_epl")


@pytest.mark.anyio
async def test_get_odds_429_raises_provider_error() -> None:
    """429 with retries exhausted → OddsProviderError.

    Note: 429 is retryable; with max_retries=1 the provider retries
    once then raises ExternalServiceError whose message doesn't contain
    "429", so _handle_get_events_error falls through to OddsProviderError.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Too Many Requests"})

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsProviderError):
        await provider.get_odds(sport="soccer_epl")


@pytest.mark.anyio
async def test_get_odds_404_raises_provider_error() -> None:
    """404 → OddsProviderError (provider does not special-case 404)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsProviderError):
        await provider.get_odds(sport="soccer_epl")


@pytest.mark.anyio
async def test_get_odds_500_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsProviderError):
        await provider.get_odds(sport="soccer_epl")


@pytest.mark.anyio
async def test_historical_odds_basic_parsing() -> None:
    """get_historical_odds filters events within ±6h of *at*."""
    at_time = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)  # match event date
    historical_event = _make_event("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")
    historical_odds = _make_odds_response("ev-1", "Arsenal", "Chelsea", "2026-07-23T14:00:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if "/events" in path:
            return httpx.Response(200, json=[historical_event])
        if "/odds" in path:
            return httpx.Response(200, json=historical_odds)
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_historical_odds(
        sport="soccer_epl", at=at_time,
    )
    assert len(result) == 1
    assert result[0].home_team == "Arsenal"
    assert result[0].away_team == "Chelsea"
