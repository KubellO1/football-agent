"""Run the isolated, append-only zero-cost intelligence collector Canary."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.intelligence.adapters import (
    FIVE_LEAGUES,
    MetNoWeatherAdapter,
    OpenFootballJsonAdapter,
)
from app.intelligence.collection import (
    ContentAddressedArchive,
    JsonlObservationStore,
    SourceHealthTracker,
)
from app.intelligence.contracts import CaptureWindow, DataType, SourcePolicy
from app.intelligence.http import CachedHttpClient

if TYPE_CHECKING:
    from app.intelligence.contracts import Observation

MET_NO_CANARY_URL = (
    "https://api.met.no/weatherapi/locationforecast/2.0/compact" "?lat=48.8566&lon=2.3522"
)


@dataclass(frozen=True, slots=True)
class CanarySummary:
    season: str
    sources_attempted: int
    external_requests: int
    cache_hits: int
    raw_payloads_created: int
    observations_inserted: int
    duplicate_observations: int
    fixture_observations: int
    post_match_observations: int
    weather_observations: int
    source_errors: dict[str, str]
    source_health: dict[str, dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_canary(
    *,
    output_dir: Path,
    client: CachedHttpClient,
    season: str,
    now: datetime,
) -> CanarySummary:
    """Collect public zero-cost sources into an isolated append-only store."""

    archive = ContentAddressedArchive(output_dir / "raw")
    store = JsonlObservationStore(output_dir / "observations.jsonl")
    health = SourceHealthTracker()
    observations: list[Observation] = []
    source_errors: dict[str, str] = {}
    cache_hits = 0
    raw_created = 0
    request_start = client.requests

    for league in FIVE_LEAGUES:
        source = f"openfootball-{league.code}"
        try:
            result = client.get_json(
                league.url(season),
                source=source,
                source_policy=SourcePolicy.OPEN_DATA,
                cache_ttl=timedelta(hours=12),
            )
            cache_hits += int(result.cache_hit)
            raw_created += int(archive.archive(result.payload).created)
            parsed = OpenFootballJsonAdapter().parse(
                result.payload,
                league=league,
                season=season,
                capture_window=CaptureWindow.DAILY,
            )
            observations.extend(parsed)
            health.record(
                source,
                success=True,
                empty=not parsed,
                captured_at=result.payload.captured_at,
            )
        except Exception as exc:  # 每个公开来源独立降级，避免单源中断整体采集
            error_code = type(exc).__name__
            source_errors[source] = error_code
            health.record(
                source,
                success=False,
                empty=False,
                captured_at=now.astimezone(UTC),
                error_code=error_code,
            )

    weather_source = "met-no-locationforecast"
    try:
        result = client.get_json(
            MET_NO_CANARY_URL,
            source=weather_source,
            source_policy=SourcePolicy.FREE_OFFICIAL_API,
            cache_ttl=timedelta(hours=1),
        )
        cache_hits += int(result.cache_hit)
        raw_created += int(archive.archive(result.payload).created)
        weather = MetNoWeatherAdapter().parse(
            result.payload,
            fixture_id=f"canary:paris-weather:{now.date().isoformat()}",
            kickoff=now + timedelta(hours=24),
            capture_window=CaptureWindow.T24H,
        )
        observations.append(weather)
        health.record(
            weather_source,
            success=True,
            empty=False,
            captured_at=result.payload.captured_at,
        )
    except Exception as exc:  # 天气适配器 Canary 不影响赛程来源验证
        error_code = type(exc).__name__
        source_errors[weather_source] = error_code
        health.record(
            weather_source,
            success=False,
            empty=False,
            captured_at=now.astimezone(UTC),
            error_code=error_code,
        )

    append_result = store.append(observations)
    health_rows: dict[str, dict[str, object]] = {}
    sources = [*(f"openfootball-{league.code}" for league in FIVE_LEAGUES), weather_source]
    for source in sources:
        snapshot = health.snapshot(source)
        health_rows[source] = {
            "attempts": snapshot.attempts,
            "successes": snapshot.successes,
            "empty_results": snapshot.empty_results,
            "failures": snapshot.failures,
            "last_success_at": (
                snapshot.last_success_at.isoformat() if snapshot.last_success_at else None
            ),
            "last_error_code": snapshot.last_error_code,
        }
    return CanarySummary(
        season=season,
        sources_attempted=len(sources),
        external_requests=client.requests - request_start,
        cache_hits=cache_hits,
        raw_payloads_created=raw_created,
        observations_inserted=append_result.inserted,
        duplicate_observations=append_result.duplicates,
        fixture_observations=sum(item.data_type is DataType.FIXTURE for item in observations),
        post_match_observations=sum(
            item.data_type is DataType.POST_MATCH_STATISTICS for item in observations
        ),
        weather_observations=sum(item.data_type is DataType.WEATHER for item in observations),
        source_errors=source_errors,
        source_health=health_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--request-budget", type=int, default=6)
    args = parser.parse_args()
    now = datetime.now(UTC)
    client = CachedHttpClient(
        args.output_dir / "cache",
        request_budget=args.request_budget,
        user_agent="FootballAgent-ZeroCost/1.0 (+https://github.com/KubellO1/football-agent)",
        minimum_interval_seconds=1.0,
    )
    try:
        summary = run_canary(
            output_dir=args.output_dir,
            client=client,
            season=args.season,
            now=now,
        )
    finally:
        client.close()
    print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
