"""Unit tests for OddsApiIoProvider.

Uses ``httpx.MockTransport`` to simulate Odds-API.io v3 responses without
hitting the real API. Follows the same pattern as ``tests/unit/test_providers.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.providers.impl.odds_api_io_provider import (
    MARKET_REJECTION_UNSUPPORTED,
    OddsApiIoProvider,
    OddsAuthError,
    OddsProviderError,
    OddsRateLimitError,
    normalize_odds_api_io_market,
)
from app.providers.schemas.odds import ProviderOddsTarget
from app.utils.rate_limiter import TokenBucketRateLimiter

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
    bookmaker: str = "Bet365",
    market_name: str = "ML",
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
                    "name": market_name,
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
        "api_key": "***",
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
            return httpx.Response(200, json=[_OK_ODDS_PAYLOAD])
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
    assert bm.bookmaker_key == "Bet365"
    assert bm.market == "h2h"
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
            return httpx.Response(200, json=[non_1x2_odds])
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    assert result == []


@pytest.mark.parametrize(
    "provider_market",
    ["1x2", "1X2", " 1x2 ", "ML", " ml ", "h2h", " H2H ", "moneyline", "money line"],
)
def test_normalize_known_three_way_moneyline_aliases(provider_market: str) -> None:
    assert normalize_odds_api_io_market(provider_market) == "h2h"


@pytest.mark.parametrize(
    "provider_market",
    ["double chance", "DoubleChance", "draw no bet", "DNB", "asian_handicap", "totals"],
)
def test_similar_or_unknown_markets_are_not_mapped_to_h2h(provider_market: str) -> None:
    assert normalize_odds_api_io_market(provider_market) is None


@pytest.mark.anyio
async def test_native_h2h_payload_remains_canonical() -> None:
    payload = _make_odds_response(
        "ev-1",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:00:00Z",
        market_name=" h2H ",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        return httpx.Response(200, json=[payload])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")

    assert result[0].bookmakers[0].market == "h2h"


@pytest.mark.anyio
async def test_1x2_payload_preserves_outcome_mapping_and_prices() -> None:
    payload = _make_odds_response(
        "ev-1",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:00:00Z",
        home_price=2.11,
        draw_price=3.41,
        away_price=3.26,
        market_name="1X2",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        return httpx.Response(200, json=[payload])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")
    market = result[0].bookmakers[0]

    assert market.market == "h2h"
    assert [(outcome.name, outcome.price) for outcome in market.outcomes] == [
        ("Arsenal", 2.11),
        ("Draw", 3.41),
        ("Chelsea", 3.26),
    ]


@pytest.mark.anyio
async def test_unknown_market_is_rejected_with_reason_code_and_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_calls: list[tuple[str, tuple[object, ...]]] = []

    def capture_log(message: str, *args: object) -> None:
        log_calls.append((message, args))

    monkeypatch.setattr("app.providers.impl.odds_api_io_provider.logger.info", capture_log)
    payload = _make_odds_response(
        "ev-1",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:00:00Z",
        market_name="mystery market",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        return httpx.Response(200, json=[payload])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="soccer_epl")

    assert result == []
    assert provider.stats()["markets_rejected_unsupported"] == 1
    assert any(MARKET_REJECTION_UNSUPPORTED in args for _, args in log_calls)


@pytest.mark.anyio
async def test_double_chance_payload_is_not_converted_to_h2h() -> None:
    payload = _make_odds_response(
        "ev-1",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:00:00Z",
        market_name="Double Chance",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        return httpx.Response(200, json=[payload])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))

    assert await provider.get_odds(sport="soccer_epl") == []
    assert provider.stats()["markets_rejected_unsupported"] == 1


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
            return httpx.Response(200, json=[no_draw_odds])
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
            return httpx.Response(200, json=[odds1, odds2])
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
async def test_get_odds_429_raises_rate_limit_error() -> None:
    """429 with retries exhausted → OddsProviderError.

    Note: 429 is retryable; with max_retries=1 the provider retries
    once then raises ExternalServiceError whose message doesn't contain
    "429", so _handle_get_events_error falls through to OddsProviderError.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Too Many Requests"})

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(OddsRateLimitError):
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
            return httpx.Response(200, json=[historical_odds])
        return httpx.Response(404)

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_historical_odds(
        sport="soccer_epl",
        at=at_time,
    )
    assert len(result) == 1
    assert result[0].home_team == "Arsenal"
    assert result[0].away_team == "Chelsea"


