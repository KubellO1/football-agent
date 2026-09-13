from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.intelligence.contracts import SourcePolicy
from app.intelligence.http import CachedHttpClient, RequestBudgetExceeded


def test_http_client_caches_and_enforces_budget(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["User-Agent"] == "FootballAgent/1.0 contact@example.test"
        return httpx.Response(200, json={"ok": True})

    client = CachedHttpClient(
        tmp_path,
        request_budget=1,
        user_agent="FootballAgent/1.0 contact@example.test",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    try:
        args = {
            "source": "test",
            "source_policy": SourcePolicy.OPEN_DATA,
            "cache_ttl": timedelta(hours=1),
        }
        assert client.get_json("https://example.test/a", **args).cache_hit is False
        assert client.get_json("https://example.test/a", **args).cache_hit is True
        assert calls == 1
        with pytest.raises(RequestBudgetExceeded):
            client.get_json("https://example.test/b", **args)
    finally:
        client.close()


def test_http_client_preserves_media_type_on_cache_hit(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"ok":true}',
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    client = CachedHttpClient(
        tmp_path,
        request_budget=1,
        user_agent="FootballAgent/1.0 contact@example.test",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    try:
        args = {
            "source": "test",
            "source_policy": SourcePolicy.OPEN_DATA,
            "cache_ttl": timedelta(hours=1),
        }
        first = client.get_json("https://example.test/data", **args)
        second = client.get_json("https://example.test/data", **args)
        assert first.payload.media_type == "text/plain; charset=utf-8"
        assert second.payload.media_type == first.payload.media_type
    finally:
        client.close()


def test_http_client_honors_expires_and_uses_conditional_request(tmp_path) -> None:
    clock = [datetime(2026, 9, 13, tzinfo=UTC)]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={"ok": True},
                headers={
                    "expires": "Sun, 13 Sep 2026 02:00:00 GMT",
                    "last-modified": "Sun, 13 Sep 2026 00:00:00 GMT",
                },
            )
        assert request.headers["If-Modified-Since"] == "Sun, 13 Sep 2026 00:00:00 GMT"
        return httpx.Response(304)

    client = CachedHttpClient(
        tmp_path,
        request_budget=2,
        user_agent="FootballAgent/1.0 contact@example.test",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: clock[0],
    )
    try:
        args = {
            "source": "test",
            "source_policy": SourcePolicy.FREE_OFFICIAL_API,
            "cache_ttl": timedelta(hours=1),
        }
        client.get_json("https://example.test/weather", **args)
        clock[0] = datetime(2026, 9, 13, 1, 30, tzinfo=UTC)
        assert client.get_json("https://example.test/weather", **args).cache_hit is True
        assert calls == 1
        clock[0] = datetime(2026, 9, 13, 2, 1, tzinfo=UTC)
        refreshed = client.get_json("https://example.test/weather", **args)
        assert refreshed.status_code == 304
        assert refreshed.cache_hit is True
        assert refreshed.payload.content == b'{"ok":true}'
        assert calls == 2
    finally:
        client.close()
