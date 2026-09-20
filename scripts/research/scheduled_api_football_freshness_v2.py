"""Research-only, crash-auditable near-kickoff odds observations.

The evidence directory is inside the scheduled local project (and ignored by
Git). No production database or job is accessed. The request ledger reserves
each attempt before network I/O, so a crash consumes budget conservatively.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from scripts.research.api_football_near_kickoff_freshness import (
    LEAGUES,
    PARIS,
    checkpoint,
    extract_odds,
    parse_time,
)

from app.config.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path("C:/Users/ruowa/Projects/football-agent/output/api-football-odds-freshness")
LEDGER = ROOT / "request_ledger.json"
MANIFEST = ROOT / "fixture.json"
CAP = 25
MIN_INTERVAL_SECONDS = 2.0
BOOKMAKERS = ("Bet365", "Pinnacle")
CHECKPOINTS = ("T90", "T60", "T30")
LEAGUE_BY_ID = {league_id: name for name, league_id in LEAGUES.items()}


def now_utc() -> datetime:
    return datetime.now(UTC)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".odds-evidence-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        if default is None:
            raise RuntimeError(f"MISSING_EVIDENCE:{path.name}")
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"INVALID_EVIDENCE:{path.name}")
    return data


def write_preflight() -> dict[str, Any]:
    ROOT.mkdir(parents=True, exist_ok=True)
    trial = ROOT / f".write-test-{uuid.uuid4().hex}.json"
    try:
        atomic_json(trial, {"phase": 1})
        if read_json(trial)["phase"] != 1:
            raise RuntimeError("WRITE_PREFLIGHT_READ_FAILED")
        atomic_json(trial, {"phase": 2})
        if read_json(trial)["phase"] != 2:
            raise RuntimeError("WRITE_PREFLIGHT_UPDATE_FAILED")
    finally:
        trial.unlink(missing_ok=True)
    result = {
        "WRITE_PREFLIGHT": "PASS",
        "execution_user": getpass.getuser(),
        "working_directory": str(Path.cwd()),
        "evidence_path": str(ROOT),
        "checked_at": now_utc().isoformat(),
    }
    atomic_json(ROOT / "write_preflight.json", result)
    return result


@contextmanager
def ledger_lock() -> Iterator[None]:
    lock = ROOT / ".request-ledger.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError("REQUEST_LEDGER_LOCKED") from None
    try:
        yield
    finally:
        lock.rmdir()


def update_ledger(request_id: str | None, changes: dict[str, Any]) -> dict[str, Any]:
    with ledger_lock():
        ledger = read_json(LEDGER, {"request_cap": CAP, "requests": []})
        requests = ledger.get("requests")
        if not isinstance(requests, list) or ledger.get("request_cap") != CAP:
            raise RuntimeError("REQUEST_LEDGER_INVALID")
        if request_id is None:
            if len(requests) >= CAP:
                raise RuntimeError("REQUEST_BUDGET_EXHAUSTED")
            entry = changes.copy()
            entry["request_id"] = uuid.uuid4().hex
            entry["state"] = "ATTEMPT_RESERVED"
            requests.append(entry)
        else:
            entry = None
            for row in requests:
                if row.get("request_id") == request_id:
                    entry = row
                    break
            if entry is not None:
                entry.update(changes)
        if entry is None:
            raise RuntimeError("REQUEST_LEDGER_ENTRY_MISSING")
        atomic_json(LEDGER, ledger)
        return entry.copy()


class AuditedReader:
    def __init__(self, client: httpx.AsyncClient, fixture_id: int | None, slot: str) -> None:
        self.client = client
        self.fixture_id = fixture_id
        self.slot = slot
        self.last_sent: float | None = None

    async def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.last_sent is not None:
            await asyncio.sleep(max(0.0, self.last_sent + MIN_INTERVAL_SECONDS - time.monotonic()))
        request = update_ledger(
            None,
            {
                "fixture_id": self.fixture_id,
                "checkpoint": self.slot,
                "requested_at": now_utc().isoformat(),
                "endpoint_class": endpoint.removeprefix("/"),
                "http_status": None,
                "rate_limit_body_error": None,
                "http_429": None,
                "retry_count": 0,
            },
        )
        request_id = str(request["request_id"])
        self.last_sent = time.monotonic()
        try:
            response = await self.client.get(endpoint, params=params)
        except httpx.HTTPError as exc:
            update_ledger(
                request_id,
                {
                    "state": "TRANSPORT_ERROR",
                    "completed_at": now_utc().isoformat(),
                    "error": type(exc).__name__,
                },
            )
            raise RuntimeError(f"TRANSPORT_ERROR:{type(exc).__name__}") from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        rate_limit_error = bool(
            isinstance(payload, dict)
            and isinstance(payload.get("errors"), dict)
            and any(str(key).casefold() == "ratelimit" for key in payload["errors"])
        )
        update_ledger(
            request_id,
            {
                "state": "RESPONSE_RECORDED",
                "completed_at": now_utc().isoformat(),
                "http_status": response.status_code,
                "rate_limit_body_error": rate_limit_error,
                "http_429": response.status_code == 429,
            },
        )
        if response.status_code == 429:
            raise RuntimeError("HTTP_429_STOP")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP_{response.status_code}_STOP")
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_JSON_STOP")
        if rate_limit_error:
            raise RuntimeError("BODY_RATE_LIMIT_STOP")
        if payload.get("errors"):
            raise RuntimeError("APPLICATION_ERROR_STOP")
        if response.headers.get("x-ratelimit-requests-remaining") == "0":
            raise RuntimeError("DAILY_QUOTA_EXHAUSTED_STOP")
        if response.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("MINUTE_QUOTA_EXHAUSTED_STOP")
        return payload


def checkpoint_path(fixture_id: int, slot: str) -> Path:
    return ROOT / f"fixture_{fixture_id}_{slot.lower()}.json"


def eligible_slot(kickoff: datetime, now: datetime) -> str | None:
    return checkpoint((kickoff - now).total_seconds() / 60)


def discovery_candidate(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    fixture = row.get("fixture") or {}
    league = row.get("league") or {}
    teams = row.get("teams") or {}
    kickoff = parse_time(fixture.get("date"))
    fixture_id = fixture.get("id")
    if (
        not isinstance(fixture_id, int)
        or kickoff is None
        or kickoff <= now + timedelta(minutes=100)
        or (fixture.get("status") or {}).get("short") not in {"NS", "TBD"}
        or league.get("id") not in LEAGUE_BY_ID
    ):
        return None
    return {
        "fixture_id": fixture_id,
        "competition": LEAGUE_BY_ID[league["id"]],
        "league_id": league["id"],
        "home": (teams.get("home") or {}).get("name"),
        "away": (teams.get("away") or {}).get("name"),
        "kickoff_utc": kickoff.isoformat(),
        "kickoff_europe_paris": kickoff.astimezone(PARIS).isoformat(),
        "T90": (kickoff - timedelta(minutes=90)).astimezone(PARIS).isoformat(),
        "T60": (kickoff - timedelta(minutes=60)).astimezone(PARIS).isoformat(),
        "T30": (kickoff - timedelta(minutes=30)).astimezone(PARIS).isoformat(),
    }


async def discover() -> dict[str, Any]:
    write_preflight()
    settings = Settings()
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY_NOT_CONFIGURED")
    today = now_utc().astimezone(PARIS).date()
    candidates: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=settings.api_football_base_url,
        timeout=20.0,
        headers={"x-apisports-key": settings.api_football_key},
    ) as client:
        reader = AuditedReader(client, None, "DISCOVERY")
        for league_id in LEAGUE_BY_ID:
            payload = await reader.get(
                "/fixtures",
                {
                    "league": league_id,
                    "season": 2026,
                    "from": today.isoformat(),
                    "to": (today + timedelta(days=7)).isoformat(),
                },
            )
            for row in payload.get("response") or []:
                if isinstance(row, dict):
                    item = discovery_candidate(row, now_utc())
                    if item is not None:
                        candidates.append(item)
    if not candidates:
        return {"status": "NO_FUTURE_FIVE_LEAGUE_FIXTURE", "candidates": 0}
    selected = min(candidates, key=lambda item: item["kickoff_utc"])
    if MANIFEST.exists():
        raise RuntimeError("MANIFEST_ALREADY_EXISTS")
    atomic_json(MANIFEST, selected)
    return {"status": "FIXTURE_SELECTED", **selected, "candidate_count": len(candidates)}


async def network_check() -> dict[str, Any]:
    """One ledgered fixture lookup, without querying odds or rewriting checkpoints."""
    write_preflight()
    selected = manifest()
    settings = Settings()
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY_NOT_CONFIGURED")
    fixture_id = int(selected["fixture_id"])
    async with httpx.AsyncClient(
        base_url=settings.api_football_base_url,
        timeout=20.0,
        headers={"x-apisports-key": settings.api_football_key},
    ) as client:
        payload = await AuditedReader(client, fixture_id, "NETWORK_CHECK").get(
            "/fixtures", {"id": fixture_id}
        )
    rows = payload.get("response") or []
    if len(rows) != 1 or (rows[0].get("fixture") or {}).get("id") != fixture_id:
        raise RuntimeError("NETWORK_CHECK_FIXTURE_MISMATCH")
    return {"status": "NETWORK_CHECK_PASS", "fixture_id": fixture_id}


def manifest() -> dict[str, Any]:
    selected = read_json(MANIFEST)
    if (
        not isinstance(selected.get("fixture_id"), int)
        or parse_time(selected.get("kickoff_utc")) is None
    ):
        raise RuntimeError("INVALID_FIXTURE_MANIFEST")
    return selected


async def observe(slot: str) -> dict[str, Any]:
    selected = manifest()
    fixture_id = int(selected["fixture_id"])
    kickoff = parse_time(selected["kickoff_utc"])
    assert kickoff is not None
    path = checkpoint_path(fixture_id, slot)
    if path.exists():
        return {"status": "CHECKPOINT_ALREADY_RECORDED", "checkpoint": slot}
    record: dict[str, Any] = {
        "fixture_id": fixture_id,
        "checkpoint": slot,
        "scheduled_at": selected[slot],
        "started_at": now_utc().isoformat(),
        "request_attempted": False,
        "request_completed": False,
        "http_status": None,
        "provider_timestamp": None,
        "captured_at": None,
        "Bet365": None,
        "Pinnacle": None,
        "error": None,
    }
    atomic_json(path, record)
    try:
        write_preflight()
        if eligible_slot(kickoff, now_utc()) != slot:
            raise RuntimeError("NOT_NATURALLY_ELIGIBLE")
        settings = Settings()
        if not settings.api_football_key:
            raise RuntimeError("API_FOOTBALL_KEY_NOT_CONFIGURED")
        async with httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            timeout=20.0,
            headers={"x-apisports-key": settings.api_football_key},
        ) as client:
            reader = AuditedReader(client, fixture_id, slot)
            record["request_attempted"] = True
            atomic_json(path, record)
            fixture_payload = await reader.get("/fixtures", {"id": fixture_id})
            matches = fixture_payload.get("response") or []
            if len(matches) != 1:
                raise RuntimeError("FIXTURE_NOT_UNIQUE_OR_MISSING")
            row = matches[0]
            fixture = row.get("fixture") or {}
            league = row.get("league") or {}
            if (
                fixture.get("id") != fixture_id
                or league.get("id") != selected["league_id"]
                or parse_time(fixture.get("date")) != kickoff
                or (fixture.get("status") or {}).get("short") not in {"NS", "TBD"}
                or eligible_slot(kickoff, now_utc()) != slot
            ):
                raise RuntimeError("FIXTURE_OR_CHECKPOINT_MISMATCH")
            odds_payload = await reader.get("/odds", {"fixture": fixture_id, "bet": 1})
            for event in odds_payload.get("response") or []:
                if isinstance(event, dict) and isinstance(event.get("fixture"), dict):
                    returned_id = event["fixture"].get("id")
                    if returned_id is not None and returned_id != fixture_id:
                        raise RuntimeError("ODDS_FIXTURE_ID_MISMATCH")
            captured_at = now_utc().isoformat()
            observation = extract_odds(odds_payload, selected, captured_at)
            record["captured_at"] = captured_at
            for bookmaker in BOOKMAKERS:
                record[bookmaker] = next(
                    (quote for quote in observation["quotes"] if quote["bookmaker"] == bookmaker),
                    {"bookmaker": bookmaker, "freshness": "UNAVAILABLE"},
                )
            record["provider_timestamp"] = {
                bookmaker: (record[bookmaker] or {}).get("provider_odds_update_at")
                for bookmaker in BOOKMAKERS
            }
            record["request_completed"] = True
            record["http_status"] = 200
            record["status"] = "CHECKPOINT_RECORDED"
    except (RuntimeError, OSError) as exc:
        record["status"] = "CHECKPOINT_FAILED"
        record["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    atomic_json(path, record)
    return {"status": record["status"], "checkpoint": slot, "error": record["error"]}


def mark_unavailable(slot: str, reason: str) -> dict[str, Any]:
    """Preserve a skipped checkpoint when unattended network permission fails."""
    selected = manifest()
    fixture_id = int(selected["fixture_id"])
    path = checkpoint_path(fixture_id, slot)
    if path.exists():
        return {"status": "CHECKPOINT_ALREADY_RECORDED", "checkpoint": slot}
    write_preflight()
    kickoff = parse_time(selected["kickoff_utc"])
    assert kickoff is not None
    eligible = eligible_slot(kickoff, now_utc()) == slot
    record = {
        "fixture_id": fixture_id,
        "checkpoint": slot,
        "scheduled_at": selected[slot],
        "started_at": now_utc().isoformat(),
        "request_attempted": False,
        "request_completed": False,
        "http_status": None,
        "provider_timestamp": None,
        "captured_at": None,
        "Bet365": {"freshness": "UNAVAILABLE"},
        "Pinnacle": {"freshness": "UNAVAILABLE"},
        "error": reason if eligible else "NOT_NATURALLY_ELIGIBLE",
        "status": "CHECKPOINT_SKIPPED",
    }
    atomic_json(path, record)
    return {"status": record["status"], "checkpoint": slot, "error": record["error"]}


def summarize() -> dict[str, Any]:
    selected = manifest()
    fixture_id = int(selected["fixture_id"])
    result: dict[str, Any] = {
        "FIXTURE_ID": fixture_id,
        "FIXTURE": f"{selected['home']} vs {selected['away']}",
        "KICKOFF": selected["kickoff_europe_paris"],
    }
    classifications: list[str] = []
    for bookmaker in BOOKMAKERS:
        for slot in CHECKPOINTS:
            record = read_json(checkpoint_path(fixture_id, slot), {})
            quote = record.get(bookmaker) or {}
            result[f"{slot}_{bookmaker.upper()}_AGE"] = quote.get("odds_age_minutes")
            state = quote.get("freshness", "UNAVAILABLE")
            result[f"{slot}_{bookmaker.upper()}_FRESHNESS"] = state
            classifications.append(state)
        movements = []
        for first, second in (("T90", "T60"), ("T60", "T30")):
            earlier = read_json(checkpoint_path(fixture_id, first), {}).get(bookmaker) or {}
            later = read_json(checkpoint_path(fixture_id, second), {}).get(bookmaker) or {}
            first_ts = earlier.get("provider_odds_update_at")
            later_ts = later.get("provider_odds_update_at")
            comparable = bool(first_ts and later_ts)
            movements.append(
                {
                    "from": first,
                    "to": second,
                    "TIMESTAMP_UPDATED": first_ts != later_ts if comparable else None,
                    "PRICE_UPDATED": (
                        tuple(earlier.get(k) for k in ("home", "draw", "away"))
                        != tuple(later.get(k) for k in ("home", "draw", "away"))
                        if comparable
                        else None
                    ),
                    "earlier_1x2": [earlier.get(k) for k in ("home", "draw", "away")],
                    "later_1x2": [later.get(k) for k in ("home", "draw", "away")],
                }
            )
        result[f"{bookmaker.upper()}_ODDS_MOVEMENT"] = movements
    for state in ("FRESH", "STALE", "UNAVAILABLE"):
        result[f"{state}_OBSERVATIONS"] = classifications.count(state)
    ledger = read_json(LEDGER, {"request_cap": CAP, "requests": []})
    requests = ledger["requests"]
    result["REQUESTS_ATTEMPTED"] = len(requests)
    result["REQUESTS_RECORDED"] = len(requests)
    result["HTTP_200"] = sum(row.get("http_status") == 200 for row in requests)
    result["RATE_LIMIT_ERRORS"] = sum(row.get("rate_limit_body_error") is True for row in requests)
    result["HTTP_429"] = sum(row.get("http_429") is True for row in requests)
    result["RETRIES"] = sum(int(row.get("retry_count", 0)) for row in requests)
    result["EVIDENCE_LOSS"] = sum(
        read_json(checkpoint_path(fixture_id, slot), {}).get("status") is None
        for slot in CHECKPOINTS
    )
    result["API_FOOTBALL_ODDS_FRESH_ENOUGH"] = (
        "SAMPLED_FRESH" if classifications == ["FRESH"] * 6 else "NO_OR_UNPROVEN"
    )
    result["CAN_REPLACE_PAID_ODDS_API"] = "no: one-fixture sample is insufficient"
    result["POINT_IN_TIME_PRODUCTION_READY"] = "no: research-only collector"
    atomic_json(ROOT / f"fixture_{fixture_id}_consolidated.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "discover",
            "network-check",
            "checkpoint",
            "mark-unavailable",
            "summarize",
        ),
    )
    parser.add_argument("--slot", choices=CHECKPOINTS)
    parser.add_argument("--reason", choices=("NETWORK_PERMISSION_UNAVAILABLE",))
    args = parser.parse_args()
    if args.command == "preflight":
        result = write_preflight()
    elif args.command == "discover":
        result = asyncio.run(discover())
    elif args.command == "network-check":
        result = asyncio.run(network_check())
    elif args.command == "checkpoint":
        if args.slot is None:
            parser.error("--slot is required for checkpoint")
        result = asyncio.run(observe(args.slot))
        if args.slot == "T30":
            result["consolidated"] = summarize()
    elif args.command == "mark-unavailable":
        if args.slot is None or args.reason is None:
            parser.error("--slot and --reason are required for mark-unavailable")
        result = mark_unavailable(args.slot, args.reason)
        if args.slot == "T30":
            result["consolidated"] = summarize()
    else:
        result = summarize()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
