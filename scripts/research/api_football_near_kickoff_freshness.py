"""One-shot, credential-redacted API-Football near-kickoff odds observation.

Research only: no DB access, no job execution, no retry loop. The admission
policy mirrors TASK-072's conservative two-second, serialized request pacing.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import Settings

LEAGUES = {
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
    "Ligue 1": 61,
}
PARIS = ZoneInfo("Europe/Paris")
REQUEST_CAP = 25
MIN_INTERVAL_SECONDS = 2.0
FRESHNESS_MINUTES = 30.0


def checkpoint(minutes_to_kickoff: float) -> str | None:
    """Choose the latest naturally due pre-kickoff checkpoint."""
    if 0 < minutes_to_kickoff <= 30:
        return "T30"
    if 30 < minutes_to_kickoff <= 60:
        return "T60"
    if 60 < minutes_to_kickoff <= 90:
        return "T90"
    return None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


def fixture_candidate(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    fixture = row.get("fixture")
    if not isinstance(fixture, dict):
        return None
    status = fixture.get("status")
    if not isinstance(status, dict) or status.get("short") not in {"NS", "TBD"}:
        return None
    kickoff = parse_time(fixture.get("date"))
    fixture_id = fixture.get("id")
    if kickoff is None or not isinstance(fixture_id, int):
        return None
    minutes = (kickoff - now).total_seconds() / 60
    due = checkpoint(minutes)
    if due is None:
        return None
    teams = row.get("teams") or {}
    league = row.get("league") or {}
    return {
        "fixture_id": fixture_id,
        "competition": league.get("name"),
        "home": (teams.get("home") or {}).get("name"),
        "away": (teams.get("away") or {}).get("name"),
        "kickoff_utc": kickoff.isoformat(),
        "kickoff_europe_paris": kickoff.astimezone(PARIS).isoformat(),
        "minutes_to_kickoff": round(minutes, 2),
        "due_checkpoint": due,
    }


class PacedReader:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.last_sent: float | None = None
        self.requests = 0
        self.http_200 = 0
        self.http_429 = 0
        self.body_rate_limits = 0
        self.timeline: list[dict[str, Any]] = []

    async def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.requests >= REQUEST_CAP:
            raise RuntimeError("RUN_BUDGET_EXHAUSTED")
        if self.last_sent is not None:
            await asyncio.sleep(max(0.0, self.last_sent + MIN_INTERVAL_SECONDS - time.monotonic()))
        self.last_sent = time.monotonic()
        self.requests += 1
        try:
            response = await self.client.get(endpoint, params=params)
        except httpx.HTTPError as exc:
            # Never print exception text: it may include a URL or credential.
            raise RuntimeError(f"TRANSPORT_ERROR:{type(exc).__name__}") from None
        observation: dict[str, Any] = {
            "endpoint": endpoint,
            "captured_at": datetime.now(UTC).isoformat(),
            "http_status": response.status_code,
            "daily_remaining": response.headers.get("x-ratelimit-requests-remaining"),
            "minute_remaining": response.headers.get("x-ratelimit-remaining"),
        }
        self.timeline.append(observation)
        if response.status_code == 429:
            self.http_429 += 1
            raise RuntimeError("HTTP_429_STOP")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP_{response.status_code}_STOP")
        self.http_200 += 1
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("INVALID_JSON_STOP") from None
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_PAYLOAD_STOP")
        errors = payload.get("errors")
        if isinstance(errors, dict) and any(str(key).casefold() == "ratelimit" for key in errors):
            self.body_rate_limits += 1
            raise RuntimeError("BODY_RATE_LIMIT_STOP")
        if errors:
            raise RuntimeError("APPLICATION_ERROR_STOP")
        # Fail closed before another request if the server says a quota is gone.
        if response.headers.get("x-ratelimit-requests-remaining") == "0":
            raise RuntimeError("DAILY_QUOTA_EXHAUSTED_STOP")
        if response.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("MINUTE_QUOTA_EXHAUSTED_STOP")
        return payload


def extract_odds(
    payload: dict[str, Any], candidate: dict[str, Any], captured_at: str
) -> dict[str, Any]:
    events = payload.get("response") or []
    quotes: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_update = parse_time(event.get("update"))
        for bookmaker in event.get("bookmakers") or []:
            if not isinstance(bookmaker, dict) or bookmaker.get("name") not in {
                "Bet365",
                "Pinnacle",
            }:
                continue
            bookmaker_update = parse_time(bookmaker.get("update"))
            timestamp = bookmaker_update or event_update
            timestamp_scope = (
                "bookmaker" if bookmaker_update else "event" if event_update else "unavailable"
            )
            for market in bookmaker.get("bets") or []:
                if (
                    not isinstance(market, dict)
                    or str(market.get("name", "")).strip().casefold() != "match winner"
                ):
                    continue
                prices = {
                    str(item.get("value", "")).strip().casefold(): item.get("odd")
                    for item in market.get("values") or []
                    if isinstance(item, dict)
                }
                age = (
                    (parse_time(captured_at) - timestamp).total_seconds() / 60
                    if timestamp is not None and parse_time(captured_at) is not None
                    else None
                )
                quotes.append(
                    {
                        "bookmaker_id": bookmaker.get("id"),
                        "bookmaker": bookmaker.get("name"),
                        "market": "1X2",
                        "home": prices.get("home"),
                        "draw": prices.get("draw"),
                        "away": prices.get("away"),
                        "provider_odds_update_at": timestamp.isoformat() if timestamp else None,
                        "timestamp_scope": timestamp_scope,
                        "odds_age_minutes": round(age, 2) if age is not None else None,
                        "freshness": (
                            "UNAVAILABLE"
                            if age is None
                            or any(prices.get(k) is None for k in ("home", "draw", "away"))
                            else "FRESH" if 0 <= age <= FRESHNESS_MINUTES else "STALE"
                        ),
                    }
                )
    return {**candidate, "request_captured_at": captured_at, "quotes": quotes}


async def main() -> None:
    settings = Settings()
    if not settings.api_football_key:
        print(json.dumps({"result": "MISSING_API_FOOTBALL_CREDENTIAL", "api_requests": 0}))
        return
    now = datetime.now(UTC)
    today = now.astimezone(PARIS).date()
    result: dict[str, Any] = {
        "task_id": "TASK-20260920-073",
        "checked_at_utc": now.isoformat(),
        "checked_at_europe_paris": now.astimezone(PARIS).isoformat(),
        "result": "NO_ELIGIBLE_FIXTURES",
        "candidates": [],
        "observations": [],
        "api_requests": 0,
        "http_200": 0,
        "body_rate_limit_errors": 0,
        "http_429": 0,
        "retries": 0,
        "request_timeline": [],
        "nearest_future_fixture": None,
    }
    async with httpx.AsyncClient(
        base_url=settings.api_football_base_url,
        timeout=20.0,
        headers={"x-apisports-key": settings.api_football_key},
    ) as client:
        reader = PacedReader(client)
        future: list[dict[str, Any]] = []
        try:
            for league_name, league_id in LEAGUES.items():
                payload = await reader.get(
                    "/fixtures",
                    {
                        "league": league_id,
                        "season": 2026,
                        "from": today.isoformat(),
                        "to": (today + timedelta(days=1)).isoformat(),
                    },
                )
                for row in payload.get("response") or []:
                    if not isinstance(row, dict):
                        continue
                    fixture = row.get("fixture") or {}
                    kickoff = parse_time(fixture.get("date"))
                    if (
                        kickoff
                        and kickoff > datetime.now(UTC)
                        and (fixture.get("status") or {}).get("short") in {"NS", "TBD"}
                    ):
                        future.append(
                            {
                                "fixture_id": fixture.get("id"),
                                "competition": league_name,
                                "kickoff_utc": kickoff.isoformat(),
                                "kickoff_europe_paris": kickoff.astimezone(PARIS).isoformat(),
                                "minutes_to_kickoff": round(
                                    (kickoff - datetime.now(UTC)).total_seconds() / 60, 2
                                ),
                            }
                        )
                    candidate = fixture_candidate(row, datetime.now(UTC))
                    if candidate is not None:
                        result["candidates"].append(candidate)
            result["candidates"] = sorted(result["candidates"], key=lambda c: c["kickoff_utc"])[:5]
            result["nearest_future_fixture"] = (
                min(future, key=lambda c: c["kickoff_utc"]) if future else None
            )
            # Recalculate eligibility immediately before the first odds request.
            eligible = []
            for item in result["candidates"]:
                kickoff = parse_time(item["kickoff_utc"])
                if kickoff is None:
                    continue
                minutes = (kickoff - datetime.now(UTC)).total_seconds() / 60
                due = checkpoint(minutes)
                if due is not None:
                    eligible.append(
                        {**item, "minutes_to_kickoff": round(minutes, 2), "due_checkpoint": due}
                    )
            if eligible:
                result["result"] = "ODDS_OBSERVED"
                for item in eligible:
                    payload = await reader.get("/odds", {"fixture": item["fixture_id"], "bet": 1})
                    result["observations"].append(
                        extract_odds(payload, item, reader.timeline[-1]["captured_at"])
                    )
        except RuntimeError as exc:
            result["result"] = str(exc)
        finally:
            result["api_requests"] = reader.requests
            result["http_200"] = reader.http_200
            result["body_rate_limit_errors"] = reader.body_rate_limits
            result["http_429"] = reader.http_429
            result["request_timeline"] = reader.timeline
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
