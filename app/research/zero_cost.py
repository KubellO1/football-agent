"""Deterministic, research-only zero-cost baseline experiment runner.

This module has no production dependency-injection registration, database access or
provider calls. It accepts an already-normalized immutable dataset and evaluates
simple Poisson-style baselines with chronological splits.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

OUTCOMES = ("home", "draw", "away")
FIVE_LEAGUES = (
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
)
VARIANT_FEATURES: dict[str, tuple[str, ...]] = {
    "A": ("goals", "recent_form"),
    "B": ("goals", "recent_form", "home_away"),
    "C": ("goals", "recent_form", "home_away", "xg"),
    "D": ("goals", "recent_form", "home_away", "xg", "shots"),
    "E": (
        "goals",
        "recent_form",
        "home_away",
        "xg",
        "shots",
        "possession",
        "goalkeeper_saves",
        "conversion",
    ),
    "F": (
        "goals",
        "recent_form",
        "home_away",
        "xg",
        "shots",
        "possession",
        "goalkeeper_saves",
        "conversion",
        "research_event_features",
    ),
}


@dataclass(frozen=True, slots=True)
class TeamMatchMetrics:
    """Observed team metrics for one completed fixture; missing values stay ``None``."""

    goals: int
    xg: float | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    possession: float | None = None
    goalkeeper_saves: int | None = None
    conversion: float | None = None
    ppda: float | None = None
    set_piece_shots: int | None = None
    header_shots: int | None = None
    big_chances: int | None = None

    def __post_init__(self) -> None:
        if self.goals < 0:
            raise ValueError("goals must be non-negative")
        for name in ("xg", "possession", "conversion", "ppda"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.possession is not None and self.possession > 100:
            raise ValueError("possession must be a percentage in [0, 100]")
        if self.conversion is not None and self.conversion > 1:
            raise ValueError("conversion must be a fraction in [0, 1]")
        for name in (
            "shots",
            "shots_on_target",
            "goalkeeper_saves",
            "set_piece_shots",
            "header_shots",
            "big_chances",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class HistoricalOddsObservation:
    """Contemporaneous odds retained for an optional research-only simulation."""

    home: float
    draw: float
    away: float
    observed_at: datetime
    prediction_at: datetime
    closing_line_research: bool = False

    def is_temporally_valid(self, kickoff: datetime) -> bool:
        return (
            self.prediction_at.tzinfo is not None
            and self.observed_at.tzinfo is not None
            and kickoff.tzinfo is not None
            and self.prediction_at < self.observed_at < kickoff
        )


@dataclass(frozen=True, slots=True)
class ResearchFixture:
    """Provider-neutral completed-fixture record used only by research experiments."""

    fixture_id: str
    source: str
    research_only: bool
    competition: str
    season: str
    kickoff: datetime
    home_team: str
    away_team: str
    home: TeamMatchMetrics
    away: TeamMatchMetrics
    odds: HistoricalOddsObservation | None = None

    def __post_init__(self) -> None:
        if self.kickoff.tzinfo is None:
            raise ValueError("kickoff must be timezone-aware")
        if not self.fixture_id or not self.source or not self.competition or not self.season:
            raise ValueError("fixture identity and provenance are required")
        if not self.home_team or not self.away_team or self.home_team == self.away_team:
            raise ValueError("home and away team identity must be distinct and non-empty")

    @property
    def natural_key(self) -> tuple[str, str, datetime, str, str]:
        return (
            self.competition.casefold(),
            self.season.casefold(),
            self.kickoff,
            self.home_team.casefold(),
            self.away_team.casefold(),
        )


@dataclass(frozen=True, slots=True)
class TeamHistoryRow:
    kickoff: datetime
    is_home: bool
    goals_for: int
    goals_against: int
    metrics_for: TeamMatchMetrics
    metrics_against: TeamMatchMetrics

    @property
    def points(self) -> int:
        if self.goals_for > self.goals_against:
            return 3
        if self.goals_for == self.goals_against:
            return 1
        return 0


@dataclass(frozen=True, slots=True)
class TemporalSample:
    fixture: ResearchFixture
    window: int
    home_history: tuple[TeamHistoryRow, ...]
    away_history: tuple[TeamHistoryRow, ...]


@dataclass(frozen=True, slots=True)
class Prediction:
    fixture_id: str
    competition: str
    kickoff: datetime
    variant: str
    window: int
    home: float
    draw: float
    away: float
    actual: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    status: str
    sample_size: int
    brier: float | None
    log_loss: float | None
    accuracy: float | None
    expected_calibration_error: float | None
    calibration_bins: tuple[dict[str, float | int], ...]


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    status: str
    fixture_count: int
    duplicate_count: int
    windows: dict[str, dict[str, Any]]
    ablation: dict[str, Any]
    betting_simulation: str
    outputs: dict[str, str | float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _team_row(fixture: ResearchFixture, *, home: bool) -> TeamHistoryRow:
    if home:
        return TeamHistoryRow(
            fixture.kickoff,
            True,
            fixture.home.goals,
            fixture.away.goals,
            fixture.home,
            fixture.away,
        )
    return TeamHistoryRow(
        fixture.kickoff,
        False,
        fixture.away.goals,
        fixture.home.goals,
        fixture.away,
        fixture.home,
    )


def deduplicate_fixtures(
    fixtures: Iterable[ResearchFixture],
) -> tuple[tuple[ResearchFixture, ...], int]:
    """Reject conflicting duplicates and collapse equivalent natural duplicates."""

    by_key: dict[tuple[str, str, datetime, str, str], ResearchFixture] = {}
    provider_ids: dict[tuple[str, str], ResearchFixture] = {}
    duplicate_count = 0
    for fixture in fixtures:
        provider_key = (fixture.source.casefold(), fixture.fixture_id)
        existing_provider = provider_ids.get(provider_key)
        if existing_provider is not None and existing_provider != fixture:
            raise ValueError(f"conflicting provider fixture id: {provider_key}")
        existing = by_key.get(fixture.natural_key)
        if existing is not None:
            if (
                existing.home.goals != fixture.home.goals
                or existing.away.goals != fixture.away.goals
            ):
                raise ValueError(f"conflicting natural fixture key: {fixture.natural_key}")
            duplicate_count += 1
            continue
        by_key[fixture.natural_key] = fixture
        provider_ids[provider_key] = fixture
    return tuple(sorted(by_key.values(), key=lambda item: item.kickoff)), duplicate_count


def build_temporal_samples(
    fixtures: Sequence[ResearchFixture], window: int
) -> tuple[TemporalSample, ...]:
    """Build samples using only same-season matches strictly before target kickoff."""

    if window < 1:
        raise ValueError("window must be positive")
    history: dict[tuple[str, str, str], list[TeamHistoryRow]] = {}
    samples: list[TemporalSample] = []
    for fixture in sorted(fixtures, key=lambda item: item.kickoff):
        base = (fixture.competition.casefold(), fixture.season.casefold())
        home_key = (*base, fixture.home_team.casefold())
        away_key = (*base, fixture.away_team.casefold())
        home_rows = history.get(home_key, [])
        away_rows = history.get(away_key, [])
        if len(home_rows) >= window and len(away_rows) >= window:
            selected_home = tuple(home_rows[-window:])
            selected_away = tuple(away_rows[-window:])
            if any(row.kickoff >= fixture.kickoff for row in (*selected_home, *selected_away)):
                raise AssertionError("temporal leakage detected")
            samples.append(TemporalSample(fixture, window, selected_home, selected_away))
        history.setdefault(home_key, []).append(_team_row(fixture, home=True))
        history.setdefault(away_key, []).append(_team_row(fixture, home=False))
    return tuple(samples)


def _complete(history: Sequence[TeamHistoryRow], names: Sequence[str]) -> bool:
    return all(getattr(row.metrics_for, name) is not None for row in history for name in names)


def variant_is_available(sample: TemporalSample, variant: str) -> bool:
    """Return whether every non-core feature required by a variant is observed."""

    histories = (sample.home_history, sample.away_history)
    if variant not in VARIANT_FEATURES:
        raise ValueError(f"unknown variant: {variant}")
    if variant in {"C", "D", "E", "F"} and not all(
        _complete(history, ("xg",)) for history in histories
    ):
        return False
    if variant in {"D", "E", "F"} and not all(
        _complete(history, ("shots", "shots_on_target")) for history in histories
    ):
        return False
    if variant in {"E", "F"} and not all(
        _complete(history, ("possession", "goalkeeper_saves", "conversion"))
        for history in histories
    ):
        return False
    return variant != "F" or all(
        _complete(history, ("ppda", "set_piece_shots", "header_shots")) for history in histories
    )


def _average(history: Sequence[TeamHistoryRow], field: str) -> float:
    values = [getattr(row.metrics_for, field) for row in history]
    if any(value is None for value in values):
        raise ValueError(f"missing required field: {field}")
    return _mean([float(value) for value in values if value is not None])


def _average_against(history: Sequence[TeamHistoryRow], field: str) -> float:
    values = [getattr(row.metrics_against, field) for row in history]
    if any(value is None for value in values):
        raise ValueError(f"missing required opponent field: {field}")
    return _mean([float(value) for value in values if value is not None])


def _goal_strength(history: Sequence[TeamHistoryRow]) -> tuple[float, float]:
    return (
        _mean([float(row.goals_for) for row in history]),
        _mean([float(row.goals_against) for row in history]),
    )


def _venue_strength(history: Sequence[TeamHistoryRow], *, home: bool) -> tuple[float, float]:
    venue = [row for row in history if row.is_home is home]
    return _goal_strength(venue if venue else history)


def _poisson_pmf(value: int, expected: float) -> float:
    return expected**value * math.exp(-expected) / math.factorial(value)


def _result_probabilities(home_lambda: float, away_lambda: float) -> tuple[float, float, float]:
    matrix = [
        [_poisson_pmf(home, home_lambda) * _poisson_pmf(away, away_lambda) for away in range(11)]
        for home in range(11)
    ]
    total = sum(sum(row) for row in matrix)
    home = sum(value for i, row in enumerate(matrix) for j, value in enumerate(row) if i > j)
    draw = sum(value for i, row in enumerate(matrix) for j, value in enumerate(row) if i == j)
    away = total - home - draw
    return home / total, draw / total, away / total


def predict_sample(sample: TemporalSample, variant: str) -> Prediction:
    """Run a fixed interpretable baseline; no test-data fitting is performed."""

    if not variant_is_available(sample, variant):
        raise ValueError(f"variant {variant} unavailable for {sample.fixture.fixture_id}")
    home_for, home_against = _goal_strength(sample.home_history)
    away_for, away_against = _goal_strength(sample.away_history)
    if variant != "A":
        home_for, home_against = _venue_strength(sample.home_history, home=True)
        away_for, away_against = _venue_strength(sample.away_history, home=False)
    home_lambda = (home_for + away_against) / 2
    away_lambda = (away_for + home_against) / 2
    home_form = _mean([row.points / 3 for row in sample.home_history])
    away_form = _mean([row.points / 3 for row in sample.away_history])
    home_lambda *= 1 + 0.08 * (home_form - away_form)
    away_lambda *= 1 + 0.08 * (away_form - home_form)
    if variant in {"C", "D", "E", "F"}:
        home_xg = _average(sample.home_history, "xg")
        away_xga = _average_against(sample.away_history, "xg")
        away_xg = _average(sample.away_history, "xg")
        home_xga = _average_against(sample.home_history, "xg")
        home_lambda = (home_lambda + (home_xg + away_xga) / 2) / 2
        away_lambda = (away_lambda + (away_xg + home_xga) / 2) / 2
    if variant in {"D", "E", "F"}:
        shots_edge = _average(sample.home_history, "shots") - _average(sample.away_history, "shots")
        target_edge = _average(sample.home_history, "shots_on_target") - _average(
            sample.away_history, "shots_on_target"
        )
        shot_adjustment = max(0.9, min(1.1, 1 + shots_edge / 200 + target_edge / 100))
        home_lambda *= shot_adjustment
        away_lambda *= 2 - shot_adjustment
    if variant in {"E", "F"}:
        possession_edge = _average(sample.home_history, "possession") - _average(
            sample.away_history, "possession"
        )
        saves_edge = _average(sample.away_history, "goalkeeper_saves") - _average(
            sample.home_history, "goalkeeper_saves"
        )
        conversion_edge = _average(sample.home_history, "conversion") - _average(
            sample.away_history, "conversion"
        )
        adjustment = max(
            0.9,
            min(
                1.1,
                1 + possession_edge / 500 + saves_edge / 100 + conversion_edge / 5,
            ),
        )
        home_lambda *= adjustment
        away_lambda *= 2 - adjustment
    if variant == "F":
        ppda_edge = _average(sample.away_history, "ppda") - _average(sample.home_history, "ppda")
        set_piece_edge = _average(sample.home_history, "set_piece_shots") - _average(
            sample.away_history, "set_piece_shots"
        )
        header_edge = _average(sample.home_history, "header_shots") - _average(
            sample.away_history, "header_shots"
        )
        adjustment = max(
            0.95,
            min(1.05, 1 + ppda_edge / 200 + set_piece_edge / 100 + header_edge / 100),
        )
        home_lambda *= adjustment
        away_lambda *= 2 - adjustment
    probabilities = _result_probabilities(max(home_lambda, 0.05), max(away_lambda, 0.05))
    actual = (
        "home"
        if sample.fixture.home.goals > sample.fixture.away.goals
        else "draw" if sample.fixture.home.goals == sample.fixture.away.goals else "away"
    )
    return Prediction(
        sample.fixture.fixture_id,
        sample.fixture.competition,
        sample.fixture.kickoff,
        variant,
        sample.window,
        *probabilities,
        actual,
    )


def evaluate(predictions: Sequence[Prediction]) -> Evaluation:
    if not predictions:
        return Evaluation("INSUFFICIENT_SAMPLE", 0, None, None, None, None, ())
    brier_values: list[float] = []
    losses: list[float] = []
    correct = 0
    bins: dict[int, list[tuple[float, int]]] = {index: [] for index in range(10)}
    for prediction in predictions:
        probabilities = (prediction.home, prediction.draw, prediction.away)
        actual_index = OUTCOMES.index(prediction.actual)
        brier_values.append(
            sum(
                (probability - (1.0 if index == actual_index else 0.0)) ** 2
                for index, probability in enumerate(probabilities)
            )
        )
        losses.append(-math.log(max(probabilities[actual_index], 1e-15)))
        selected = max(range(3), key=probabilities.__getitem__)
        correct += int(selected == actual_index)
        confidence = probabilities[selected]
        bins[min(int(confidence * 10), 9)].append((confidence, int(selected == actual_index)))
    calibration: list[dict[str, float | int]] = []
    ece = 0.0
    for index, rows in bins.items():
        if not rows:
            continue
        mean_confidence = _mean([row[0] for row in rows])
        observed = _mean([float(row[1]) for row in rows])
        ece += len(rows) / len(predictions) * abs(mean_confidence - observed)
        calibration.append(
            {
                "lower": index / 10,
                "upper": (index + 1) / 10,
                "count": len(rows),
                "mean_confidence": mean_confidence,
                "observed_accuracy": observed,
            }
        )
    return Evaluation(
        "PASS",
        len(predictions),
        _mean(brier_values),
        _mean(losses),
        correct / len(predictions),
        ece,
        tuple(calibration),
    )


def chronological_split(
    samples: Sequence[TemporalSample],
) -> tuple[tuple[TemporalSample, ...], ...]:
    ordered = tuple(sorted(samples, key=lambda sample: sample.fixture.kickoff))
    train_end = int(len(ordered) * 0.6)
    validation_end = int(len(ordered) * 0.8)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


class ZeroCostExperimentRunner:
    """Evaluate fixed zero-cost variants on chronological out-of-sample fixtures."""

    def run(
        self,
        fixtures: Iterable[ResearchFixture],
        *,
        windows: Sequence[int] = (5, 10, 20),
    ) -> ExperimentReport:
        unique, duplicate_count = deduplicate_fixtures(fixtures)
        window_reports: dict[str, dict[str, Any]] = {}
        best: tuple[str, int, Evaluation] | None = None
        paired_scores: dict[tuple[int, str], Evaluation] = {}
        for window in windows:
            samples = build_temporal_samples(unique, window)
            train, validation, test = chronological_split(samples)
            variants: dict[str, Any] = {}
            for variant in VARIANT_FEATURES:
                eligible_test = tuple(
                    sample for sample in test if variant_is_available(sample, variant)
                )
                predictions = tuple(predict_sample(sample, variant) for sample in eligible_test)
                result = evaluate(predictions)
                league_results = {
                    league: asdict(
                        evaluate(tuple(item for item in predictions if item.competition == league))
                    )
                    for league in FIVE_LEAGUES
                }
                variants[variant] = {
                    "features": VARIANT_FEATURES[variant],
                    "eligible_test_samples": len(eligible_test),
                    "evaluation": asdict(result),
                    "by_league": league_results,
                }
                paired_scores[(window, variant)] = result
                if result.brier is not None and (
                    best is None or result.brier < (best[2].brier or math.inf)
                ):
                    best = (variant, window, result)
            window_reports[str(window)] = {
                "temporal_samples": len(samples),
                "train": len(train),
                "validation": len(validation),
                "test": len(test),
                "variants": variants,
            }
        ablation: dict[str, Any] = {}
        variants_order = tuple(VARIANT_FEATURES)
        for left, right in zip(variants_order, variants_order[1:], strict=False):
            comparisons = []
            for window in windows:
                previous = paired_scores[(window, left)]
                current = paired_scores[(window, right)]
                paired = previous.sample_size == current.sample_size and current.sample_size > 0
                comparisons.append(
                    {
                        "window": window,
                        "status": "PAIRED" if paired else "INSUFFICIENT_PAIRED_SAMPLE",
                        "sample_size": current.sample_size if paired else 0,
                        "brier_delta": (
                            None
                            if previous.brier is None or current.brier is None
                            else current.brier - previous.brier
                        ),
                    }
                )
            ablation[f"{left}->{right}"] = comparisons
        valid_odds = sum(
            1
            for fixture in unique
            if fixture.odds and fixture.odds.is_temporally_valid(fixture.kickoff)
        )
        betting_status = (
            "INSUFFICIENT_ODDS_DATA" if valid_odds == 0 else "NOT_RUN_REQUIRES_GATE_ADAPTER"
        )
        outputs: dict[str, str | float] = {
            "ZERO_COST_MODEL_FEASIBLE": "yes" if best is not None else "no",
            "BEST_ZERO_COST_MODEL": (
                "UNAVAILABLE" if best is None else f"{best[0]}_WINDOW_{best[1]}"
            ),
            "BEST_BRIER": "UNAVAILABLE" if best is None else float(best[2].brier or 0),
            "BEST_LOG_LOSS": "UNAVAILABLE" if best is None else float(best[2].log_loss or 0),
            "CALIBRATION_ACCEPTABLE": "no" if best is None else "unproven",
            "FIVE_LEAGUE_GENERALIZATION": "insufficient_data",
            "PAID_ADVANCED_METRICS_NEEDED_FOR_MODEL_QUALITY": "unproven",
            "LIVE_ODDS_REQUIRED_FOR_FORMAL_BET": "yes",
            "MARKET_REFERENCE": "SEPARATE_UNAVAILABLE",
            "FROZEN_BETTING_POLICY": (
                "min_ev=0.05; confidence>=0.70; Recommendation Gate required; "
                "quarter Kelly; stake cap<=0.03"
            ),
            "RECOMMENDED_NEXT_STEP": (
                "Normalize a materially larger rights-cleared zero-cost historical dataset, "
                "then rerun the unchanged experiment."
            ),
        }
        return ExperimentReport(
            "PASS" if best is not None else "INSUFFICIENT_SAMPLE",
            len(unique),
            duplicate_count,
            window_reports,
            ablation,
            betting_status,
            outputs,
        )


def _metrics(payload: Mapping[str, Any]) -> TeamMatchMetrics:
    return TeamMatchMetrics(
        goals=int(payload["goals"]),
        xg=payload.get("xg"),
        shots=payload.get("shots"),
        shots_on_target=payload.get("shots_on_target"),
        possession=payload.get("possession"),
        goalkeeper_saves=payload.get("goalkeeper_saves"),
        conversion=payload.get("conversion"),
        ppda=payload.get("ppda"),
        set_piece_shots=payload.get("set_piece_shots"),
        header_shots=payload.get("header_shots"),
        big_chances=payload.get("big_chances"),
    )


def load_research_fixtures(path: Path) -> tuple[ResearchFixture, ...]:
    """Load the versioned normalized JSON contract used by the CLI runner."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported normalized research schema")
    return tuple(
        ResearchFixture(
            fixture_id=str(item["fixture_id"]),
            source=str(item["source"]),
            research_only=bool(item["research_only"]),
            competition=str(item["competition"]),
            season=str(item["season"]),
            kickoff=datetime.fromisoformat(item["kickoff"]),
            home_team=str(item["home_team"]),
            away_team=str(item["away_team"]),
            home=_metrics(item["home"]),
            away=_metrics(item["away"]),
        )
        for item in payload["fixtures"]
    )


def validate_dataset_manifest(path: Path) -> dict[str, Any]:
    """Validate provenance and the research-only/production-use safety boundary."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported dataset manifest schema")
    datasets = payload.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("dataset manifest must contain at least one dataset")
    required = {
        "source",
        "license",
        "research_allowed",
        "production_allowed",
        "competitions",
        "seasons",
        "fixture_count",
        "event_count",
        "teams",
        "date_range",
        "available_metrics",
        "missing_metrics",
        "fixture_identity_strategy",
        "source_location",
        "checksum",
        "parser_version",
    }
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise ValueError("dataset entry must be an object")
        missing = sorted(required.difference(dataset))
        if missing:
            raise ValueError(f"dataset entry is missing fields: {missing}")
        if dataset.get("research_only") is True and dataset["production_allowed"] is not False:
            raise ValueError("research-only dataset cannot be production-compatible")
    return cast("dict[str, Any]", payload)
