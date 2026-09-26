"""API-Football targeted named-bookmaker odds contract."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.config.settings import Settings
from app.providers import build_odds_provider
from app.providers.impl.api_football_odds_provider import ApiFootballOddsProvider
from app.providers.schemas.odds import ProviderOddsTarget


def _target(provider_id: str | None = "1550130") -> ProviderOddsTarget:
    return ProviderOddsTarget(
        fixture_id=uuid4(),
        provider_fixture_id=provider_id,
        home_team="Villarreal",
        away_team="Levante",
        kickoff=datetime(2026, 9, 20, 16, 30, tzinfo=UTC),
        sport_key="soccer_spain_la_liga",
    )


def _provider(handler: httpx.MockTransport) -> ApiFootballOddsProvider:
    return ApiFootballOddsProvider(
        api_key="test-only",
        base_url="https://example.test",
        timeout_seconds=5,
        max_retries=0,
        backoff_base_seconds=0.1,
        bookmakers=("Bet365", "Pinnacle"),
        client=httpx.AsyncClient(
            base_url="https://example.test",
            transport=handler,
        ),
    )


@pytest.mark.unit
async def test_production_odds_factory_does_not_construct_paid_providers() -> None:
    settings = Settings(_env_file=None, api_football_key="test-only")
    instance = build_odds_provider(settings)
    assert isinstance(instance, ApiFootballOddsProvider)
    await instance.aclose()


@pytest.mark.unit
async def test_real_payload_shape_maps_named_bookmakers_to_canonical_h2h() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "fixture": {
                            "id": 1550130,
                            "date": "2026-09-20T16:30:00+00:00",
                        },
                        "update": "2026-09-20T15:00:00+00:00",
                        "bookmakers": [
                            {
                                "id": 8,
                                "name": "Bet365",
                                "update": "2026-09-20T15:15:00+00:00",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "1.90"},
                                            {"value": "Draw", "odd": "3.40"},
                                            {"value": "Away", "odd": "4.20"},
                                        ],
                                    }
                                ],
                            },
                            {
                                "id": 4,
                                "name": "Pinnacle",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "1.95"},
                                            {"value": "Draw", "odd": "3.35"},
                                            {"value": "Away", "odd": "4.10"},
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
        )

    instance = _provider(httpx.MockTransport(handler))
    events = await instance.get_odds_for_fixtures(
        sport="football",
        fixtures=[_target()],
        markets=("h2h",),
    )

    assert "fixture=1550130" in seen_query
    assert "bet=1" in seen_query
    assert len(events) == 1
    event = events[0]
    assert event.source == "api-football"
    assert event.provider_id == "1550130"
    assert [market.bookmaker_title for market in event.bookmakers] == [
        "Bet365",
        "Pinnacle",
    ]
    assert all(market.market == "h2h" for market in event.bookmakers)
    assert event.bookmakers[0].last_update == datetime(
        2026,
        9,
        20,
        15,
        15,
        tzinfo=UTC,
    )
    assert event.bookmakers[1].last_update == datetime(
        2026,
        9,
        20,
        15,
        0,
        tzinfo=UTC,
    )
    assert [(item.name, item.price) for item in event.bookmakers[0].outcomes] == [
        ("Villarreal", 1.9),
        ("Draw", 3.4),
        ("Levante", 4.2),
    ]
    assert instance.pop_coverage_stats()["combined_odds_coverage"] == 1.0
    await instance.aclose()


@pytest.mark.unit
async def test_unknown_or_incomplete_bookmaker_market_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "fixture": {"id": 1550130},
                        "update": "2026-09-20T15:00:00+00:00",
                        "bookmakers": [
                            {
                                "name": "Consensus Average",
                                "bets": [],
                            },
                            {
                                "name": "Bet365",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "1.90"},
                                            {"value": "Away", "odd": "4.20"},
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
        )

    instance = _provider(httpx.MockTransport(handler))
    assert (
        await instance.get_odds_for_fixtures(
            sport="football",
            fixtures=[_target()],
        )
        == []
    )
    stats = instance.pop_coverage_stats()
    assert stats["primary_empty_events"] == 1
    assert stats["unmatched_reason_counts"] == {"NO_BOOKMAKER_ODDS": 1}
    await instance.aclose()


@pytest.mark.unit
async def test_missing_provider_fixture_id_fails_closed_without_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    instance = _provider(httpx.MockTransport(handler))
    assert (
        await instance.get_odds_for_fixtures(
            sport="football",
            fixtures=[_target(None)],
        )
        == []
    )
    assert calls == 0
    assert instance.pop_coverage_stats()["unmatched_reason_counts"] == {"MISSING_PROVIDER_EVENT": 1}
    await instance.aclose()
