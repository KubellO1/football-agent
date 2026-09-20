"""No-network contracts for crash-safe scheduled odds evidence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from scripts.research import scheduled_api_football_freshness_v2 as module


@pytest.fixture
def evidence_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "LEDGER", tmp_path / "request_ledger.json")
    monkeypatch.setattr(module, "MANIFEST", tmp_path / "fixture.json")
    return tmp_path


def test_write_preflight_creates_reads_updates_and_deletes(evidence_root: Path) -> None:
    result = module.write_preflight()
    assert result["WRITE_PREFLIGHT"] == "PASS"
    assert module.read_json(evidence_root / "write_preflight.json") == result
    assert list(evidence_root.glob(".write-test-*.json")) == []


def test_request_ledger_reserves_before_network_and_fails_closed_at_cap(
    evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "CAP", 1)
    first = module.update_ledger(None, {"endpoint_class": "fixtures"})
    assert first["state"] == "ATTEMPT_RESERVED"
    with pytest.raises(RuntimeError, match="REQUEST_BUDGET_EXHAUSTED"):
        module.update_ledger(None, {"endpoint_class": "odds"})
    updated = module.update_ledger(first["request_id"], {"http_status": 200})
    assert updated["http_status"] == 200
    assert len(module.read_json(module.LEDGER)["requests"]) == 1


def test_future_fixture_candidate_preserves_real_kickoff() -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    kickoff = now + timedelta(hours=3)
    row = {
        "fixture": {"id": 987654, "date": kickoff.isoformat(), "status": {"short": "NS"}},
        "league": {"id": 39},
        "teams": {"home": {"name": "Home"}, "away": {"name": "Away"}},
    }
    selected = module.discovery_candidate(row, now)
    assert selected is not None
    assert selected["kickoff_utc"] == kickoff.isoformat()
    assert module.parse_time(selected["T90"]) == kickoff - timedelta(minutes=90)
    assert module.parse_time(selected["T60"]) == kickoff - timedelta(minutes=60)
    assert module.parse_time(selected["T30"]) == kickoff - timedelta(minutes=30)
    row["fixture"]["status"]["short"] = "FT"
    assert module.discovery_candidate(row, now) is None


@pytest.mark.asyncio
async def test_ineligible_checkpoint_never_opens_http_client(
    evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kickoff = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)
    module.atomic_json(
        module.MANIFEST,
        {
            "fixture_id": 987654,
            "league_id": 39,
            "kickoff_utc": kickoff.isoformat(),
            "T90": (kickoff - timedelta(minutes=90)).isoformat(),
        },
    )
    monkeypatch.setattr(module, "now_utc", lambda: kickoff - timedelta(hours=4))

    class ForbiddenClient:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("HTTP client must not be created")

    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenClient)
    outcome = await module.observe("T90")
    assert outcome["status"] == "CHECKPOINT_FAILED"
    assert outcome["error"] == "NOT_NATURALLY_ELIGIBLE"
    assert module.read_json(module.LEDGER, {"requests": []})["requests"] == []


def test_missing_quote_remains_unavailable(evidence_root: Path) -> None:
    module.atomic_json(
        module.MANIFEST,
        {
            "fixture_id": 987654,
            "home": "Home",
            "away": "Away",
            "kickoff_utc": "2026-09-22T18:00:00+00:00",
            "kickoff_europe_paris": "2026-09-22T20:00:00+02:00",
        },
    )
    result = module.summarize()
    assert result["UNAVAILABLE_OBSERVATIONS"] == 6
    assert result["FRESH_OBSERVATIONS"] == 0
    assert result["API_FOOTBALL_ODDS_FRESH_ENOUGH"] == "NO_OR_UNPROVEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (200, {"errors": {"rateLimit": "wait"}}, "BODY_RATE_LIMIT_STOP"),
        (429, {"errors": {}}, "HTTP_429_STOP"),
    ],
)
async def test_http_failures_are_recorded_before_abort(
    evidence_root: Path, status: int, payload: dict[str, object], expected: str
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status, json=payload))
    async with httpx.AsyncClient(base_url="https://example.test", transport=transport) as client:
        reader = module.AuditedReader(client, 987654, "T90")
        with pytest.raises(RuntimeError, match=expected):
            await reader.get("/odds", {"fixture": 987654})
    requests = module.read_json(module.LEDGER)["requests"]
    assert len(requests) == 1
    assert requests[0]["state"] == "RESPONSE_RECORDED"
    assert requests[0]["http_status"] == status
    assert requests[0]["rate_limit_body_error"] is (status == 200)
    assert requests[0]["http_429"] is (status == 429)


@pytest.mark.asyncio
async def test_natural_checkpoint_persists_two_named_bookmakers(
    evidence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kickoff = datetime(2026, 9, 20, 16, 30, tzinfo=UTC)
    observed = kickoff - timedelta(minutes=85)
    module.atomic_json(
        module.MANIFEST,
        {
            "fixture_id": 1570402,
            "league_id": 140,
            "competition": "La Liga",
            "home": "Villarreal",
            "away": "Levante",
            "kickoff_utc": kickoff.isoformat(),
            "kickoff_europe_paris": kickoff.astimezone(module.PARIS).isoformat(),
            "T90": (kickoff - timedelta(minutes=90)).astimezone(module.PARIS).isoformat(),
        },
    )
    monkeypatch.setattr(module, "now_utc", lambda: observed)
    monkeypatch.setattr(module, "MIN_INTERVAL_SECONDS", 0.0)

    class FakeSettings:
        api_football_key = "test-only"
        api_football_base_url = "https://example.test"

    monkeypatch.setattr(module, "Settings", FakeSettings)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fixtures":
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "fixture": {
                                "id": 1570402,
                                "date": kickoff.isoformat(),
                                "status": {"short": "NS"},
                            },
                            "league": {"id": 140},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "response": [
                    {
                        "fixture": {"id": 1570402},
                        "update": (observed - timedelta(minutes=15)).isoformat(),
                        "bookmakers": [
                            {
                                "id": bookmaker_id,
                                "name": name,
                                "bets": [
                                    {
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "2.10"},
                                            {"value": "Draw", "odd": "3.20"},
                                            {"value": "Away", "odd": "3.80"},
                                        ],
                                    }
                                ],
                            }
                            for bookmaker_id, name in ((8, "Bet365"), (4, "Pinnacle"))
                        ],
                    }
                ]
            },
        )

    client_class = httpx.AsyncClient

    def mock_client(**_kwargs: object) -> httpx.AsyncClient:
        return client_class(base_url="https://example.test", transport=httpx.MockTransport(respond))

    monkeypatch.setattr(module.httpx, "AsyncClient", mock_client)
    result = await module.observe("T90")
    assert result["status"] == "CHECKPOINT_RECORDED"
    record = module.read_json(module.checkpoint_path(1570402, "T90"))
    assert record["request_attempted"] is True
    assert record["request_completed"] is True
    assert record["Bet365"]["freshness"] == "FRESH"
    assert record["Pinnacle"]["freshness"] == "FRESH"
    assert record["Bet365"]["timestamp_scope"] == "event"
    assert len(module.read_json(module.LEDGER)["requests"]) == 2
