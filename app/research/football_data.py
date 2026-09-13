"""Football-Data.co.uk research-only longitudinal CSV adapter.

The adapter consumes the site's public CSV downloads. It deliberately keeps
market odds outside :class:`ResearchFixture` because the source does not expose
row-level capture timestamps. Closing odds are therefore evaluation targets,
never pre-match model features.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.research.zero_cost import ResearchFixture, TeamMatchMetrics, deduplicate_fixtures

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

PARSER_VERSION = "football-data-csv-v1"
BASE_URL = "https://www.football-data.co.uk/mmz4281"


@dataclass(frozen=True, slots=True)
class CompetitionSpec:
    name: str
    division: str
    timezone: str


FIVE_LEAGUE_SPECS: tuple[CompetitionSpec, ...] = (
    CompetitionSpec("Premier League", "E0", "Europe/London"),
    CompetitionSpec("La Liga", "SP1", "Europe/Madrid"),
    CompetitionSpec("Serie A", "I1", "Europe/Rome"),
    CompetitionSpec("Bundesliga", "D1", "Europe/Berlin"),
    CompetitionSpec("Ligue 1", "F1", "Europe/Paris"),
)
SPECS_BY_DIVISION = {spec.division: spec for spec in FIVE_LEAGUE_SPECS}


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    url: str
    cache_path: str
    sha256: str
    byte_count: int
    fetched_at: str
    cache_hit: bool
    parser_version: str = PARSER_VERSION


@dataclass(frozen=True, slots=True)
class HistoricalOddsQuote:
    fixture_id: str
    source: str
    bookmaker: str
    context: str
    home: float
    draw: float
    away: float
    raw_columns: tuple[str, str, str]
    captured_at: None = None
    model_feature_allowed: bool = False
    target_evaluation_allowed: bool = True


@dataclass(frozen=True, slots=True)
class MatchSupplementalStats:
    fixture_id: str
    half_time_home_goals: int | None
    half_time_away_goals: int | None
    home_corners: int | None
    away_corners: int | None
    home_fouls: int | None
    away_fouls: int | None
    home_yellow_cards: int | None
    away_yellow_cards: int | None
    home_red_cards: int | None
    away_red_cards: int | None


@dataclass(frozen=True, slots=True)
class ParsedSeason:
    fixtures: tuple[ResearchFixture, ...]
    odds: tuple[HistoricalOddsQuote, ...]
    supplemental: tuple[MatchSupplementalStats, ...]
    rejected_rows: int


@dataclass(frozen=True, slots=True)
class LongitudinalDataset:
    fixtures: tuple[ResearchFixture, ...]
    odds: tuple[HistoricalOddsQuote, ...]
    supplemental: tuple[MatchSupplementalStats, ...]
    artifacts: tuple[SourceArtifact, ...]
    duplicate_count: int
    rejected_rows: int

    def summary(self) -> dict[str, object]:
        """Return auditable coverage without mutating or enriching source values."""

        observations = len(self.fixtures) * 2
        metric_fields = (
            "xg",
            "shots",
            "shots_on_target",
            "possession",
            "goalkeeper_saves",
            "conversion",
        )
        metric_counts = {
            field: sum(
                getattr(metrics, field) is not None
                for fixture in self.fixtures
                for metrics in (fixture.home, fixture.away)
            )
            for field in metric_fields
        }
        league_seasons: dict[str, dict[str, int]] = {}
        team_appearances: dict[tuple[str, str, str], int] = {}
        for fixture in self.fixtures:
            seasons = league_seasons.setdefault(fixture.competition, {})
            seasons[fixture.season] = seasons.get(fixture.season, 0) + 1
            for team in (fixture.home_team, fixture.away_team):
                key = (fixture.competition, fixture.season, team)
                team_appearances[key] = team_appearances.get(key, 0) + 1
        rolling_history = {
            str(window): sum(count >= window for count in team_appearances.values())
            for window in (5, 10, 20)
        }
        odds_contexts: dict[str, int] = {}
        odds_bookmakers: dict[str, int] = {}
        for quote in self.odds:
            odds_contexts[quote.context] = odds_contexts.get(quote.context, 0) + 1
            odds_bookmakers[quote.bookmaker] = odds_bookmakers.get(quote.bookmaker, 0) + 1
        return {
            "fixture_count": len(self.fixtures),
            "team_season_count": len(team_appearances),
            "league_season_fixture_counts": league_seasons,
            "rolling_history_ready_team_seasons": rolling_history,
            "metric_observation_denominator": observations,
            "metric_observation_counts": metric_counts,
            "metric_observation_rates": {
                field: (count / observations if observations else 0.0)
                for field, count in metric_counts.items()
            },
            "odds_quote_count": len(self.odds),
            "odds_context_counts": odds_contexts,
            "odds_bookmaker_counts": odds_bookmakers,
            "supplemental_row_count": len(self.supplemental),
            "duplicate_count": self.duplicate_count,
            "rejected_rows": self.rejected_rows,
            "download_count": sum(not artifact.cache_hit for artifact in self.artifacts),
            "cache_hit_count": sum(artifact.cache_hit for artifact in self.artifacts),
        }

    def write_normalized(self, path: Path) -> None:
        """Write the existing TASK-054 normalized fixture contract."""

        payload = {
            "schema_version": "1.0",
            "source": "football-data.co.uk",
            "research_only": True,
            "parser_version": PARSER_VERSION,
            "fixtures": [_fixture_payload(fixture) for fixture in self.fixtures],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def write_provenance(self, path: Path) -> None:
        payload = {
            "source": "Football-Data.co.uk public CSV downloads",
            "research_only": True,
            "production_allowed": "UNCONFIRMED",
            "parser_version": PARSER_VERSION,
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
            "fixture_count": len(self.fixtures),
            "odds_quote_count": len(self.odds),
            "duplicate_count": self.duplicate_count,
            "rejected_rows": self.rejected_rows,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class FootballDataCsvAdapter:
    """Download, cache, checksum and normalize public Football-Data CSV files."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        fetcher: Callable[[str], bytes] | None = None,
        minimum_request_interval_seconds: float = 1.0,
    ) -> None:
        if minimum_request_interval_seconds < 0:
            raise ValueError("minimum request interval must be non-negative")
        self._cache_dir = cache_dir
        self._fetcher = fetcher or _download
        self._minimum_interval = minimum_request_interval_seconds
        self._last_request_at: float | None = None

    def acquire(self, season_codes: Sequence[str]) -> LongitudinalDataset:
        fixtures: list[ResearchFixture] = []
        odds: list[HistoricalOddsQuote] = []
        supplemental: list[MatchSupplementalStats] = []
        artifacts: list[SourceArtifact] = []
        rejected_rows = 0
        for season_code in season_codes:
            season = _season_label(season_code)
            for spec in FIVE_LEAGUE_SPECS:
                content, artifact = self._get(season_code, spec.division)
                parsed = parse_csv(content, spec=spec, season=season)
                fixtures.extend(parsed.fixtures)
                odds.extend(parsed.odds)
                supplemental.extend(parsed.supplemental)
                rejected_rows += parsed.rejected_rows
                artifacts.append(artifact)
        unique, duplicate_count = deduplicate_fixtures(fixtures)
        fixture_ids = {fixture.fixture_id for fixture in unique}
        unique_odds = _deduplicate_odds(quote for quote in odds if quote.fixture_id in fixture_ids)
        unique_supplemental = _deduplicate_supplemental(
            item for item in supplemental if item.fixture_id in fixture_ids
        )
        return LongitudinalDataset(
            unique,
            unique_odds,
            unique_supplemental,
            tuple(artifacts),
            duplicate_count,
            rejected_rows,
        )

    def _get(self, season_code: str, division: str) -> tuple[bytes, SourceArtifact]:
        if division not in SPECS_BY_DIVISION:
            raise ValueError(f"unsupported division: {division}")
        if not re.fullmatch(r"\d{4}", season_code):
            raise ValueError("season code must use Football-Data YYZZ format")
        url = f"{BASE_URL}/{season_code}/{division}.csv"
        path = self._cache_dir / season_code / f"{division}.csv"
        cache_hit = path.exists()
        if cache_hit:
            content = path.read_bytes()
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        else:
            self._throttle()
            content = self._fetcher(url)
            if not content:
                raise ValueError(f"empty CSV response: {url}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            fetched_at = datetime.now(UTC).isoformat()
            self._last_request_at = time.monotonic()
        artifact = SourceArtifact(
            url=url,
            cache_path=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
            fetched_at=fetched_at,
            cache_hit=cache_hit,
        )
        return content, artifact

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        wait = self._minimum_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)


