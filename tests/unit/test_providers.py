"""外部数据源 Provider 的单元测试。

用 httpx.MockTransport 注入假响应，不触达真实网络。覆盖：
- API-Football 与 The Odds API 的响应解析（字段映射、bookmaker×market 展平）；
- 基础 HTTP 层的重试（瞬时 503 后成功、重试耗尽抛错）与不可重试 4xx 立即抛错。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.providers.impl.odds_api_provider import TheOddsApiProvider

# --- 假响应载荷 -------------------------------------------------------------

_FIXTURES_PAYLOAD = {
    "response": [
        {
            "fixture": {
                "id": 12345,
                "date": "2026-07-02T18:30:00+00:00",
                "status": {"short": "NS"},
                "venue": {"name": "Old Trafford"},
            },
            "league": {"name": "Premier League", "season": 2026},
            "teams": {
                "home": {"id": 33, "name": "Manchester United"},
                "away": {"id": 40, "name": "Liverpool"},
            },
            "goals": {"home": None, "away": None},
        }
    ]
}

_ODDS_PAYLOAD = [
    {
        "id": "abc123",
        "sport_key": "soccer_epl",
        "commence_time": "2026-07-02T18:30:00Z",
        "home_team": "Manchester United",
        "away_team": "Liverpool",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "last_update": "2026-07-02T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-07-02T12:00:00Z",
                        "outcomes": [
                            {"name": "Manchester United", "price": 2.5},
                            {"name": "Liverpool", "price": 2.9},
                            {"name": "Draw", "price": 3.3},
                        ],
                    }
                ],
            }
        ],
    }
]


def _provider_kwargs(client: httpx.AsyncClient) -> dict:
    return {
        "api_key": "test-key",
        "base_url": "https://example.test",
        "timeout_seconds": 5.0,
        "max_retries": 3,
        "backoff_base_seconds": 0.0,  # 关闭退避等待，保持测试快速
        "client": client,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )


# --- 解析 -------------------------------------------------------------------


@pytest.mark.unit
async def test_api_football_parses_fixture() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fixtures"
        return httpx.Response(200, json=_FIXTURES_PAYLOAD)

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    fixtures = await provider.get_fixtures(on_date=date(2026, 7, 2))

    assert len(fixtures) == 1
    f = fixtures[0]
    assert f.provider_id == "12345"
    assert f.kickoff == datetime(2026, 7, 2, 18, 30, tzinfo=UTC)
    assert f.status == "NS"
    assert f.home.name == "Manchester United"
    assert f.away.provider_id == "40"
    assert f.league == "Premier League"
    assert f.season == 2026
    assert f.venue == "Old Trafford"


@pytest.mark.unit
async def test_api_football_get_fixture_returns_none_when_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": []})

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    assert await provider.get_fixture("999") is None


@pytest.mark.unit
async def test_odds_api_parses_and_flattens_markets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sports/soccer_epl/odds"
        assert request.url.params["apiKey"] == "test-key"
        assert request.url.params["oddsFormat"] == "decimal"
        return httpx.Response(200, json=_ODDS_PAYLOAD)

    provider = TheOddsApiProvider(**_provider_kwargs(_client(handler)))
    events = await provider.get_odds(sport="soccer_epl")

    assert len(events) == 1
    event = events[0]
    assert event.provider_id == "abc123"
    assert event.home_team == "Manchester United"
    assert len(event.bookmakers) == 1
    market = event.bookmakers[0]
    assert market.bookmaker_key == "pinnacle"
    assert market.market == "h2h"
    assert len(market.outcomes) == 3
    assert market.outcomes[0].price == 2.5


_HISTORICAL_PAYLOAD = {
    "timestamp": "2024-08-17T11:00:00Z",
    "previous_timestamp": "2024-08-17T10:00:00Z",
    "next_timestamp": "2024-08-17T12:00:00Z",
    "data": _ODDS_PAYLOAD,
}


@pytest.mark.unit
async def test_odds_api_parses_historical_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 历史端点路径 + date 参数（ISO8601 UTC）
        assert request.url.path == "/historical/sports/soccer_epl/odds"
        assert request.url.params["apiKey"] == "test-key"
        assert request.url.params["date"] == "2024-08-17T11:00:00Z"
        return httpx.Response(200, json=_HISTORICAL_PAYLOAD)

    provider = TheOddsApiProvider(**_provider_kwargs(_client(handler)))
    events = await provider.get_historical_odds(
        sport="soccer_epl", at=datetime(2024, 8, 17, 11, 0, tzinfo=UTC)
    )

    # 从 envelope 的 data 数组解出事件，形状与 live 相同
    assert len(events) == 1
    assert events[0].provider_id == "abc123"
    assert events[0].bookmakers[0].outcomes[0].price == 2.5


@pytest.mark.unit
async def test_odds_api_historical_handles_empty_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 该时点无数据：data 为空数组
        return httpx.Response(200, json={"timestamp": "2024-08-17T11:00:00Z", "data": []})

    provider = TheOddsApiProvider(**_provider_kwargs(_client(handler)))
    events = await provider.get_historical_odds(
        sport="soccer_epl", at=datetime(2024, 8, 17, 11, 0, tzinfo=UTC)
    )
    assert events == []


# --- 重试 / 超时 ------------------------------------------------------------


@pytest.mark.unit
async def test_retries_transient_status_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:  # 前两次瞬时 503
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json={"response": []})

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    result = await provider.get_fixtures()

    assert result == []
    assert calls["n"] == 3  # 两次重试后第三次成功


@pytest.mark.unit
async def test_retry_exhaustion_raises_external_service_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(ExternalServiceError):
        await provider.get_fixtures()


@pytest.mark.unit
async def test_transport_error_is_retried_then_raises() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("boom", request=request)

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(ExternalServiceError):
        await provider.get_fixtures()
    assert calls["n"] == 4  # 1 初次 + 3 重试


@pytest.mark.unit
async def test_non_retryable_4xx_raises_immediately() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="invalid api key")

    provider = ApiFootballProvider(**_provider_kwargs(_client(handler)))
    with pytest.raises(ExternalServiceError):
        await provider.get_fixtures()
    assert calls["n"] == 1  # 4xx 不重试