@pytest.mark.anyio
async def test_targeted_multi_queries_three_mapped_fixtures_once_with_bet365() -> None:
    fixtures = [
        ("ev-river", "River Plate", "Rosario Central", "2026-08-03T20:00:00Z"),
        ("ev-lanus", "Lanus", "Instituto Cordoba", "2026-08-03T20:30:00Z"),
        (
            "ev-sarmiento",
            "Sarmiento Junin",
            "Independiente Rivadavia",
            "2026-08-03T21:00:00Z",
        ),
    ]
    events = [_make_event(*fixture) for fixture in fixtures]
    events.append(_make_event("ev-other", "Other A", "Other B", "2026-08-03T20:00:00Z"))
    odds_by_id = {
        event_id: _make_odds_response(event_id, home, away, kickoff)
        for event_id, home, away, kickoff in fixtures
    }
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events)
        assert request.url.path.endswith("/odds/multi")
        params = dict(request.url.params)
        assert params["bookmakers"] == "Bet365"
        assert "10BET" not in params["bookmakers"]
        ids = params["eventIds"].split(",")
        assert set(ids) == set(odds_by_id)
        return httpx.Response(200, json=[odds_by_id[event_id] for event_id in ids])

    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    targets = [
        ProviderOddsTarget(
            fixture_id=uuid4(),
            home_team=home,
            away_team=away,
            kickoff=datetime.fromisoformat(kickoff.replace("Z", "+00:00")),
        )
        for _, home, away, kickoff in fixtures
    ]
    result = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)

    assert len(result) == 3
    assert {item.provider_id for item in result} == set(odds_by_id)
    assert len(calls) == 2


@pytest.mark.anyio
async def test_multi_chunks_at_ten_events() -> None:
    events = [
        _make_event(
            f"ev-{index}",
            f"Home {index}",
            f"Away {index}",
            "2026-08-03T20:00:00Z",
        )
        for index in range(11)
    ]
    multi_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events)
        ids = dict(request.url.params)["eventIds"].split(",")
        multi_sizes.append(len(ids))
        rows = [
            _make_odds_response(
                event_id,
                f"Home {event_id.removeprefix('ev-')}",
                f"Away {event_id.removeprefix('ev-')}",
                "2026-08-03T20:00:00Z",
            )
            for event_id in ids
        ]
        return httpx.Response(200, json=rows)

    provider = OddsApiIoProvider(
        **_provider_kwargs(_client(handler)),
        run_request_budget=5,
    )
    targets = [
        ProviderOddsTarget(
            fixture_id=uuid4(),
            home_team=f"Home {index}",
            away_team=f"Away {index}",
            kickoff=datetime(2026, 8, 3, 20, tzinfo=UTC),
        )
        for index in range(11)
    ]
    result = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)

    assert len(result) == 11
    assert multi_sizes == [10, 1]


@pytest.mark.anyio
async def test_403_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=_OK_EVENTS_LIST)
        calls += 1
        return httpx.Response(403, json={"message": "bookmaker not allowed"})

    kwargs = _provider_kwargs(_client(handler))
    kwargs["max_retries"] = 3
    provider = OddsApiIoProvider(**kwargs)
    result = await provider.get_odds(sport="football")

    assert result == []
    assert calls == 1


@pytest.mark.anyio
async def test_429_waits_retry_after_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=[])

    monkeypatch.setattr("app.providers.base.asyncio.sleep", fake_sleep)
    provider = OddsApiIoProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_odds(sport="football")

    assert result == []
    assert calls == 2
    assert waits == [7.0]


@pytest.mark.anyio
async def test_429_without_reset_is_not_retried_with_short_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    monkeypatch.setattr("app.providers.base.asyncio.sleep", fake_sleep)
    kwargs = _provider_kwargs(_client(handler))
    kwargs["max_retries"] = 3
    provider = OddsApiIoProvider(**kwargs)

    with pytest.raises(OddsRateLimitError):
        await provider.get_odds(sport="football")

    assert calls == 1
    assert waits == []


@pytest.mark.anyio
async def test_run_request_budget_stops_before_second_multi_batch() -> None:
    events = [
        _make_event(
            f"ev-{index}",
            f"Home {index}",
            f"Away {index}",
            "2026-08-03T20:00:00Z",
        )
        for index in range(11)
    ]
    multi_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal multi_calls
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events)
        multi_calls += 1
        ids = dict(request.url.params)["eventIds"].split(",")
        return httpx.Response(
            200,
            json=[
                _make_odds_response(
                    event_id,
                    f"Home {event_id.removeprefix('ev-')}",
                    f"Away {event_id.removeprefix('ev-')}",
                    "2026-08-03T20:00:00Z",
                )
                for event_id in ids
            ],
        )

    provider = OddsApiIoProvider(
        **_provider_kwargs(_client(handler)),
        run_request_budget=2,
    )
    targets = [
        ProviderOddsTarget(
            fixture_id=uuid4(),
            home_team=f"Home {index}",
            away_team=f"Away {index}",
            kickoff=datetime(2026, 8, 3, 20, tzinfo=UTC),
        )
        for index in range(11)
    ]

    result = await provider.get_odds_for_fixtures(sport="football", fixtures=targets)

    assert len(result) == 10
    assert multi_calls == 1


@pytest.mark.anyio
async def test_response_rate_limit_headers_update_budget() -> None:
    limiter = TokenBucketRateLimiter(budget=100, daily_budget=500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            headers={
                "x-ratelimit-limit": "100",
                "x-ratelimit-remaining": "42",
                "x-ratelimit-reset": "60",
            },
        )

    provider = OddsApiIoProvider(
        **_provider_kwargs(_client(handler)),
        rate_limiter=limiter,
    )
    await provider.get_odds(sport="football")

    budget = await limiter.budget()
    assert budget.capacity == 100
    assert budget.remaining == 42
    assert budget.daily_remaining == 499
