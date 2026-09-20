"""Research-only, point-in-time API-Football odds observations.

One local Windows research task may have multiple one-time triggers. Every run
checks which manifest fixtures are naturally due at T-60/T-30 and stores
evidence before making a bounded, paced request. No database is accessed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from scripts.research.api_football_near_kickoff_freshness import (
    PARIS,
    extract_odds,
    parse_time,
)

from app.config.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path("C:/Users/ruowa/Projects/football-agent/output/api-football-multi-freshness-task075")
MANIFEST = Path(__file__).with_name("task075_fixtures.json")
LEDGER = ROOT / "request_ledger.json"
MAX_REQUESTS = 25
MAX_REQUESTS_PER_MINUTE = 20
MAX_REQUESTS_PER_DAY = 100
MIN_INTERVAL_SECONDS = 2.0
MAX_TRIGGER_DELAY = timedelta(minutes=5)
BOOKMAKERS = ("Bet365", "Pinnacle")
SLOTS = {"T60": timedelta(minutes=60), "T30": timedelta(minutes=30)}


def now_utc() -> datetime:
    return datetime.now(UTC)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        if default is None:
            raise RuntimeError(f"MISSING_EVIDENCE:{path.name}")
        return default
    with path.open(encoding="utf-8") as stream:
        loaded = json.load(stream)
    if not isinstance(loaded, dict):
        raise RuntimeError(f"INVALID_EVIDENCE:{path.name}")
    return loaded


def manifest() -> list[dict[str, Any]]:
    data = read_json(MANIFEST)
    rows = data.get("fixtures")
    if not isinstance(rows, list) or not 5 <= len(rows) <= 10:
        raise RuntimeError("INVALID_MANIFEST_SIZE")
    ids: set[int] = set()
    leagues: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("fixture_id"), int):
            raise RuntimeError("INVALID_MANIFEST_FIXTURE")
        fixture_id = row["fixture_id"]
        kickoff = parse_time(row.get("kickoff_utc"))
        if fixture_id in ids or kickoff is None:
            raise RuntimeError("DUPLICATE_OR_INVALID_MANIFEST_FIXTURE")
        if not isinstance(row.get("league_id"), int):
            raise RuntimeError("INVALID_MANIFEST_LEAGUE")
        ids.add(fixture_id)
        leagues.add(row["league_id"])
    if len(leagues) < 3:
        raise RuntimeError("INSUFFICIENT_LEAGUE_DIVERSITY")
    return rows


def evidence_path(fixture_id: int, slot: str) -> Path:
    return ROOT / f"fixture_{fixture_id}_{slot.lower()}.json"


def due_at(row: dict[str, Any], slot: str) -> datetime:
    kickoff = parse_time(row["kickoff_utc"])
    assert kickoff is not None
    return kickoff - SLOTS[slot]


def is_naturally_due(row: dict[str, Any], slot: str, now: datetime) -> bool:
    due = due_at(row, slot)
    kickoff = parse_time(row["kickoff_utc"])
    assert kickoff is not None
    return due <= now <= due + MAX_TRIGGER_DELAY and now < kickoff


@contextmanager
def collector_lock() -> Iterator[None]:
    """Serialise all scheduled processes; fail rather than break a stale lock."""
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = ROOT / ".collector.lock"
    fd: int | None = None
    for _ in range(180):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(1)
    if fd is None:
        raise RuntimeError("COLLECTOR_LOCKED")
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _ledger() -> dict[str, Any]:
    data = read_json(LEDGER, {"request_cap": MAX_REQUESTS, "requests": []})
    if data.get("request_cap") != MAX_REQUESTS or not isinstance(data.get("requests"), list):
        raise RuntimeError("REQUEST_LEDGER_INVALID")
    return data


async def reserve_request(fixture_id: int, slot: str, endpoint: str) -> str:
    """Persist PRE_REQUEST before network I/O; count all physical attempts."""
    data = _ledger()
    requests = data["requests"]
    if len(requests) >= MAX_REQUESTS:
        raise RuntimeError("RUN_REQUEST_BUDGET_EXHAUSTED")
    now = now_utc()
    timestamps = [parse_time(item.get("requested_at")) for item in requests]
    recent = [
        value for value in timestamps if value is not None and now - value < timedelta(minutes=1)
    ]
    today = [value for value in timestamps if value is not None and value.date() == now.date()]
    if len(today) >= MAX_REQUESTS_PER_DAY:
        raise RuntimeError("DAILY_REQUEST_BUDGET_EXHAUSTED")
    delay = 0.0
    if recent and len(recent) >= MAX_REQUESTS_PER_MINUTE:
        delay = max(delay, (min(recent) + timedelta(minutes=1) - now).total_seconds())
    if timestamps and timestamps[-1] is not None:
        delay = max(
            delay, (timestamps[-1] + timedelta(seconds=MIN_INTERVAL_SECONDS) - now).total_seconds()
        )
    if delay > 0:
        await asyncio.sleep(delay)
    request_id = uuid.uuid4().hex
    requests.append(
        {
            "request_id": request_id,
            "fixture_id": fixture_id,
            "checkpoint": slot,
            "endpoint_class": endpoint.removeprefix("/"),
            "requested_at": now_utc().isoformat(),
            "state": "PRE_REQUEST",
            "http_status": None,
            "body_rate_limit_error": None,
            "http_429": None,
            "retries": 0,
        }
    )
    atomic_json(LEDGER, data)
    return request_id


def finish_request(request_id: str, **changes: Any) -> None:
    data = _ledger()
    matches = [item for item in data["requests"] if item.get("request_id") == request_id]
    if len(matches) != 1:
        raise RuntimeError("REQUEST_LEDGER_ENTRY_MISSING")
    matches[0].update(changes)
    matches[0]["completed_at"] = now_utc().isoformat()
    atomic_json(LEDGER, data)


class AuditedReader:
    def __init__(self, client: httpx.AsyncClient, fixture_id: int, slot: str) -> None:
        self.client = client
        self.fixture_id = fixture_id
        self.slot = slot

    async def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = await reserve_request(self.fixture_id, self.slot, endpoint)
        try:
            response = await self.client.get(endpoint, params=params)
        except httpx.HTTPError as exc:
            finish_request(request_id, state="TRANSPORT_ERROR", error=type(exc).__name__)
            raise RuntimeError(f"TRANSPORT_ERROR:{type(exc).__name__}") from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        body_limit = bool(
            isinstance(payload, dict)
            and isinstance(payload.get("errors"), dict)
            and any(str(key).casefold() == "ratelimit" for key in payload["errors"])
        )
        finish_request(
            request_id,
            state="POST_REQUEST",
            http_status=response.status_code,
            body_rate_limit_error=body_limit,
            http_429=response.status_code == 429,
            retry_after=response.headers.get("Retry-After"),
            daily_remaining=response.headers.get("x-ratelimit-requests-remaining"),
            minute_remaining=response.headers.get("x-ratelimit-remaining"),
        )
        if response.status_code == 429:
            raise RuntimeError("HTTP_429_STOP")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP_{response.status_code}_STOP")
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_JSON_STOP")
        if body_limit:
            raise RuntimeError("BODY_RATE_LIMIT_STOP")
        if payload.get("errors"):
            raise RuntimeError("APPLICATION_ERROR_STOP")
        if response.headers.get("x-ratelimit-requests-remaining") == "0":
            raise RuntimeError("DAILY_QUOTA_EXHAUSTED_STOP")
        if response.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("MINUTE_QUOTA_EXHAUSTED_STOP")
        return payload


def validate_fixture(payload: dict[str, Any], row: dict[str, Any]) -> None:
    matches = payload.get("response") or []
    if len(matches) != 1:
        raise RuntimeError("FIXTURE_NOT_UNIQUE_OR_MISSING")
    actual = matches[0]
    fixture = actual.get("fixture") or {}
    league = actual.get("league") or {}
    teams = actual.get("teams") or {}
    if (
        fixture.get("id") != row["fixture_id"]
        or league.get("id") != row["league_id"]
        or parse_time(fixture.get("date")) != parse_time(row["kickoff_utc"])
        or (fixture.get("status") or {}).get("short") not in {"NS", "TBD"}
        or (teams.get("home") or {}).get("name") != row["home"]
        or (teams.get("away") or {}).get("name") != row["away"]
    ):
        raise RuntimeError("FIXTURE_IDENTITY_OR_KICKOFF_CHANGED")


async def collect_one(client: httpx.AsyncClient, row: dict[str, Any], slot: str) -> dict[str, Any]:
    fixture_id = row["fixture_id"]
    path = evidence_path(fixture_id, slot)
    if path.exists():
        return {"fixture_id": fixture_id, "checkpoint": slot, "status": "ALREADY_RECORDED"}
    record: dict[str, Any] = {
        "fixture_id": fixture_id,
        "competition": row["competition"],
        "home": row["home"],
        "away": row["away"],
        "kickoff_utc": row["kickoff_utc"],
        "checkpoint": slot,
        "scheduled_at": due_at(row, slot).astimezone(PARIS).isoformat(),
        "started_at": now_utc().isoformat(),
        "request_attempted": False,
        "request_completed": False,
        "http_status": None,
        "captured_at": None,
        "bookmakers": {name: {"freshness": "UNAVAILABLE"} for name in BOOKMAKERS},
        "status": "PRE_REQUEST",
        "error": None,
    }
    atomic_json(path, record)
    try:
        if not is_naturally_due(row, slot, now_utc()):
            raise RuntimeError("MISSED_NATURAL_CHECKPOINT")
        reader = AuditedReader(client, fixture_id, slot)
        record["request_attempted"] = True
        atomic_json(path, record)
        fixture_payload = await reader.get("/fixtures", {"id": fixture_id})
        validate_fixture(fixture_payload, row)
        if not is_naturally_due(row, slot, now_utc()):
            raise RuntimeError("MISSED_NATURAL_CHECKPOINT")
        odds_payload = await reader.get("/odds", {"fixture": fixture_id, "bet": 1})
        for event in odds_payload.get("response") or []:
            if isinstance(event, dict) and isinstance(event.get("fixture"), dict):
                returned_id = event["fixture"].get("id")
                if returned_id is not None and returned_id != fixture_id:
                    raise RuntimeError("ODDS_FIXTURE_ID_MISMATCH")
        captured_at = now_utc().isoformat()
        extracted = extract_odds(odds_payload, row, captured_at)
        for name in BOOKMAKERS:
            quotes = [q for q in extracted["quotes"] if q["bookmaker"] == name]
            if len(quotes) == 1:
                record["bookmakers"][name] = quotes[0]
            elif len(quotes) > 1:
                record["bookmakers"][name] = {
                    "freshness": "UNAVAILABLE",
                    "reason": "AMBIGUOUS_BOOKMAKER_QUOTES",
                }
        record["captured_at"] = captured_at
        record["raw_payload_sha256"] = hashlib.sha256(
            json.dumps(odds_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        record["request_completed"] = True
        record["http_status"] = 200
        record["status"] = "CHECKPOINT_RECORDED"
    except (RuntimeError, OSError) as exc:
        record["status"] = "CHECKPOINT_FAILED"
        record["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    atomic_json(path, record)
    return {
        "fixture_id": fixture_id,
        "checkpoint": slot,
        "status": record["status"],
        "error": record["error"],
    }


async def collect_due() -> dict[str, Any]:
    rows = manifest()
    settings = Settings()
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY_NOT_CONFIGURED")
    with collector_lock():
        now = now_utc()
        due = [(row, slot) for row in rows for slot in SLOTS if is_naturally_due(row, slot, now)]
        if not due:
            return {"status": "NO_NATURALLY_DUE_CHECKPOINT", "requests": 0}
        results = []
        async with httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            timeout=20.0,
            headers={"x-apisports-key": settings.api_football_key},
        ) as client:
            for row, slot in sorted(
                due, key=lambda item: (due_at(item[0], item[1]), item[0]["fixture_id"])
            ):
                if evidence_path(row["fixture_id"], slot).exists():
                    continue
                results.append(await collect_one(client, row, slot))
                if results[-1].get("error") in {
                    "HTTP_429_STOP",
                    "BODY_RATE_LIMIT_STOP",
                    "DAILY_QUOTA_EXHAUSTED_STOP",
                    "MINUTE_QUOTA_EXHAUSTED_STOP",
                    "RUN_REQUEST_BUDGET_EXHAUSTED",
                }:
                    break
        return {
            "status": "COLLECTION_FINISHED",
            "results": results,
            "request_total": len(_ledger()["requests"]),
        }


def summarize() -> dict[str, Any]:
    rows = manifest()
    result: dict[str, Any] = {
        "TASK_ID": "TASK-20260920-075",
        "FIXTURES_TESTED": len(rows),
        "LEAGUES_TESTED": sorted({row["competition"] for row in rows}),
        "OBSERVATIONS": [],
    }
    age_by_slot: dict[str, list[float]] = {slot: [] for slot in SLOTS}
    fresh_by_pair: dict[tuple[str, str], int] = {
        (name, slot): 0 for name in BOOKMAKERS for slot in SLOTS
    }
    comparable = 0
    updated = 0
    all_fresh = True
    all_bookmaker_timestamps = True
    for row in rows:
        records = {slot: read_json(evidence_path(row["fixture_id"], slot), {}) for slot in SLOTS}
        for name in BOOKMAKERS:
            first = (records["T60"].get("bookmakers") or {}).get(name) or {}
            second = (records["T30"].get("bookmakers") or {}).get(name) or {}
            ts_first = first.get("provider_odds_update_at")
            ts_second = second.get("provider_odds_update_at")
            if ts_first and ts_second:
                comparable += 1
                updated += ts_first != ts_second
            for slot in SLOTS:
                record = records[slot]
                quote = (record.get("bookmakers") or {}).get(name) or {}
                freshness = quote.get("freshness", "UNAVAILABLE")
                age = quote.get("odds_age_minutes")
                if isinstance(age, (int, float)):
                    age_by_slot[slot].append(float(age))
                fresh_by_pair[(name, slot)] += freshness == "FRESH"
                all_fresh &= freshness == "FRESH"
                all_bookmaker_timestamps &= quote.get("timestamp_scope") == "bookmaker"
                result["OBSERVATIONS"].append(
                    {
                        "fixture_id": row["fixture_id"],
                        "competition": row["competition"],
                        "home": row["home"],
                        "away": row["away"],
                        "kickoff_utc": row["kickoff_utc"],
                        "checkpoint": slot,
                        "bookmaker": name,
                        "provider_odds_update_at": quote.get("provider_odds_update_at"),
                        "captured_at": record.get("captured_at"),
                        "odds_age_minutes": age,
                        "home_odds": quote.get("home"),
                        "draw_odds": quote.get("draw"),
                        "away_odds": quote.get("away"),
                        "freshness": freshness,
                        "timestamp_scope": quote.get("timestamp_scope"),
                        "checkpoint_status": record.get("status", "NOT_EXECUTED"),
                        "error": record.get("error"),
                    }
                )
    for name in BOOKMAKERS:
        for slot in SLOTS:
            result[f"{name.upper()}_{slot}_FRESH_RATE"] = fresh_by_pair[(name, slot)] / len(rows)
    for slot in SLOTS:
        result[f"MEDIAN_ODDS_AGE_{slot}"] = (
            statistics.median(age_by_slot[slot]) if age_by_slot[slot] else None
        )
    all_ages = age_by_slot["T60"] + age_by_slot["T30"]
    result["MAX_ODDS_AGE"] = max(all_ages) if all_ages else None
    result["TIMESTAMP_UPDATE_RATE"] = updated / comparable if comparable else None
    result["TIMESTAMP_UPDATE_COMPARABLE_PAIRS"] = comparable
    result["BOOKMAKER_TIMESTAMP_COVERAGE"] = all_bookmaker_timestamps
    ledger = _ledger()
    requests = ledger["requests"]
    result["HTTP_REQUESTS_ATTEMPTED"] = len(requests)
    result["HTTP_200"] = sum(item.get("http_status") == 200 for item in requests)
    result["BODY_RATELIMIT_ERRORS"] = sum(
        item.get("body_rate_limit_error") is True for item in requests
    )
    result["HTTP_429"] = sum(item.get("http_429") is True for item in requests)
    result["RETRIES"] = sum(int(item.get("retries", 0)) for item in requests)
    result["API_FOOTBALL_ODDS_PRODUCTION_SUITABLE"] = (
        all_fresh and all_bookmaker_timestamps and len(result["LEAGUES_TESTED"]) == 5
    )
    atomic_json(ROOT / "consolidated.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "collect-due", "summarize"))
    args = parser.parse_args()
    if args.command == "preflight":
        rows = manifest()
        probe = ROOT / "write_preflight.json"
        atomic_json(probe, {"status": "CREATED"})
        if read_json(probe).get("status") != "CREATED":
            raise RuntimeError("EVIDENCE_PREFLIGHT_FAILED")
        atomic_json(probe, {"status": "UPDATED"})
        if read_json(probe).get("status") != "UPDATED":
            raise RuntimeError("EVIDENCE_PREFLIGHT_FAILED")
        probe.unlink()
        result = {"status": "PREFLIGHT_PASS", "fixtures": len(rows)}
    elif args.command == "collect-due":
        result = asyncio.run(collect_due())
    else:
        result = summarize()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
