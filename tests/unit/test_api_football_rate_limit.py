"""API-Football request admission and body-level rate-limit contract."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from app.config.settings import Settings
from app.core.exceptions import ExternalServiceError
from app.providers.api_football_rate_limit import ApiFootballRequestLimiter
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.providers.impl.injury_provider import ApiFootballInjuryProvider


class FakeTime:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.day = date(2026, 9, 20)
        self.delays: list[float] = []

    def clock(self) -> float:
        return self.seconds

    def utc_day(self) -> date:
        return self.day

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.seconds += seconds


def limiter(
    clock: FakeTime,
    *,
    interval: float = 2.0,
    minute: int = 20,
    daily: int = 100,
    run: int = 30,
) -> ApiFootballRequestLimiter:
    return ApiFootballRequestLimiter(
        min_interval_seconds=interval,
        per_minute_budget=minute,
        daily_budget=daily,
        run_budget=run,
        circuit_cooldown_seconds=60,
        clock=clock.clock,
        utc_day=clock.utc_day,
        sleep=clock.sleep,
    )


def provider(
    handler: httpx.MockTransport,
    rate_limiter: ApiFootballRequestLimiter | None,
    *,
    max_retries: int = 2,
) -> ApiFootballProvider:
    return ApiFootballProvider(
        api_key="test-only",
        base_url="https://example.test",
        timeout_seconds=5,
        max_retries=max_retries,
        backoff_base_seconds=0.5,
        client=httpx.AsyncClient(
            base_url="https://example.test",
            transport=handler,
        ),
        rate_limiter=rate_limiter,
    )


@pytest.mark.unit
async def test_minimum_interval_and_run_budget() -> None:
    fake = FakeTime()
    guard = limiter(fake, run=2)
    async with guard.serial():
        await guard.admit()
        await guard.admit()
        with pytest.raises(ExternalServiceError, match="RUN_BUDGET"):
            await guard.admit()
    assert fake.delays == [2.0]
    assert guard.physical_requests == 2


@pytest.mark.unit
async def test_per_minute_and_daily_budgets() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0, minute=2, daily=3)
    async with guard.serial():
        await guard.admit()
        await guard.admit()
        await guard.admit()
        with pytest.raises(ExternalServiceError, match="DAILY_BUDGET"):
            await guard.admit()
    assert fake.delays == [60.0]
    assert guard.physical_requests == 3


@pytest.mark.unit
async def test_server_headers_tighten_budget() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0)
    async with guard.serial():
        await guard.admit()
        guard.observe_headers(
            {
                "x-ratelimit-requests-limit": "150000",
                "x-ratelimit-requests-remaining": "1",
                "x-ratelimit-limit": "900",
                "x-ratelimit-remaining": "1",
            }
        )
        await guard.admit()
        with pytest.raises(ExternalServiceError, match="DAILY_BUDGET"):
            await guard.admit()


@pytest.mark.unit
async def test_server_minute_zero_waits_for_window() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0)
    async with guard.serial():
        await guard.admit()
        guard.observe_headers({"x-ratelimit-remaining": "0"})
        await guard.admit()
    assert fake.delays == [60.0]


@pytest.mark.unit
async def test_server_reported_limit_tightens_local_ceiling() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0, minute=20, daily=100)
    guard.observe_headers(
        {
            "x-ratelimit-requests-limit": "2",
            "x-ratelimit-limit": "1",
            "x-ratelimit-requests-remaining": "2",
            "x-ratelimit-remaining": "1",
        }
    )
    async with guard.serial():
        await guard.admit()
        await guard.admit()
        with pytest.raises(ExternalServiceError, match="DAILY_BUDGET"):
            await guard.admit()
    assert fake.delays == [60.0]


@pytest.mark.unit
async def test_http_200_body_rate_limit_is_not_empty_success() -> None:
    fake = FakeTime()
    guard = limiter(fake)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"errors": {"rateLimit": "too many"}, "response": []},
        )

    instance = provider(httpx.MockTransport(handler), guard)
    with pytest.raises(ExternalServiceError, match="CIRCUIT_OPEN"):
        await instance.get_fixtures()
    assert calls == 2
    assert guard.physical_requests == 2
    await instance.aclose()


@pytest.mark.unit
async def test_injury_rate_limit_cannot_be_mistaken_for_no_injuries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": {"rateLimit": "too many"}, "response": []})

    instance = ApiFootballInjuryProvider(
        api_key="test-only",
        base_url="https://example.test",
        timeout_seconds=5,
        max_retries=0,
        backoff_base_seconds=0.5,
        client=httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    with pytest.raises(ExternalServiceError, match="APPLICATION_RATE_LIMIT"):
        await instance.get_injuries(fixture_id=123)
    await instance.aclose()


@pytest.mark.unit
async def test_http_429_respects_retry_after_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeTime()
    guard = limiter(fake)
    calls = 0

    async def fake_backoff(seconds: float) -> None:
        await fake.sleep(seconds)

    monkeypatch.setattr("app.providers.base.asyncio.sleep", fake_backoff)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"errors": [], "response": []})

    instance = provider(httpx.MockTransport(handler), guard)
    assert await instance.get_fixtures() == []
    assert calls == 2
    assert fake.delays == [5.0]
    await instance.aclose()


@pytest.mark.unit
async def test_http_429_without_reset_uses_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0)
    calls = 0

    async def fake_backoff(seconds: float) -> None:
        await fake.sleep(seconds)

    monkeypatch.setattr("app.providers.base.asyncio.sleep", fake_backoff)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"errors": [], "response": []})

    instance = provider(httpx.MockTransport(handler), guard)
    assert await instance.get_fixtures() == []
    assert calls == 2
    assert fake.delays == [0.5]
    await instance.aclose()


@pytest.mark.unit
async def test_http_403_is_not_retried() -> None:
    fake = FakeTime()
    guard = limiter(fake)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"errors": {"access": "denied"}})

    instance = provider(httpx.MockTransport(handler), guard)
    with pytest.raises(ExternalServiceError, match="status 403"):
        await instance.get_fixtures()
    assert calls == 1
    await instance.aclose()


@pytest.mark.unit
async def test_non_rate_limit_application_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"errors": {"fixture": "invalid"}, "response": []},
        )

    instance = provider(httpx.MockTransport(handler), None)
    with pytest.raises(ExternalServiceError, match="application-level errors"):
        await instance.get_fixtures()
    assert calls == 1
    await instance.aclose()


@pytest.mark.unit
async def test_circuit_breaker_recovers_after_cooldown() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0)
    guard.note_rate_limit(None)
    guard.note_rate_limit(None)
    async with guard.serial():
        with pytest.raises(ExternalServiceError, match="CIRCUIT_OPEN"):
            await guard.admit()
        fake.seconds = 60
        await guard.admit()
    assert guard.physical_requests == 1


@pytest.mark.unit
async def test_serial_scope_prevents_concurrent_requests() -> None:
    fake = FakeTime()
    guard = limiter(fake, interval=0)
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with guard.serial():
            await guard.admit()
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(work(), work(), work())
    assert peak == 1
    assert guard.physical_requests == 3


@pytest.mark.unit
def test_config_limits_are_bounded_and_not_betting_thresholds() -> None:
    values = Settings()
    assert values.api_football_min_request_interval_seconds == 2.0
    assert values.api_football_per_minute_request_budget == 20
    assert values.api_football_daily_request_budget == 1000
    assert values.api_football_run_request_budget == 100
    assert values.recommendations_min_ev == 0.05
    assert values.recommendations_min_confidence == 0.70
