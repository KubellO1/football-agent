"""Odds-API.io 密钥、配置和限额安全契约。"""

from __future__ import annotations

import io
import logging
import time
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.core.logging import RedactingFormatter, SensitiveDataFilter
from app.utils.rate_limiter import TokenBucketRateLimiter
from app.workers.scheduler_runner import _LOG_FORMAT as SCHEDULER_LOG_FORMAT


class FakeRedis:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def cache_get(self, key: str) -> str | None:
        return self.values.get(key)

    async def cache_set(self, key: str, value: str, ttl: int = 300) -> None:
        del ttl
        self.values[key] = value


def test_query_api_key_is_redacted_from_message_arguments_and_traceback() -> None:
    secret = "sensitive" + "-value"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.odds.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("request=%s count=%d", f"https://example.test/events?apiKey={secret}", 3)
    try:
        raise RuntimeError(f"failed URL apiKey={secret}&sport=football")
    except RuntimeError:
        logger.exception("provider failure")

    rendered = stream.getvalue()
    assert secret not in rendered
    assert "apiKey=***" in rendered
    assert "count=3" in rendered


def test_query_api_key_is_redacted_when_url_encoded() -> None:
    secret = "encoded-secret-value"
    record = logging.makeLogRecord(
        {"msg": ("request=https://example.test/events?" f"apiKey%3D{secret}%26sport%3Dfootball")}
    )

    rendered = RedactingFormatter("%(message)s").format(record)

    assert "apiKey%3D***" in rendered
    assert secret not in rendered


def test_scheduler_file_formatters_are_redacting() -> None:
    assert isinstance(SCHEDULER_LOG_FORMAT, RedactingFormatter)


def test_free_odds_api_defaults_are_frozen() -> None:
    settings = Settings(
        _env_file=None,
        odds_api_io_plan="free",
        odds_api_io_bookmakers=["Bet365"],
        odds_api_io_hourly_request_limit=100,
        odds_api_io_daily_request_limit=500,
        odds_api_io_run_request_budget=10,
    )

    assert settings.odds_api_io_plan == "free"
    assert settings.odds_api_io_bookmakers == ["Bet365"]
    assert settings.odds_api_io_hourly_request_limit == 100
    assert settings.odds_api_io_daily_request_limit == 500
    assert settings.odds_api_io_run_request_budget == 10


def test_free_plan_rejects_unavailable_bookmaker() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            odds_api_io_plan="free",
            odds_api_io_bookmakers=["Bet365", "10BET"],
        )


def test_solo_plan_can_enable_additional_bookmaker() -> None:
    settings = Settings(
        _env_file=None,
        odds_api_io_plan="solo",
        odds_api_io_bookmakers=["Bet365", "10BET"],
    )

    assert settings.odds_api_io_bookmakers == ["Bet365", "10BET"]


def test_paid_profile_accepts_verified_runtime_limits() -> None:
    settings = Settings(
        _env_file=None,
        odds_api_io_plan="paid",
        odds_api_io_bookmakers=["Bet365"],
        odds_api_io_hourly_request_limit=10_000,
        odds_api_io_daily_request_limit=0,
    )

    assert settings.odds_api_io_hourly_request_limit == 10_000
    assert settings.odds_api_io_daily_request_limit == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("odds_api_io_hourly_request_limit", 101),
        ("odds_api_io_daily_request_limit", 501),
        ("odds_api_io_run_request_budget", 101),
    ],
)
def test_free_limits_cannot_be_configured_above_contract(field: str, value: int) -> None:
    values: dict[str, object] = {
        "odds_api_io_plan": "free",
        "odds_api_io_bookmakers": ["Bet365"],
        "odds_api_io_hourly_request_limit": 100,
        "odds_api_io_daily_request_limit": 500,
        "odds_api_io_run_request_budget": 10,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_rate_limiter_enforces_hourly_and_daily_budgets() -> None:
    hourly = TokenBucketRateLimiter(budget=2, daily_budget=10)
    assert await hourly.acquire()
    assert await hourly.acquire()
    assert not await hourly.acquire()

    daily = TokenBucketRateLimiter(budget=10, daily_budget=2)
    assert await daily.acquire()
    assert await daily.acquire()
    assert not await daily.acquire()


@pytest.mark.anyio
async def test_provider_headers_reduce_local_remaining_budget() -> None:
    limiter = TokenBucketRateLimiter(budget=100, daily_budget=500)

    await limiter.update_from_headers(
        {
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "1",
            "x-ratelimit-reset": "60",
        }
    )

    assert await limiter.remaining() == 1
    assert await limiter.acquire()
    assert not await limiter.acquire()


@pytest.mark.anyio
async def test_paid_rate_limiter_adopts_server_capacity_and_has_no_daily_cap() -> None:
    limiter = TokenBucketRateLimiter(
        budget=1_000,
        daily_budget=0,
        dynamic_server_limit=True,
    )

    await limiter.update_from_headers(
        {
            "x-ratelimit-limit": "10000",
            "x-ratelimit-remaining": "9999",
            "x-ratelimit-reset": "2026-08-03T18:28:52Z",
        }
    )

    budget = await limiter.budget()
    assert budget.capacity == 10_000
    assert budget.remaining == 9_999
    assert budget.daily_capacity is None
    assert budget.daily_remaining is None
    assert budget.reset_at == pytest.approx(
        datetime(2026, 8, 3, 18, 28, 52, tzinfo=UTC).timestamp()
    )


@pytest.mark.anyio
async def test_paid_rate_limiter_honours_non_hour_aligned_server_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_reset = datetime(2026, 8, 3, 18, 20, tzinfo=UTC).timestamp()
    reset_at = datetime(2026, 8, 3, 18, 28, 52, tzinfo=UTC).timestamp()
    limiter = TokenBucketRateLimiter(
        budget=10_000,
        daily_budget=0,
        dynamic_server_limit=True,
    )
    limiter._hour_window = int(before_reset // 3600)
    monkeypatch.setattr(time, "time", lambda: before_reset)
    await limiter.update_from_headers(
        {
            "x-ratelimit-limit": "10000",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "2026-08-03T18:28:52Z",
        }
    )

    assert not await limiter.acquire()

    monkeypatch.setattr(time, "time", lambda: reset_at + 1)
    assert await limiter.acquire()


@pytest.mark.anyio
async def test_rate_limiter_restores_only_current_redis_windows() -> None:
    hour_window = int(time.time() // 3600)
    day_window = int(time.time() // 86400)
    redis = FakeRedis(
        {
            "odds:rate:hourly_used": "7",
            "odds:rate:hour_window": str(hour_window),
            "odds:rate:daily_used": "11",
            "odds:rate:day_window": str(day_window),
        }
    )
    limiter = TokenBucketRateLimiter(budget=100, daily_budget=500, redis=redis)  # type: ignore[arg-type]

    budget = await limiter.budget()

    assert budget.remaining == 93
    assert budget.daily_remaining == 489


@pytest.mark.anyio
async def test_rate_limiter_ignores_stale_redis_windows() -> None:
    redis = FakeRedis(
        {
            "odds:rate:hourly_used": "99",
            "odds:rate:hour_window": str(int(time.time() // 3600) - 1),
            "odds:rate:daily_used": "499",
            "odds:rate:day_window": str(int(time.time() // 86400) - 1),
        }
    )
    limiter = TokenBucketRateLimiter(budget=100, daily_budget=500, redis=redis)  # type: ignore[arg-type]

    budget = await limiter.budget()

    assert budget.remaining == 100
    assert budget.daily_remaining == 500