def parse_csv(content: bytes, *, spec: CompetitionSpec, season: str) -> ParsedSeason:
    """Parse completed rows; incomplete/future rows are explicitly rejected."""

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fixtures: list[ResearchFixture] = []
    odds: list[HistoricalOddsQuote] = []
    supplemental: list[MatchSupplementalStats] = []
    rejected_rows = 0
    for row in reader:
        if not _is_completed(row):
            rejected_rows += 1
            continue
        try:
            kickoff = _parse_kickoff(row, spec.timezone)
            home_team = _team_name(row.get("HomeTeam"))
            away_team = _team_name(row.get("AwayTeam"))
            fixture_id = _fixture_id(spec.division, season, kickoff, home_team, away_team)
            home_goals = _required_int(row, "FTHG")
            away_goals = _required_int(row, "FTAG")
            home_shots = _optional_int(row.get("HS"))
            away_shots = _optional_int(row.get("AS"))
            fixture = ResearchFixture(
                fixture_id=fixture_id,
                source="football-data.co.uk",
                research_only=True,
                competition=spec.name,
                season=season,
                kickoff=kickoff,
                home_team=home_team,
                away_team=away_team,
                home=TeamMatchMetrics(
                    goals=home_goals,
                    xg=None,
                    shots=home_shots,
                    shots_on_target=_optional_int(row.get("HST")),
                    conversion=_conversion(home_goals, home_shots),
                ),
                away=TeamMatchMetrics(
                    goals=away_goals,
                    xg=None,
                    shots=away_shots,
                    shots_on_target=_optional_int(row.get("AST")),
                    conversion=_conversion(away_goals, away_shots),
                ),
            )
        except (KeyError, TypeError, ValueError):
            rejected_rows += 1
            continue
        fixtures.append(fixture)
        odds.extend(_odds_quotes(fixture_id, row))
        supplemental.append(
            MatchSupplementalStats(
                fixture_id=fixture_id,
                half_time_home_goals=_optional_int(row.get("HTHG")),
                half_time_away_goals=_optional_int(row.get("HTAG")),
                home_corners=_optional_int(row.get("HC")),
                away_corners=_optional_int(row.get("AC")),
                home_fouls=_optional_int(row.get("HF")),
                away_fouls=_optional_int(row.get("AF")),
                home_yellow_cards=_optional_int(row.get("HY")),
                away_yellow_cards=_optional_int(row.get("AY")),
                home_red_cards=_optional_int(row.get("HR")),
                away_red_cards=_optional_int(row.get("AR")),
            )
        )
    return ParsedSeason(tuple(fixtures), tuple(odds), tuple(supplemental), rejected_rows)


