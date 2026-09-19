"""Research-only normalization for documented PinnWire/Pinnodds REST boards.

This module performs no HTTP requests and is not registered in production. The
two technical feeds both represent a single canonical Pinnacle bookmaker.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

Source = Literal["pinnwire", "pinnodds"]
Selection = Literal["home", "draw", "away"]
PARSER_VERSION = "pinnacle-prematch-1x2-v1"


@dataclass(frozen=True)
class PinnacleQuote:
    source: Source
    provider_event_id: str
    bookmaker_key: str
    league_name: str
    home_team: str
    away_team: str
    kickoff: datetime
    selection: Selection
    decimal_odds: Decimal
    captured_at: datetime
    source_timestamp: None
    raw_payload_hash: str
    parser_version: str = PARSER_VERSION


@dataclass(frozen=True)
class RejectedEvent:
    provider_event_id: str | None
    reason_code: str


@dataclass(frozen=True)
class ParseResult:
    quotes: tuple[PinnacleQuote, ...]
    rejected: tuple[RejectedEvent, ...]


def parse_prematch_board(
    raw_payload: bytes, *, source: Source, captured_at: datetime
) -> ParseResult:
    """Parse complete full-time soccer 1X2 quotes without inventing source time.

    ``captured_at`` must be the actual UTC observation time supplied by the HTTP
    boundary. The API's ``last`` cursor/serve time is not a price-update time.
    """
    if source not in ("pinnwire", "pinnodds"):
        raise ValueError("Unsupported Pinnacle feed")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    captured_at = captured_at.astimezone(UTC)
    payload: Any = json.loads(raw_payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("Prematch board requires an events array")

    digest = hashlib.sha256(raw_payload).hexdigest()
    quotes: list[PinnacleQuote] = []
    rejected: list[RejectedEvent] = []
    for item in payload["events"]:
        if not isinstance(item, dict):
            rejected.append(RejectedEvent(None, "INVALID_EVENT"))
            continue
        event_id = str(item["event_id"]) if item.get("event_id") is not None else None
        if item.get("sport_id") != 1 or item.get("event_type", "prematch") != "prematch":
            rejected.append(RejectedEvent(event_id, "NOT_PREMATCH_SOCCER"))
            continue
        league = item.get("league_name")
        home = item.get("home")
        away = item.get("away")
        starts = item.get("starts") or item.get("start_ts")
        if (
            not event_id
            or not isinstance(league, str)
            or not league.strip()
            or not isinstance(home, str)
            or not home.strip()
            or not isinstance(away, str)
            or not away.strip()
            or not isinstance(starts, str)
            or not starts.strip()
        ):
            rejected.append(RejectedEvent(event_id, "MISSING_EVENT_IDENTITY"))
            continue
        try:
            kickoff = datetime.fromisoformat(starts.replace("Z", "+00:00"))
        except ValueError:
            rejected.append(RejectedEvent(event_id, "INVALID_KICKOFF"))
            continue
        if kickoff.tzinfo is None or kickoff.utcoffset() is None:
            rejected.append(RejectedEvent(event_id, "INVALID_KICKOFF"))
            continue
        period = (
            item.get("periods", {}).get("num_0") if isinstance(item.get("periods"), dict) else None
        )
        prices = period.get("money_line") if isinstance(period, dict) else None
        if not isinstance(prices, dict):
            rejected.append(RejectedEvent(event_id, "NO_FULL_TIME_1X2"))
            continue
        parsed: dict[str, Decimal] = {}
        for selection in ("home", "draw", "away"):
            value = prices.get(selection)
            if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
                break
            try:
                price = Decimal(str(value))
            except InvalidOperation:
                break
            if not price.is_finite() or price <= 1:
                break
            parsed[selection] = price
        if len(parsed) != 3:
            rejected.append(RejectedEvent(event_id, "INCOMPLETE_1X2"))
            continue
        for selection, price in parsed.items():
            quotes.append(
                PinnacleQuote(
                    source=source,
                    provider_event_id=event_id,
                    bookmaker_key="pinnacle",
                    league_name=league.strip(),
                    home_team=home.strip(),
                    away_team=away.strip(),
                    kickoff=kickoff.astimezone(UTC),
                    selection=cast("Selection", selection),
                    decimal_odds=price,
                    captured_at=captured_at,
                    source_timestamp=None,
                    raw_payload_hash=digest,
                )
            )
    return ParseResult(tuple(quotes), tuple(rejected))
