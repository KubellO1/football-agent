"""No-network tests for the TASK-075 local research collector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from scripts.research import api_football_multi_fixture_freshness as collector

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "LEDGER", tmp_path / "request_ledger.json")
    return tmp_path


def fixture_row(fixture_id: int = 1) -> dict[str, Any]:
    return {
        "fixture_id": fixture_id,
        "league_id": 39,
        "competition": "Premier League",
        "home": "Home",
        "away": "Away",
        "kickoff_utc": "2026-10-10T11:30:00+00:00",
    }


def test_natural_checkpoint_and_no_backfill() -> None:
    row = fixture_row()
    kickoff = datetime(2026, 10, 10, 11, 30, tzinfo=UTC)
    assert collector.is_naturally_due(row, "T60", kickoff - timedelta(minutes=60))
    assert collector.is_naturally_due(
        row, "T60", kickoff - timedelta(minutes=60) + timedelta(minutes=4)
    )
    assert not collector.is_naturally_due(
        row, "T60", kickoff - timedelta(minutes=60) + timedelta(minutes=6)
    )
    assert not collector.is_naturally_due(row, "T30", kickoff - timedelta(minutes=60))
    assert collector.is_naturally_due(row, "T30", kickoff - timedelta(minutes=30))
    assert not collector.is_naturally_due(
        row, "T30", kickoff - timedelta(minutes=29) + timedelta(minutes=6)
    )


def test_validate_fixture_rejects_changed_kickoff_or_identity() -> None:
    row = fixture_row()
    payload = {
        "response": [
            {
                "fixture": {"id": 1, "date": row["kickoff_utc"], "status": {"short": "NS"}},
                "league": {"id": 39},
                "teams": {"home": {"name": "Home"}, "away": {"name": "Away"}},
            }
        ]
    }
    collector.validate_fixture(payload, row)
    payload["response"][0]["fixture"]["date"] = "2026-10-10T12:30:00+00:00"
    with pytest.raises(RuntimeError, match="FIXTURE_IDENTITY_OR_KICKOFF_CHANGED"):
        collector.validate_fixture(payload, row)


@pytest.mark.asyncio
async def test_body_rate_limit_is_failure_with_post_request_evidence(
    isolated: Path,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"errors": {"rateLimit": "slow down"}})
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        reader = collector.AuditedReader(client, 1, "T60")
        with pytest.raises(RuntimeError, match="BODY_RATE_LIMIT_STOP"):
            await reader.get("/fixtures", {"id": 1})
    requests = collector.read_json(isolated / "request_ledger.json")["requests"]
    assert len(requests) == 1
    assert requests[0]["state"] == "POST_REQUEST"
    assert requests[0]["http_status"] == 200
    assert requests[0]["body_rate_limit_error"] is True
    assert requests[0]["retries"] == 0


@pytest.mark.asyncio
async def test_request_cap_fails_closed_before_second_request(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "MAX_REQUESTS", 1)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"response": [], "errors": {}})
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        reader = collector.AuditedReader(client, 1, "T60")
        await reader.get("/fixtures", {"id": 1})
        with pytest.raises(RuntimeError, match="RUN_REQUEST_BUDGET_EXHAUSTED"):
            await reader.get("/odds", {"fixture": 1})
    assert len(collector.read_json(isolated / "request_ledger.json")["requests"]) == 1


@pytest.mark.asyncio
async def test_no_due_checkpoint_makes_no_request(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "manifest", lambda: [fixture_row()])
    monkeypatch.setattr(collector, "now_utc", lambda: datetime(2026, 10, 9, 11, 0, tzinfo=UTC))
    monkeypatch.setattr(
        collector,
        "Settings",
        lambda: type(
            "SettingsStub",
            (),
            {"api_football_key": "test-only", "api_football_base_url": "https://example.test"},
        )(),
    )
    assert (await collector.collect_due())["status"] == "NO_NATURALLY_DUE_CHECKPOINT"
    assert not (isolated / "request_ledger.json").exists()


@pytest.mark.asyncio
async def test_same_instant_two_fixtures_are_collected_in_one_run(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = fixture_row(1)
    first["kickoff_utc"] = "2026-10-09T19:30:00+00:00"
    second = fixture_row(2)
    second["kickoff_utc"] = "2026-10-09T20:00:00+00:00"
    other = fixture_row(3)
    monkeypatch.setattr(collector, "manifest", lambda: [first, second, other, other, other])
    monkeypatch.setattr(collector, "now_utc", lambda: datetime(2026, 10, 9, 19, 0, 2, tzinfo=UTC))
    monkeypatch.setattr(
        collector,
        "Settings",
        lambda: type(
            "SettingsStub",
            (),
            {"api_football_key": "test-only", "api_football_base_url": "https://example.test"},
        )(),
    )
    calls: list[tuple[int, str]] = []

    class ClientStub:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> ClientStub:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def fake_collect(client: object, row: dict[str, Any], slot: str) -> dict[str, Any]:
        calls.append((row["fixture_id"], slot))
        return {"status": "CHECKPOINT_RECORDED", "fixture_id": row["fixture_id"]}

    monkeypatch.setattr(collector.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(collector, "collect_one", fake_collect)
    result = await collector.collect_due()
    assert result["status"] == "COLLECTION_FINISHED"
    assert calls == [(1, "T30"), (2, "T60")]


@pytest.mark.asyncio
async def test_complete_quote_persists_and_replay_is_idempotent(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = fixture_row()
    captured = datetime(2026, 10, 10, 10, 30, 2, tzinfo=UTC)
    monkeypatch.setattr(collector, "now_utc", lambda: captured)

    class ReaderStub:
        calls = 0

        def __init__(self, client: object, fixture_id: int, slot: str) -> None:
            assert fixture_id == 1 and slot == "T60"

        async def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            ReaderStub.calls += 1
            if endpoint == "/fixtures":
                return {
                    "response": [
                        {
                            "fixture": {
                                "id": 1,
                                "date": row["kickoff_utc"],
                                "status": {"short": "NS"},
                            },
                            "league": {"id": 39},
                            "teams": {"home": {"name": "Home"}, "away": {"name": "Away"}},
                        }
                    ]
                }
            return {
                "response": [
                    {
                        "fixture": {"id": 1},
                        "update": "2026-10-10T10:25:00+00:00",
                        "bookmakers": [
                            {
                                "id": 8,
                                "name": "Bet365",
                                "bets": [
                                    {
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "2.00"},
                                            {"value": "Draw", "odd": "3.25"},
                                            {"value": "Away", "odd": "3.75"},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    monkeypatch.setattr(collector, "AuditedReader", ReaderStub)
    first = await collector.collect_one(object(), row, "T60")  # type: ignore[arg-type]
    assert first["status"] == "CHECKPOINT_RECORDED"
    record = collector.read_json(isolated / "fixture_1_t60.json")
    assert record["bookmakers"]["Bet365"]["freshness"] == "FRESH"
    assert record["bookmakers"]["Bet365"]["provider_odds_update_at"] == "2026-10-10T10:25:00+00:00"
    assert record["bookmakers"]["Pinnacle"]["freshness"] == "UNAVAILABLE"
    assert record["captured_at"] != record["bookmakers"]["Bet365"]["provider_odds_update_at"]
    second = await collector.collect_one(object(), row, "T60")  # type: ignore[arg-type]
    assert second["status"] == "ALREADY_RECORDED"
    assert ReaderStub.calls == 2


def test_summary_counts_unavailable_and_never_calls_it_suitable(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [fixture_row(i) for i in range(1, 6)]
    monkeypatch.setattr(collector, "manifest", lambda: rows)
    for row in rows:
        for slot in ("T60", "T30"):
            age = 15.0 if slot == "T60" else 45.0
            collector.atomic_json(
                collector.evidence_path(row["fixture_id"], slot),
                {
                    "status": "CHECKPOINT_RECORDED",
                    "captured_at": "2026-10-10T10:30:00+00:00",
                    "bookmakers": {
                        "Bet365": {
                            "freshness": "FRESH" if slot == "T60" else "STALE",
                            "odds_age_minutes": age,
                            "provider_odds_update_at": "2026-10-10T10:15:00+00:00",
                            "timestamp_scope": "event",
                        },
                        "Pinnacle": {"freshness": "UNAVAILABLE"},
                    },
                },
            )
    summary = collector.summarize()
    assert summary["BET365_T60_FRESH_RATE"] == 1.0
    assert summary["BET365_T30_FRESH_RATE"] == 0.0
    assert summary["PINNACLE_T60_FRESH_RATE"] == 0.0
    assert summary["MEDIAN_ODDS_AGE_T60"] == 15.0
    assert summary["MEDIAN_ODDS_AGE_T30"] == 45.0
    assert summary["MAX_ODDS_AGE"] == 45.0
    assert summary["TIMESTAMP_UPDATE_RATE"] == 0.0
    assert summary["API_FOOTBALL_ODDS_PRODUCTION_SUITABLE"] is False
    assert len(summary["OBSERVATIONS"]) == 20
    assert json.loads((isolated / "consolidated.json").read_text(encoding="utf-8")) == summary