def canonical_team_key(competition: str, team_name: str) -> str:
    """Return a provider-neutral deterministic key without guessing aliases."""

    normalized = unicodedata.normalize("NFKD", team_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    competition_slug = re.sub(r"[^a-z0-9]+", "-", competition.casefold()).strip("-")
    return f"{competition_slug}:{slug}"


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "FootballAgentResearch/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status != 200:
            raise OSError(f"Football-Data request failed with HTTP {response.status}")
        return bytes(response.read())


def _season_label(code: str) -> str:
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError("invalid season code")
    start = 2000 + int(code[:2])
    end = 2000 + int(code[2:])
    if end != start + 1:
        raise ValueError("season code must represent consecutive years")
    return f"{start}/{end}"


def _is_completed(row: Mapping[str, str]) -> bool:
    return bool(row.get("Date") and row.get("HomeTeam") and row.get("AwayTeam")) and row.get(
        "FTR"
    ) in {"H", "D", "A"}


def _parse_kickoff(row: Mapping[str, str], timezone_name: str) -> datetime:
    date_value = (row.get("Date") or "").strip()
    time_value = (row.get("Time") or "12:00").strip() or "12:00"
    parsed_date = None
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            parsed_date = datetime.strptime(date_value, pattern).date()
            break
        except ValueError:
            continue
    if parsed_date is None:
        raise ValueError(f"invalid match date: {date_value}")
    parsed_time = datetime.strptime(time_value, "%H:%M").time()
    local = datetime.combine(parsed_date, parsed_time, tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(UTC)


def _team_name(value: str | None) -> str:
    name = " ".join((value or "").split())
    if not name:
        raise ValueError("team name is required")
    return name


def _fixture_id(
    division: str, season: str, kickoff: datetime, home_team: str, away_team: str
) -> str:
    identity = "|".join(
        (
            division,
            season,
            kickoff.isoformat(),
            canonical_team_key(division, home_team),
            canonical_team_key(division, away_team),
        )
    )
    return f"football-data:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _required_int(row: Mapping[str, str], key: str) -> int:
    value = _optional_int(row.get(key))
    if value is None:
        raise ValueError(f"missing required integer: {key}")
    return value


def _optional_int(value: str | None) -> int | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    number = float(cleaned)
    if not number.is_integer() or number < 0:
        raise ValueError(f"invalid non-negative integer: {value}")
    return int(number)


def _optional_float(value: str | None) -> float | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    number = float(cleaned)
    return number if number > 1 else None


def _conversion(goals: int, shots: int | None) -> float | None:
    return None if shots in {None, 0} else goals / shots


def _quote(
    fixture_id: str,
    row: Mapping[str, str],
    *,
    bookmaker: str,
    context: str,
    columns: tuple[str, str, str],
) -> HistoricalOddsQuote | None:
    values = tuple(_optional_float(row.get(column)) for column in columns)
    if any(value is None for value in values):
        return None
    home, draw, away = values
    assert home is not None and draw is not None and away is not None
    return HistoricalOddsQuote(
        fixture_id,
        "football-data.co.uk",
        bookmaker,
        context,
        home,
        draw,
        away,
        columns,
    )


def _odds_quotes(fixture_id: str, row: Mapping[str, str]) -> tuple[HistoricalOddsQuote, ...]:
    candidates = (
        ("market-average", "PRE_CLOSING", ("AvgH", "AvgD", "AvgA")),
        ("market-average", "CLOSING", ("AvgCH", "AvgCD", "AvgCA")),
        ("Bet365", "PRE_CLOSING", ("B365H", "B365D", "B365A")),
        ("Bet365", "CLOSING", ("B365CH", "B365CD", "B365CA")),
        ("Bwin", "PRE_CLOSING", ("BWH", "BWD", "BWA")),
        ("Bwin", "CLOSING", ("BWCH", "BWCD", "BWCA")),
        ("Pinnacle", "PRE_CLOSING", ("PSH", "PSD", "PSA")),
        ("Pinnacle", "CLOSING", ("PSCH", "PSCD", "PSCA")),
    )
    return tuple(
        quote
        for bookmaker, context, columns in candidates
        if (
            quote := _quote(
                fixture_id,
                row,
                bookmaker=bookmaker,
                context=context,
                columns=columns,
            )
        )
        is not None
    )


def _deduplicate_odds(quotes: Iterable[HistoricalOddsQuote]) -> tuple[HistoricalOddsQuote, ...]:
    values: dict[tuple[str, str, str], HistoricalOddsQuote] = {}
    for quote in quotes:
        key = (quote.fixture_id, quote.bookmaker.casefold(), quote.context)
        existing = values.get(key)
        if existing is not None and existing != quote:
            raise ValueError(f"conflicting historical odds key: {key}")
        values[key] = quote
    return tuple(values.values())


def _deduplicate_supplemental(
    values: Iterable[MatchSupplementalStats],
) -> tuple[MatchSupplementalStats, ...]:
    by_fixture: dict[str, MatchSupplementalStats] = {}
    for value in values:
        existing = by_fixture.get(value.fixture_id)
        if existing is not None and existing != value:
            raise ValueError(f"conflicting supplemental stats: {value.fixture_id}")
        by_fixture[value.fixture_id] = value
    return tuple(by_fixture.values())


def _metrics_payload(metrics: TeamMatchMetrics) -> dict[str, int | float | None]:
    return asdict(metrics)


def _fixture_payload(fixture: ResearchFixture) -> dict[str, object]:
    return {
        "fixture_id": fixture.fixture_id,
        "source": fixture.source,
        "research_only": fixture.research_only,
        "competition": fixture.competition,
        "season": fixture.season,
        "kickoff": fixture.kickoff.isoformat(),
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "home": _metrics_payload(fixture.home),
        "away": _metrics_payload(fixture.away),
    }
