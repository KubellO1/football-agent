"""Three one-shot, local API-Football odds observations for fixture 1550130.

Invoked by a Codex local-project automation. It writes only sanitized evidence
outside the repository; it does not touch PostgreSQL or production jobs.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import httpx
from api_football_near_kickoff_freshness import (
    PARIS,
    PacedReader,
    checkpoint,
    extract_odds,
    parse_time,
)

from app.config.settings import Settings

FIXTURE_ID = 1550130
LEAGUE_ID = 135  # Serie A
KICKOFF = datetime.fromisoformat("2026-09-20T12:30:00+02:00").astimezone(UTC)
EVIDENCE = Path(
    "C:/Users/ruowa/Projects/football-agent-task-artifacts/"
    "TASK-20260920-073/fixture_1550130_checkpoints.json"
)
CONSOLIDATED = EVIDENCE.with_name("fixture_1550130_consolidated.json")
TOTAL_REQUEST_CAP = 25
BOOKMAKERS = ("Bet365", "Pinnacle")
CHECKPOINTS = ("T90", "T60", "T30")


def load_evidence() -> dict[str, Any]:
    if EVIDENCE.exists():
        data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        if data.get("fixture_id") != FIXTURE_ID:
            raise RuntimeError("EVIDENCE_FIXTURE_MISMATCH")
        return data
    return {"fixture_id": FIXTURE_ID, "http_requests": 0, "observations": {}, "attempts": []}


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".freshness-", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def expected_checkpoint(now: datetime) -> str | None:
    minutes = (KICKOFF - now).total_seconds() / 60
    return checkpoint(minutes)


def record_attempt(data: dict[str, Any], reader: PacedReader, outcome: str) -> None:
    data["http_requests"] = int(data["http_requests"]) + reader.requests
    data["attempts"].append(
        {
            "at_utc": datetime.now(UTC).isoformat(),
            "outcome": outcome,
            "requests": reader.requests,
            "http_200": reader.http_200,
            "body_rate_limit_errors": reader.body_rate_limits,
            "http_429": reader.http_429,
            "retries": 0,
            "request_timeline": reader.timeline,
        }
    )
    save_json(EVIDENCE, data)


def quote_at(data: dict[str, Any], checkpoint_name: str, bookmaker: str) -> dict[str, Any] | None:
    observation = data["observations"].get(checkpoint_name) or {}
    return next(
        (quote for quote in observation.get("quotes", []) if quote.get("bookmaker") == bookmaker),
        None,
    )


def consolidate(data: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"FIXTURE_ID": FIXTURE_ID}
    classifications: list[str] = []
    ages: list[float] = []
    for bookmaker in BOOKMAKERS:
        label = bookmaker.upper()
        for checkpoint_name in CHECKPOINTS:
            quote = quote_at(data, checkpoint_name, bookmaker)
            output[f"{checkpoint_name}_{label}_AGE"] = (
                quote.get("odds_age_minutes") if quote is not None else None
            )
            output[f"{checkpoint_name}_{label}_CLASSIFICATION"] = (
                quote.get("freshness") if quote is not None else "UNAVAILABLE"
            )
            output[f"{checkpoint_name}_{label}_TIMESTAMP_SCOPE"] = (
                quote.get("timestamp_scope") if quote is not None else "unavailable"
            )
            output[f"{checkpoint_name}_{label}_HOME"] = quote.get("home") if quote else None
            output[f"{checkpoint_name}_{label}_DRAW"] = quote.get("draw") if quote else None
            output[f"{checkpoint_name}_{label}_AWAY"] = quote.get("away") if quote else None
            classifications.append(output[f"{checkpoint_name}_{label}_CLASSIFICATION"])
            if quote is not None and quote.get("odds_age_minutes") is not None:
                ages.append(float(quote["odds_age_minutes"]))

        pairs: list[dict[str, Any]] = []
        for earlier, later in (("T90", "T60"), ("T60", "T30")):
            first = quote_at(data, earlier, bookmaker)
            second = quote_at(data, later, bookmaker)
            pairs.append(
                {
                    "from": earlier,
                    "to": later,
                    "timestamp_updated": (
                        first.get("provider_odds_update_at")
                        != second.get("provider_odds_update_at")
                        if first and second
                        else None
                    ),
                    "price_moved": (
                        (first.get("home"), first.get("draw"), first.get("away"))
                        != (second.get("home"), second.get("draw"), second.get("away"))
                        if first and second
                        else None
                    ),
                }
            )
        output[f"{label}_ODDS_MOVEMENT"] = pairs

    comparisons = [
        pair["timestamp_updated"]
        for bookmaker in BOOKMAKERS
        for pair in output[f"{bookmaker.upper()}_ODDS_MOVEMENT"]
        if pair["timestamp_updated"] is not None
    ]
    output["ODDS_TIMESTAMP_UPDATES_OBSERVED"] = any(comparisons) if comparisons else None
    for state in ("FRESH", "STALE", "UNAVAILABLE"):
        output[f"{state}_OBSERVATIONS"] = classifications.count(state)
    output["MAX_ODDS_AGE"] = max(ages) if ages else None
    output["MEDIAN_ODDS_AGE"] = median(ages) if ages else None
    attempts = data["attempts"]
    output["HTTP_REQUESTS"] = data["http_requests"]
    output["RATE_LIMIT_ERRORS"] = sum(item["body_rate_limit_errors"] for item in attempts)
    output["HTTP_429"] = sum(item["http_429"] for item in attempts)
    output["RETRIES"] = sum(item["retries"] for item in attempts)
    # A single fixture and potentially event-level timestamps are not enough
    # to certify a replacement production odds feed, even if all six ages pass.
    output["API_FOOTBALL_ODDS_FRESH_ENOUGH"] = (
        "SAMPLED_FRESH" if classifications == ["FRESH"] * 6 else "NO_OR_UNPROVEN"
    )
    output["CAN_REPLACE_PAID_ODDS_API"] = "no: single-fixture sample only"
    output["POINT_IN_TIME_PRODUCTION_READY"] = "no: not deployed or integration-tested"
    output["frozen_freshness_minutes"] = 30
    output["fixture_kickoff_utc"] = KICKOFF.isoformat()
    output["observations"] = data["observations"]
    return output


async def run() -> None:
    now = datetime.now(UTC)
    due = expected_checkpoint(now)
    data = load_evidence()
    final_slot = now.astimezone(PARIS) >= datetime.fromisoformat("2026-09-20T12:00:00+02:00")
    if due is None:
        if final_slot:
            save_json(CONSOLIDATED, consolidate(data))
        print(
            json.dumps(
                {
                    "status": "NOT_NATURALLY_ELIGIBLE",
                    "at_europe_paris": now.astimezone(PARIS).isoformat(),
                }
            )
        )
        return
    if due in data["observations"]:
        if final_slot:
            save_json(CONSOLIDATED, consolidate(data))
        print(json.dumps({"status": "CHECKPOINT_ALREADY_RECORDED", "checkpoint": due}))
        return
    if data["http_requests"] + 2 > TOTAL_REQUEST_CAP:
        if final_slot:
            save_json(CONSOLIDATED, consolidate(data))
        print(json.dumps({"status": "TOTAL_REQUEST_BUDGET_EXHAUSTED", "checkpoint": due}))
        return
    settings = Settings()
    if not settings.api_football_key:
        if final_slot:
            save_json(CONSOLIDATED, consolidate(data))
        print(json.dumps({"status": "API_FOOTBALL_KEY_NOT_CONFIGURED"}))
        return
    async with httpx.AsyncClient(
        base_url=settings.api_football_base_url,
        timeout=20.0,
        headers={"x-apisports-key": settings.api_football_key},
    ) as client:
        reader = PacedReader(client)
        outcome = "UNKNOWN"
        try:
            fixture_payload = await reader.get("/fixtures", {"id": FIXTURE_ID})
            matches = fixture_payload.get("response") or []
            if len(matches) != 1:
                raise RuntimeError("FIXTURE_NOT_UNIQUE_OR_MISSING")
            fixture_row = matches[0]
            fixture = fixture_row.get("fixture") or {}
            league = fixture_row.get("league") or {}
            actual_kickoff = parse_time(fixture.get("date"))
            if (
                fixture.get("id") != FIXTURE_ID
                or league.get("id") != LEAGUE_ID
                or actual_kickoff != KICKOFF
                or (fixture.get("status") or {}).get("short") not in {"NS", "TBD"}
                or expected_checkpoint(datetime.now(UTC)) != due
            ):
                raise RuntimeError("FIXTURE_OR_CHECKPOINT_MISMATCH")
            odds_payload = await reader.get("/odds", {"fixture": FIXTURE_ID, "bet": 1})
            captured_at = reader.timeline[-1]["captured_at"]
            selected = {
                "fixture_id": FIXTURE_ID,
                "competition": "Serie A",
                "kickoff_utc": KICKOFF.isoformat(),
                "kickoff_europe_paris": KICKOFF.astimezone(PARIS).isoformat(),
                "due_checkpoint": due,
            }
            observation = extract_odds(odds_payload, selected, captured_at)
            observation["checkpoint"] = due
            for bookmaker in BOOKMAKERS:
                if not any(q.get("bookmaker") == bookmaker for q in observation["quotes"]):
                    observation["quotes"].append(
                        {
                            "bookmaker": bookmaker,
                            "market": "1X2",
                            "home": None,
                            "draw": None,
                            "away": None,
                            "provider_odds_update_at": None,
                            "timestamp_scope": "unavailable",
                            "odds_age_minutes": None,
                            "freshness": "UNAVAILABLE",
                        }
                    )
            data["observations"][due] = observation
            outcome = "CHECKPOINT_RECORDED"
        except RuntimeError as exc:
            outcome = str(exc)
        finally:
            record_attempt(data, reader, outcome)
            if final_slot:
                save_json(CONSOLIDATED, consolidate(data))
        print(
            json.dumps(
                {
                    "status": outcome,
                    "checkpoint": due,
                    "total_requests": data["http_requests"],
                    "evidence_path": str(EVIDENCE),
                    "consolidated_path": str(CONSOLIDATED) if due == "T30" else None,
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(run())
