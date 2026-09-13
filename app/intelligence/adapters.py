"""合规零成本来源的纯解析适配器。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    RawPayload,
    SourcePolicy,
    canonical_json,
    utc_datetime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

OPENFOOTBALL_PARSER_VERSION = "openfootball-json-v1"
MET_NO_PARSER_VERSION = "met-no-locationforecast-v1"
MANUAL_PARSER_VERSION = "manual-official-evidence-v1"


@dataclass(frozen=True, slots=True)
class OpenFootballLeague:
    competition: str
    code: str
    timezone: str

    def url(self, season: str) -> str:
        return (
            "https://raw.githubusercontent.com/openfootball/football.json/"
            f"master/{season}/{self.code}.json"
        )


FIVE_LEAGUES: tuple[OpenFootballLeague, ...] = (
    OpenFootballLeague("Premier League", "en.1", "Europe/London"),
    OpenFootballLeague("La Liga", "es.1", "Europe/Madrid"),
    OpenFootballLeague("Serie A", "it.1", "Europe/Rome"),
    OpenFootballLeague("Bundesliga", "de.1", "Europe/Berlin"),
    OpenFootballLeague("Ligue 1", "fr.1", "Europe/Paris"),
)


class OpenFootballJsonAdapter:
    parser_version = OPENFOOTBALL_PARSER_VERSION

    def parse(
        self,
        raw: RawPayload,
        *,
        league: OpenFootballLeague,
        season: str,
        capture_window: CaptureWindow,
    ) -> tuple[Observation, ...]:
        document = json.loads(raw.content)
        matches = document.get("matches")
        if not isinstance(matches, list):
            raise ValueError("OpenFootball payload must contain a matches list")
        observations: list[Observation] = []
        for record in matches:
            if not isinstance(record, dict):
                raise ValueError("OpenFootball match must be an object")
            home = _required_text(record, "team1")
            away = _required_text(record, "team2")
            kickoff = _openfootball_kickoff(record, league.timezone)
            fixture_id = _fixture_id(league.competition, kickoff, home, away)
            payload: dict[str, object] = {
                "competition": league.competition,
                "season": season,
                "kickoff": kickoff.isoformat(),
                "home_team": home,
                "away_team": away,
                "round": record.get("round"),
            }
            observations.append(
                _observation(
                    raw,
                    fixture_id=fixture_id,
                    parser_version=self.parser_version,
                    data_type=DataType.FIXTURE,
                    capture_window=capture_window,
                    payload=payload,
                )
            )
            score = record.get("score")
            if isinstance(score, dict) and _valid_score(score.get("ft")):
                final_score = score["ft"]
                observations.append(
                    _observation(
                        raw,
                        fixture_id=fixture_id,
                        parser_version=self.parser_version,
                        data_type=DataType.POST_MATCH_STATISTICS,
                        capture_window=CaptureWindow.POST_MATCH,
                        payload={
                            **payload,
                            "home_goals": final_score[0],
                            "away_goals": final_score[1],
                        },
                    )
                )
        return tuple(observations)


class MetNoWeatherAdapter:
    parser_version = MET_NO_PARSER_VERSION

    def parse(
        self,
        raw: RawPayload,
        *,
        fixture_id: str,
        kickoff: datetime,
        capture_window: CaptureWindow,
    ) -> Observation:
        document = json.loads(raw.content)
        timeseries = document.get("properties", {}).get("timeseries")
        if not isinstance(timeseries, list) or not timeseries:
            raise ValueError("MET Norway payload must contain timeseries")
        published_at = _optional_iso(
            document.get("properties", {}).get("meta", {}).get("updated_at")
        )
        kickoff_utc = utc_datetime(kickoff, field="kickoff")
        nearest = min(timeseries, key=lambda item: abs(_parse_iso(item["time"]) - kickoff_utc))
        instant = nearest.get("data", {}).get("instant", {}).get("details", {})
        if not isinstance(instant, dict):
            raise ValueError("MET Norway timeseries item lacks instant details")
        return _observation(
            raw,
            fixture_id=fixture_id,
            parser_version=self.parser_version,
            data_type=DataType.WEATHER,
            capture_window=capture_window,
            published_at=published_at,
            payload={
                "forecast_time": _parse_iso(nearest["time"]).isoformat(),
                "air_temperature_c": instant.get("air_temperature"),
                "wind_speed_mps": instant.get("wind_speed"),
                "wind_from_direction_deg": instant.get("wind_from_direction"),
                "relative_humidity_pct": instant.get("relative_humidity"),
                "precipitation_next_1h_mm": _precipitation(nearest, "next_1_hours"),
            },
        )


class ManualObservationAdapter:
    parser_version = MANUAL_PARSER_VERSION
    _allowed = frozenset(
        {
            DataType.TEAM_NEWS,
            DataType.INJURY,
            DataType.SUSPENSION,
            DataType.LINEUP,
            DataType.LINEUP_CHANGE,
            DataType.ODDS,
        }
    )

    def create(
        self,
        *,
        fixture_id: str,
        source: str,
        source_url: str,
        published_at: datetime | None,
        captured_at: datetime,
        data_type: DataType,
        capture_window: CaptureWindow,
        payload: Mapping[str, object],
    ) -> Observation:
        """接收用户提供的官方证据；不代表系统自动抓取页面。"""

        if data_type not in self._allowed:
            raise ValueError("unsupported manual observation data type")
        return Observation(
            fixture_id=fixture_id,
            source=source,
            source_url=source_url,
            published_at=published_at,
            captured_at=captured_at,
            raw_payload_hash=hashlib.sha256(canonical_json(payload)).hexdigest(),
            parser_version=self.parser_version,
            data_type=data_type,
            source_policy=SourcePolicy.MANUAL_USER_SUPPLIED,
            capture_window=capture_window,
            payload=payload,
        )


def _observation(
    raw: RawPayload,
    *,
    fixture_id: str,
    parser_version: str,
    data_type: DataType,
    capture_window: CaptureWindow,
    payload: Mapping[str, object],
    published_at: datetime | None = None,
) -> Observation:
    return Observation(
        fixture_id=fixture_id,
        source=raw.source,
        source_url=raw.source_url,
        published_at=published_at,
        captured_at=raw.captured_at,
        raw_payload_hash=raw.sha256,
        parser_version=parser_version,
        data_type=data_type,
        source_policy=raw.source_policy,
        capture_window=capture_window,
        payload=payload,
    )


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"OpenFootball match lacks {key}")
    return value.strip()


def _openfootball_kickoff(record: Mapping[str, Any], timezone: str) -> datetime:
    date = _required_text(record, "date")
    time = record.get("time", "12:00")
    if not isinstance(time, str):
        raise ValueError("OpenFootball time must be a string")
    return (
        datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=ZoneInfo(timezone)).astimezone(UTC)
    )


def _fixture_id(competition: str, kickoff: datetime, home: str, away: str) -> str:
    identity = canonical_json(
        {"competition": competition, "kickoff": kickoff.isoformat(), "home": home, "away": away}
    )
    return f"openfootball:{hashlib.sha256(identity).hexdigest()[:24]}"


def _valid_score(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and item >= 0 for item in value)
    )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_iso(value: object) -> datetime | None:
    return _parse_iso(value) if isinstance(value, str) and value.strip() else None


def _precipitation(item: Mapping[str, Any], period: str) -> object:
    return item.get("data", {}).get(period, {}).get("details", {}).get("precipitation_amount")
