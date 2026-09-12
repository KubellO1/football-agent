"""Leakage-safe, zero-cost model comparison against the bookmaker market.

This module is research-only.  It has no production DI registration, database
access, provider calls, recommendation gate, Kelly sizing, or side effects.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.research.longitudinal import _no_xg_predictions
from app.research.zero_cost import (
    OUTCOMES,
    Prediction,
    ResearchFixture,
    TeamHistoryRow,
    TemporalSample,
    build_temporal_samples,
    evaluate,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from app.research.football_data import HistoricalOddsQuote

FEATURE_NAMES = (
    "goals_for_edge",
    "goals_against_edge",
    "venue_goals_edge",
    "venue_points_edge",
    "shots_edge",
    "shots_on_target_edge",
    "conversion_edge",
    "defensive_shot_suppression_edge",
    "points_per_match_edge",
    "goal_difference_edge",
    "rest_days_edge",
    "opponent_strength_adjusted_form_edge",
    "ewma_form_edge",
    "league_goal_normalized_edge",
    "season_phase",
    "home_promoted",
    "away_promoted",
)


@dataclass(frozen=True, slots=True)
class ModelRow:
    fixture: ResearchFixture
    features: tuple[float, ...]
    market_probabilities: tuple[float, float, float]
    offered_odds: tuple[float, float, float]
    closing_odds: tuple[float, float, float] | None
    actual_index: int


@dataclass(frozen=True, slots=True)
class Scaler:
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def transform(self, values: Sequence[float]) -> tuple[float, ...]:
        return tuple(
            (value - mean) / scale
            for value, mean, scale in zip(values, self.means, self.scales, strict=True)
        )


@dataclass(frozen=True, slots=True)
class SoftmaxModel:
    scaler: Scaler
    weights: tuple[tuple[float, ...], ...]
    market_offset: bool
    market_features: bool

    def predict(self, row: ModelRow) -> tuple[float, float, float]:
        raw_features = _model_features(row, include_market=self.market_features)
        values = (1.0, *self.scaler.transform(raw_features))
        offsets = (
            tuple(math.log(max(value, 1e-12)) for value in row.market_probabilities)
            if self.market_offset
            else (0.0, 0.0, 0.0)
        )
        logits = tuple(
            offsets[index]
            + sum(weight * value for weight, value in zip(class_weights, values, strict=True))
            for index, class_weights in enumerate(self.weights)
        )
        return _softmax(logits)


@dataclass(frozen=True, slots=True)
class Stump:
    feature: int
    threshold: float
    left: float
    right: float

    def predict(self, values: Sequence[float]) -> float:
        return self.left if values[self.feature] <= self.threshold else self.right


@dataclass(frozen=True, slots=True)
class BoostedResidualModel:
    scaler: Scaler
    stumps: tuple[tuple[Stump, ...], ...]
    learning_rate: float

    def predict(self, row: ModelRow) -> tuple[float, float, float]:
        values = self.scaler.transform(row.features)
        logits = [math.log(max(value, 1e-12)) for value in row.market_probabilities]
        for class_index, class_stumps in enumerate(self.stumps):
            logits[class_index] += self.learning_rate * sum(
                stump.predict(values) for stump in class_stumps
            )
        return _softmax(logits)


@dataclass(frozen=True, slots=True)
class BinaryPlatt:
    slope: float
    intercept: float

    def predict(self, probability: float) -> float:
        logit = math.log(max(probability, 1e-12) / max(1 - probability, 1e-12))
        return _sigmoid(self.slope * logit + self.intercept)


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    classes: tuple[BinaryPlatt, BinaryPlatt, BinaryPlatt]

    def apply(self, probabilities: Sequence[float]) -> tuple[float, float, float]:
        values = tuple(
            model.predict(probability)
            for model, probability in zip(self.classes, probabilities, strict=True)
        )
        return _normalize(values)


@dataclass(frozen=True, slots=True)
class IsotonicCurve:
    upper_bounds: tuple[float, ...]
    values: tuple[float, ...]

    def predict(self, probability: float) -> float:
        for upper, value in zip(self.upper_bounds, self.values, strict=True):
            if probability <= upper:
                return value
        return self.values[-1]


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    classes: tuple[IsotonicCurve, IsotonicCurve, IsotonicCurve]

    def apply(self, probabilities: Sequence[float]) -> tuple[float, float, float]:
        values = tuple(
            curve.predict(probability)
            for curve, probability in zip(self.classes, probabilities, strict=True)
        )
        return _normalize(values)


def build_model_rows(
    fixtures: Sequence[ResearchFixture],
    odds: Iterable[HistoricalOddsQuote],
    *,
    window: int = 20,
) -> tuple[ModelRow, ...]:
    """Build market-aligned feature rows using information available before kickoff."""

    opening = _preferred_quotes(odds, "PRE_CLOSING")
    closing = _preferred_quotes(odds, "CLOSING")
    promoted = _promoted_teams(fixtures)
    league_goal_means = _league_goal_means(fixtures)
    opponent_strength_edges = _pre_match_elo_edges(fixtures)
    rows: list[ModelRow] = []
    for sample in build_temporal_samples(fixtures, window):
        quote = opening.get(sample.fixture.fixture_id)
        if quote is None or not _feature_history_complete(sample):
            continue
        probabilities = remove_overround((quote.home, quote.draw, quote.away))
        closing_quote = closing.get(sample.fixture.fixture_id)
        rows.append(
            ModelRow(
                fixture=sample.fixture,
                features=_features(
                    sample,
                    league_goal_means[sample.fixture.fixture_id],
                    opponent_strength_edges[sample.fixture.fixture_id],
                    promoted,
                ),
                market_probabilities=probabilities,
                offered_odds=(quote.home, quote.draw, quote.away),
                closing_odds=(
                    (closing_quote.home, closing_quote.draw, closing_quote.away)
                    if closing_quote is not None
                    else None
                ),
                actual_index=_actual_index(sample.fixture),
            )
        )
    return tuple(rows)


def remove_overround(odds: Sequence[float]) -> tuple[float, float, float]:
    if len(odds) != 3 or any(not math.isfinite(value) or value <= 1 for value in odds):
        raise ValueError("three finite decimal prices above 1 are required")
    implied = tuple(1 / value for value in odds)
    return _normalize(implied)


def fit_softmax(
    rows: Sequence[ModelRow],
    *,
    market_offset: bool,
    market_features: bool = False,
    epochs: int = 140,
    learning_rate: float = 0.12,
    l2: float = 0.002,
) -> SoftmaxModel:
    if not rows:
        raise ValueError("training rows are required")
    matrix = tuple(_model_features(row, include_market=market_features) for row in rows)
    scaler = _fit_scaler(matrix)
    feature_count = len(matrix[0]) + 1
    weights = [[0.0] * feature_count for _ in range(3)]
    for epoch in range(epochs):
        gradient = [[0.0] * feature_count for _ in range(3)]
        for row in rows:
            raw_features = _model_features(row, include_market=market_features)
            values = (1.0, *scaler.transform(raw_features))
            offsets = (
                tuple(math.log(max(value, 1e-12)) for value in row.market_probabilities)
                if market_offset
                else (0.0, 0.0, 0.0)
            )
            probabilities = _softmax(
                tuple(
                    offsets[class_index]
                    + sum(
                        weight * value
                        for weight, value in zip(weights[class_index], values, strict=True)
                    )
                    for class_index in range(3)
                )
            )
            for class_index in range(3):
                error = probabilities[class_index] - int(row.actual_index == class_index)
                for feature_index, value in enumerate(values):
                    gradient[class_index][feature_index] += error * value
        step = learning_rate / math.sqrt(epoch + 1)
        for class_index in range(3):
            for feature_index in range(feature_count):
                penalty = 0.0 if feature_index == 0 else l2 * weights[class_index][feature_index]
                weights[class_index][feature_index] -= step * (
                    gradient[class_index][feature_index] / len(rows) + penalty
                )
    return SoftmaxModel(
        scaler,
        tuple(tuple(values) for values in weights),
        market_offset,
        market_features,
    )


def fit_boosted_residual(
    rows: Sequence[ModelRow], *, rounds: int = 18, learning_rate: float = 0.08
) -> BoostedResidualModel:
    if not rows:
        raise ValueError("training rows are required")
    scaler = _fit_scaler(tuple(row.features for row in rows))
    matrix = tuple(scaler.transform(row.features) for row in rows)
    logits = [[math.log(max(value, 1e-12)) for value in row.market_probabilities] for row in rows]
    stumps: list[list[Stump]] = [[], [], []]
    thresholds = _candidate_thresholds(matrix)
    for _ in range(rounds):
        probabilities = tuple(_softmax(tuple(value)) for value in logits)
        for class_index in range(3):
            residuals = tuple(
                int(row.actual_index == class_index) - probabilities[index][class_index]
                for index, row in enumerate(rows)
            )
            stump = _best_stump(matrix, residuals, thresholds)
            stumps[class_index].append(stump)
            for index, values in enumerate(matrix):
                logits[index][class_index] += learning_rate * stump.predict(values)
    return BoostedResidualModel(
        scaler,
        tuple(tuple(class_stumps) for class_stumps in stumps),
        learning_rate,
    )


def fit_platt(
    probabilities: Sequence[tuple[float, float, float]], actual: Sequence[int]
) -> PlattCalibrator:
    models: list[BinaryPlatt] = []
    for class_index in range(3):
        slope, intercept = 1.0, 0.0
        for epoch in range(300):
            slope_gradient = 0.0
            intercept_gradient = 0.0
            for values, target in zip(probabilities, actual, strict=True):
                probability = values[class_index]
                logit = math.log(max(probability, 1e-12) / max(1 - probability, 1e-12))
                error = _sigmoid(slope * logit + intercept) - int(target == class_index)
                slope_gradient += error * logit
                intercept_gradient += error
            step = 0.08 / math.sqrt(epoch + 1)
            slope -= step * slope_gradient / len(actual)
            intercept -= step * intercept_gradient / len(actual)
        models.append(BinaryPlatt(slope, intercept))
    return PlattCalibrator((models[0], models[1], models[2]))


def fit_isotonic(
    probabilities: Sequence[tuple[float, float, float]], actual: Sequence[int]
) -> IsotonicCalibrator:
    curves = tuple(
        _fit_isotonic_curve(
            tuple(
                (values[class_index], int(target == class_index))
                for values, target in zip(probabilities, actual, strict=True)
            )
        )
        for class_index in range(3)
    )
    return IsotonicCalibrator((curves[0], curves[1], curves[2]))


def run_market_improvement_experiment(
    fixtures: Sequence[ResearchFixture],
    odds: Iterable[HistoricalOddsQuote],
    *,
    selection_season: str = "2024/2025",
    holdout_season: str = "2025/2026",
) -> dict[str, Any]:
    """Run fixed walk-forward model selection and untouched final holdout evaluation."""

    odds_tuple = tuple(odds)
    rows = build_model_rows(fixtures, odds_tuple, window=20)
    train = tuple(row for row in rows if row.fixture.season < selection_season)
    selection = tuple(row for row in rows if row.fixture.season == selection_season)
    holdout = tuple(row for row in rows if row.fixture.season == holdout_season)
    if not train or len(selection) < 100 or len(holdout) < 100:
        raise ValueError("multi-season train, selection and holdout samples are required")

    selection_fit, selection_calibration = _chronological_halves(selection)
    validation_models = _fit_model_families(train)
    validation_predictions = {
        name: _predict_rows(model, selection_fit) for name, model in validation_models.items()
    }
    calibration_inputs = {
        name: _predict_rows(model, selection_calibration)
        for name, model in validation_models.items()
    }
    calibrated_candidates = _calibrated_candidates(
        validation_predictions,
        calibration_inputs,
        selection_fit,
        selection_calibration,
    )
    candidate_scores = {
        name: _evaluation_payload(predictions, selection_calibration)
        for name, predictions in calibrated_candidates.items()
    }
    selected_name = min(
        (name for name in candidate_scores if name != "MARKET_BASELINE"),
        key=lambda name: float(candidate_scores[name]["brier"]),
    )

    final_models = _fit_model_families((*train, *selection))
    prior_models = _fit_model_families(train)
    prior_selection_predictions = {
        name: _predict_rows(model, selection) for name, model in prior_models.items()
    }
    final_raw = {name: _predict_rows(model, holdout) for name, model in final_models.items()}
    final_candidates = _apply_selected_calibration_family(
        selected_name,
        final_raw,
        prior_selection_predictions,
        selection,
        holdout,
    )
    market_holdout = tuple(row.market_probabilities for row in holdout)
    c20_holdout = _c20_probabilities(fixtures, holdout)
    model_holdout = final_candidates[selected_name]

    comparisons: dict[str, Any] = {
        "C_WINDOW_20": _evaluation_payload(c20_holdout, holdout),
        "MARKET_BASELINE": _evaluation_payload(market_holdout, holdout),
    }
    for name, probabilities in final_candidates.items():
        comparisons[name] = _evaluation_payload(probabilities, holdout)
        comparisons[name]["betting"] = _betting_evaluation(probabilities, holdout)
    comparisons["C_WINDOW_20"]["betting"] = _betting_evaluation(c20_holdout, holdout)
    comparisons["MARKET_BASELINE"]["betting"] = _betting_evaluation(market_holdout, holdout)

    market_brier = float(comparisons["MARKET_BASELINE"]["brier"])
    market_log_loss = float(comparisons["MARKET_BASELINE"]["log_loss"])
    model_brier = float(comparisons[selected_name]["brier"])
    model_log_loss = float(comparisons[selected_name]["log_loss"])
    robust = _robustness(
        model_holdout,
        holdout,
        selection_result=candidate_scores[selected_name],
        selection_season=selection_season,
        holdout_season=holdout_season,
    )
    beats_market = model_brier < market_brier and model_log_loss < market_log_loss
    betting = comparisons[selected_name]["betting"]["positive_ev"]
    stable = robust["league_brier_range"] < 0.12 and robust["walk_forward_brier_range"] < 0.08
    positive_not_tiny = (
        betting["bet_count"] >= 100 and betting["roi"] is not None and betting["roi"] > 0
    )
    decision = (
        "ZERO_COST_MODEL_PROMISING"
        if beats_market and positive_not_tiny and stable
        else "ZERO_COST_MODEL_RESEARCH_ONLY"
    )

    return {
        "task_id": "TASK-20260913-056",
        "status": "PASS",
        "decision": decision,
        "predictive_edge_statement": (
            "INCREMENTAL_PREDICTIVE_EDGE_EVIDENCE"
            if beats_market
            else "NO_PREDICTIVE_EDGE_EVIDENCE"
        ),
        "dataset": {
            "fixtures": len(fixtures),
            "market_aligned_rows": len(rows),
            "train_rows": len(train),
            "selection_rows": len(selection),
            "holdout_rows": len(holdout),
            "leagues": sorted({row.fixture.competition for row in rows}),
            "seasons": sorted({row.fixture.season for row in rows}),
        },
        "frozen_task_055_benchmark": {
            "model": "C_WINDOW_20",
            "sample_size": 790,
            "brier": 0.6195309914406985,
            "log_loss": 1.0323687040304372,
            "roi": -0.12455950540958269,
            "status": "RESEARCH_FEASIBLE_ONLY",
            "production_candidate": False,
            "modified": False,
        },
        "leakage_controls": {
            "market_feature_context": "PRE_CLOSING_ONLY",
            "market_timing_contract": ("RESEARCH_BENCHMARK_ONLY_CAPTURE_TIME_UNAVAILABLE"),
            "market_live_feature_eligible": False,
            "closing_odds_usage": "SETTLEMENT_AND_CLV_EVALUATION_ONLY",
            "selection_season": selection_season,
            "final_holdout_season": holdout_season,
            "final_oos_threshold_optimization": False,
            "future_match_data_used": False,
        },
        "feature_names": FEATURE_NAMES,
        "model_families": {
            "multinomial_logistic": "evaluated",
            "poisson_style_C_WINDOW_20": "evaluated unchanged",
            "market_offset_residual_logistic": "evaluated",
            "gradient_boosted_decision_stumps": "evaluated",
            "calibrated_ensemble": "evaluated",
            "external_ml_dependency_added": False,
        },
        "calibration": {
            "platt": "trained only on past season predictions",
            "isotonic": "trained only on past season predictions",
            "temperature": "selection-grid fitted before final holdout",
        },
        "selection_candidate_scores": candidate_scores,
        "selected_model": selected_name,
        "holdout": comparisons,
        "robustness": robust,
        "required_outputs": {
            "MARKET_BRIER": market_brier,
            "MARKET_LOGLOSS": market_log_loss,
            "MODEL_BRIER": model_brier,
            "MODEL_LOGLOSS": model_log_loss,
            "MODEL_MINUS_MARKET_BRIER": model_brier - market_brier,
            "MODEL_MINUS_MARKET_LOGLOSS": model_log_loss - market_log_loss,
        },
        "production_candidate": False,
        "production_reason": (
            "Research-only source rights remain unconfirmed and the frozen production "
            "completeness contract is unchanged; this task cannot promote any model."
        ),
    }


PredictiveModel = SoftmaxModel | BoostedResidualModel


def _fit_model_families(rows: Sequence[ModelRow]) -> dict[str, PredictiveModel]:
    return {
        "FOOTBALL_LOGIT": fit_softmax(rows, market_offset=False),
        "MARKET_PLUS_FEATURES": fit_softmax(
            rows,
            market_offset=False,
            market_features=True,
        ),
        "MARKET_RESIDUAL": fit_softmax(rows, market_offset=True),
        "MARKET_RESIDUAL_BOOST": fit_boosted_residual(rows),
    }


def _predict_rows(
    model: PredictiveModel, rows: Sequence[ModelRow]
) -> tuple[tuple[float, float, float], ...]:
    return tuple(model.predict(row) for row in rows)


def _calibrated_candidates(
    first_half: dict[str, tuple[tuple[float, float, float], ...]],
    second_half: dict[str, tuple[tuple[float, float, float], ...]],
    first_rows: Sequence[ModelRow],
    second_rows: Sequence[ModelRow],
) -> dict[str, tuple[tuple[float, float, float], ...]]:
    actual = tuple(row.actual_index for row in first_rows)
    candidates: dict[str, tuple[tuple[float, float, float], ...]] = {
        "MARKET_BASELINE": tuple(row.market_probabilities for row in second_rows)
    }
    for name, fit_predictions in first_half.items():
        validation = second_half[name]
        candidates[name] = validation
        platt = fit_platt(fit_predictions, actual)
        isotonic = fit_isotonic(fit_predictions, actual)
        candidates[f"{name}_PLATT"] = tuple(platt.apply(values) for values in validation)
        candidates[f"{name}_ISOTONIC"] = tuple(isotonic.apply(values) for values in validation)
        temperature = _fit_temperature(fit_predictions, actual)
        candidates[f"{name}_TEMPERATURE"] = tuple(
            _apply_temperature(values, temperature) for values in validation
        )
    base_names = ("MARKET_RESIDUAL", "MARKET_RESIDUAL_BOOST")
    candidates["CALIBRATED_ENSEMBLE"] = tuple(
        _normalize(
            tuple(
                (
                    second_half[base_names[0]][index][class_index]
                    + second_half[base_names[1]][index][class_index]
                    + second_rows[index].market_probabilities[class_index]
                )
                / 3
                for class_index in range(3)
            )
        )
        for index in range(len(second_rows))
    )
    return candidates


def _apply_selected_calibration_family(
    selected_name: str,
    final_raw: dict[str, tuple[tuple[float, float, float], ...]],
    calibration_raw: dict[str, tuple[tuple[float, float, float], ...]],
    calibration_rows: Sequence[ModelRow],
    final_rows: Sequence[ModelRow],
) -> dict[str, tuple[tuple[float, float, float], ...]]:
    actual = tuple(row.actual_index for row in calibration_rows)
    values: dict[str, tuple[tuple[float, float, float], ...]] = dict(final_raw)
    for name, predictions in final_raw.items():
        platt = fit_platt(calibration_raw[name], actual)
        isotonic = fit_isotonic(calibration_raw[name], actual)
        temperature = _fit_temperature(calibration_raw[name], actual)
        values[f"{name}_PLATT"] = tuple(platt.apply(item) for item in predictions)
        values[f"{name}_ISOTONIC"] = tuple(isotonic.apply(item) for item in predictions)
        values[f"{name}_TEMPERATURE"] = tuple(
            _apply_temperature(item, temperature) for item in predictions
        )
    ensemble = tuple(
        _normalize(
            tuple(
                (
                    final_raw["MARKET_RESIDUAL"][index][class_index]
                    + final_raw["MARKET_RESIDUAL_BOOST"][index][class_index]
                    + final_rows[index].market_probabilities[class_index]
                )
                / 3
                for class_index in range(3)
            )
        )
        for index in range(len(next(iter(final_raw.values()))))
    )
    values["CALIBRATED_ENSEMBLE"] = ensemble
    if selected_name not in values:
        raise AssertionError(f"selected candidate cannot be reproduced: {selected_name}")
    return values


def _features(
    sample: TemporalSample,
    league_goal_mean: float,
    opponent_strength_edge: float,
    promoted: set[tuple[str, str, str]],
) -> tuple[float, ...]:
    home = sample.home_history
    away = sample.away_history
    home_venue = tuple(row for row in home if row.is_home) or home
    away_venue = tuple(row for row in away if not row.is_home) or away
    home_goals = _mean_attr(home, "goals_for")
    away_goals = _mean_attr(away, "goals_for")
    home_against = _mean_attr(home, "goals_against")
    away_against = _mean_attr(away, "goals_against")
    home_ppm = _mean_points(home)
    away_ppm = _mean_points(away)
    season_key = (sample.fixture.competition, sample.fixture.season)
    return (
        home_goals - away_goals,
        away_against - home_against,
        _mean_attr(home_venue, "goals_for") - _mean_attr(away_venue, "goals_for"),
        _mean_points(home_venue) - _mean_points(away_venue),
        _metric_mean(home, "shots") - _metric_mean(away, "shots"),
        _metric_mean(home, "shots_on_target") - _metric_mean(away, "shots_on_target"),
        _metric_mean(home, "conversion") - _metric_mean(away, "conversion"),
        _metric_against_mean(away, "shots") - _metric_against_mean(home, "shots"),
        home_ppm - away_ppm,
        (home_goals - home_against) - (away_goals - away_against),
        _rest_days(sample.fixture, home) - _rest_days(sample.fixture, away),
        opponent_strength_edge,
        _ewma_form(home) - _ewma_form(away),
        ((home_goals + away_against) - (away_goals + home_against)) / max(league_goal_mean, 0.1),
        min((len(home) + len(away)) / 76, 1.0),
        float((*season_key, sample.fixture.home_team) in promoted),
        float((*season_key, sample.fixture.away_team) in promoted),
    )


def _feature_history_complete(sample: TemporalSample) -> bool:
    return all(
        all(
            row.metrics_for.shots is not None
            and row.metrics_for.shots_on_target is not None
            and row.metrics_for.conversion is not None
            and row.metrics_against.shots is not None
            for row in history
        )
        for history in (sample.home_history, sample.away_history)
    )


def _promoted_teams(fixtures: Sequence[ResearchFixture]) -> set[tuple[str, str, str]]:
    rosters: dict[tuple[str, str], set[str]] = {}
    seasons: dict[str, list[str]] = {}
    for fixture in fixtures:
        key = (fixture.competition, fixture.season)
        rosters.setdefault(key, set()).update((fixture.home_team, fixture.away_team))
        seasons.setdefault(fixture.competition, []).append(fixture.season)
    promoted: set[tuple[str, str, str]] = set()
    for competition, values in seasons.items():
        ordered = sorted(set(values))
        for index, season in enumerate(ordered):
            if index == 0:
                continue
            previous = rosters[(competition, ordered[index - 1])]
            for team in rosters[(competition, season)] - previous:
                promoted.add((competition, season, team))
    return promoted


def _preferred_quotes(
    odds: Iterable[HistoricalOddsQuote], context: str
) -> dict[str, HistoricalOddsQuote]:
    priority = {"market-average": 3, "Pinnacle": 2, "Bet365": 1}
    selected: dict[str, HistoricalOddsQuote] = {}
    for quote in odds:
        if quote.context != context:
            continue
        current = selected.get(quote.fixture_id)
        if current is None or priority.get(quote.bookmaker, 0) > priority.get(current.bookmaker, 0):
            selected[quote.fixture_id] = quote
    return selected


def _model_features(row: ModelRow, *, include_market: bool) -> tuple[float, ...]:
    if not include_market:
        return row.features
    home, draw, away = row.market_probabilities
    return (
        *row.features,
        math.log(max(home, 1e-12) / max(draw, 1e-12)),
        math.log(max(away, 1e-12) / max(draw, 1e-12)),
    )


def _league_goal_means(fixtures: Sequence[ResearchFixture]) -> dict[str, float]:
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    result: dict[str, float] = {}
    for fixture in sorted(fixtures, key=lambda item: item.kickoff):
        key = (fixture.competition, fixture.season)
        goals, matches = totals.get(key, (0, 0))
        result[fixture.fixture_id] = goals / matches if matches else 2.5
        totals[key] = (
            goals + fixture.home.goals + fixture.away.goals,
            matches + 1,
        )
    return result


def _pre_match_elo_edges(fixtures: Sequence[ResearchFixture]) -> dict[str, float]:
    ratings: dict[tuple[str, str, str], float] = {}
    result: dict[str, float] = {}
    for fixture in sorted(fixtures, key=lambda item: item.kickoff):
        base = (fixture.competition.casefold(), fixture.season.casefold())
        home_key = (*base, fixture.home_team.casefold())
        away_key = (*base, fixture.away_team.casefold())
        home_rating = ratings.get(home_key, 1500.0)
        away_rating = ratings.get(away_key, 1500.0)
        result[fixture.fixture_id] = (home_rating - away_rating) / 400.0
        expected_home = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        actual_home = (
            1.0
            if fixture.home.goals > fixture.away.goals
            else 0.5 if fixture.home.goals == fixture.away.goals else 0.0
        )
        delta = 20.0 * (actual_home - expected_home)
        ratings[home_key] = home_rating + delta
        ratings[away_key] = away_rating - delta
    return result


def _fit_scaler(matrix: Sequence[Sequence[float]]) -> Scaler:
    columns = tuple(zip(*matrix, strict=True))
    means = tuple(sum(column) / len(column) for column in columns)
    scales = tuple(
        max(math.sqrt(sum((value - mean) ** 2 for value in column) / len(column)), 1e-9)
        for column, mean in zip(columns, means, strict=True)
    )
    return Scaler(means, scales)


def _candidate_thresholds(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(
            sorted(column)[int((len(column) - 1) * quantile)]
            for quantile in (0.15, 0.3, 0.5, 0.7, 0.85)
        )
        for column in zip(*matrix, strict=True)
    )


def _best_stump(
    matrix: Sequence[Sequence[float]],
    residuals: Sequence[float],
    thresholds: Sequence[Sequence[float]],
) -> Stump:
    best: tuple[float, Stump] | None = None
    for feature, values in enumerate(thresholds):
        for threshold in values:
            left = [
                residual
                for row, residual in zip(matrix, residuals, strict=True)
                if row[feature] <= threshold
            ]
            right = [
                residual
                for row, residual in zip(matrix, residuals, strict=True)
                if row[feature] > threshold
            ]
            if not left or not right:
                continue
            left_mean = sum(left) / len(left)
            right_mean = sum(right) / len(right)
            error = sum(
                (residual - (left_mean if row[feature] <= threshold else right_mean)) ** 2
                for row, residual in zip(matrix, residuals, strict=True)
            )
            stump = Stump(feature, threshold, left_mean, right_mean)
            if best is None or error < best[0]:
                best = (error, stump)
    if best is None:
        return Stump(0, 0.0, 0.0, 0.0)
    return best[1]


def _fit_temperature(
    probabilities: Sequence[tuple[float, float, float]], actual: Sequence[int]
) -> float:
    candidates = tuple(0.5 + index * 0.05 for index in range(41))
    return min(
        candidates,
        key=lambda temperature: _log_loss(
            tuple(_apply_temperature(values, temperature) for values in probabilities), actual
        ),
    )


def _apply_temperature(
    probabilities: Sequence[float], temperature: float
) -> tuple[float, float, float]:
    return _softmax(tuple(math.log(max(value, 1e-12)) / temperature for value in probabilities))


def _fit_isotonic_curve(values: Sequence[tuple[float, int]]) -> IsotonicCurve:
    blocks: list[list[float]] = []
    for probability, target in sorted(values):
        blocks.append([probability, probability, float(target), 1.0])
        while len(blocks) >= 2 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    return IsotonicCurve(
        tuple(block[1] for block in blocks),
        tuple(block[2] / block[3] for block in blocks),
    )


def _evaluation_payload(
    probabilities: Sequence[tuple[float, float, float]], rows: Sequence[ModelRow]
) -> dict[str, Any]:
    predictions = tuple(
        Prediction(
            row.fixture.fixture_id,
            row.fixture.competition,
            row.fixture.kickoff,
            "RESEARCH",
            20,
            *values,
            OUTCOMES[row.actual_index],
        )
        for values, row in zip(probabilities, rows, strict=True)
    )
    return asdict(evaluate(predictions))


def _betting_evaluation(
    probabilities: Sequence[tuple[float, float, float]], rows: Sequence[ModelRow]
) -> dict[str, Any]:
    all_bets: list[tuple[float, ModelRow, int, float, float]] = []
    positive: list[tuple[float, ModelRow, int, float, float]] = []
    for values, row in zip(probabilities, rows, strict=True):
        selected = max(range(3), key=values.__getitem__)
        max_ev_selection = max(
            range(3), key=lambda index: values[index] * row.offered_odds[index] - 1
        )
        ev = values[max_ev_selection] * row.offered_odds[max_ev_selection] - 1
        all_bets.append((_profit(row, selected), row, selected, ev, max(values)))
        if ev > 0:
            positive.append(
                (_profit(row, max_ev_selection), row, max_ev_selection, ev, max(values))
            )
    return {
        "all_model_selections": _bet_summary(all_bets),
        "positive_ev": _bet_summary(positive),
        "positive_ev_breakdowns": {
            "ev_bucket": _group_bets(positive, lambda item: _ev_bucket(item[3])),
            "confidence_bucket": _group_bets(positive, lambda item: _confidence_bucket(item[4])),
            "league": _group_bets(positive, lambda item: item[1].fixture.competition),
            "season": _group_bets(positive, lambda item: item[1].fixture.season),
            "selection": _group_bets(positive, lambda item: OUTCOMES[item[2]]),
            "odds_range": _group_bets(
                positive, lambda item: _odds_bucket(item[1].offered_odds[item[2]])
            ),
        },
        "note": "Research-only unit stakes at PRE_CLOSING prices; production EV/Kelly/Gate not used.",
    }


def _bet_summary(values: Sequence[tuple[float, ModelRow, int, float, float]]) -> dict[str, Any]:
    profits = tuple(item[0] for item in values)
    if not profits:
        return {
            "bet_count": 0,
            "roi": None,
            "yield": None,
            "max_drawdown": None,
            "roi_95ci": None,
            "average_closing_value": None,
        }
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for profit in profits:
        cumulative += profit
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return {
        "bet_count": len(profits),
        "roi": sum(profits) / len(profits),
        "yield": sum(profits) / len(profits),
        "max_drawdown": drawdown,
        "roi_95ci": _bootstrap_roi_ci(profits),
        "average_closing_value": _average_closing_value(values),
    }


def _average_closing_value(
    values: Sequence[tuple[float, ModelRow, int, float, float]],
) -> float | None:
    closing_values = tuple(
        item[1].offered_odds[item[2]] / item[1].closing_odds[item[2]] - 1
        for item in values
        if item[1].closing_odds is not None
    )
    return sum(closing_values) / len(closing_values) if closing_values else None


def _group_bets(
    values: Sequence[tuple[float, ModelRow, int, float, float]],
    key: Callable[[tuple[float, ModelRow, int, float, float]], str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[float, ModelRow, int, float, float]]] = {}
    for value in values:
        grouped.setdefault(key(value), []).append(value)
    return {name: _bet_summary(group) for name, group in sorted(grouped.items())}


def _bootstrap_roi_ci(profits: Sequence[float], repetitions: int = 500) -> tuple[float, float]:
    generator = random.Random(560)
    estimates = sorted(
        sum(generator.choice(profits) for _ in profits) / len(profits) for _ in range(repetitions)
    )
    return estimates[int(0.025 * repetitions)], estimates[int(0.975 * repetitions) - 1]


def _robustness(
    probabilities: Sequence[tuple[float, float, float]],
    rows: Sequence[ModelRow],
    *,
    selection_result: dict[str, Any],
    selection_season: str,
    holdout_season: str,
) -> dict[str, Any]:
    league: dict[str, dict[str, Any]] = {}
    league_briers: list[float] = []
    for name in sorted({row.fixture.competition for row in rows}):
        indexes = tuple(index for index, row in enumerate(rows) if row.fixture.competition == name)
        payload = _evaluation_payload(
            tuple(probabilities[index] for index in indexes),
            tuple(rows[index] for index in indexes),
        )
        league[name] = payload
        league_briers.append(float(payload["brier"]))
    midpoint = len(rows) // 2
    halves = (
        _evaluation_payload(probabilities[:midpoint], rows[:midpoint]),
        _evaluation_payload(probabilities[midpoint:], rows[midpoint:]),
    )
    half_briers = [float(item["brier"]) for item in halves]
    return {
        "by_league": league,
        "by_season": {
            selection_season: selection_result,
            holdout_season: _evaluation_payload(probabilities, rows),
        },
        "by_holdout_half": halves,
        "league_brier_range": max(league_briers) - min(league_briers),
        "walk_forward_brier_range": max(half_briers) - min(half_briers),
        "minimum_league_sample": min(int(item["sample_size"]) for item in league.values()),
        "cherry_picked_leagues_removed": 0,
    }


def _c20_probabilities(
    fixtures: Sequence[ResearchFixture], rows: Sequence[ModelRow]
) -> tuple[tuple[float, float, float], ...]:
    fixture_ids = {row.fixture.fixture_id for row in rows}
    predictions = _no_xg_predictions(
        tuple(
            sample
            for sample in build_temporal_samples(fixtures, 20)
            if sample.fixture.fixture_id in fixture_ids
        ),
        "C",
    )
    index = {prediction.fixture_id: prediction for prediction in predictions}
    return tuple(
        (
            index[row.fixture.fixture_id].home,
            index[row.fixture.fixture_id].draw,
            index[row.fixture.fixture_id].away,
        )
        for row in rows
    )


def _chronological_halves(
    rows: Sequence[ModelRow],
) -> tuple[tuple[ModelRow, ...], tuple[ModelRow, ...]]:
    midpoint = len(rows) // 2
    return tuple(rows[:midpoint]), tuple(rows[midpoint:])


def _actual_index(fixture: ResearchFixture) -> int:
    return (
        0
        if fixture.home.goals > fixture.away.goals
        else 1 if fixture.home.goals == fixture.away.goals else 2
    )


def _profit(row: ModelRow, selection: int) -> float:
    return row.offered_odds[selection] - 1 if selection == row.actual_index else -1.0


def _mean_attr(history: Sequence[TeamHistoryRow], field: str) -> float:
    return sum(float(getattr(row, field)) for row in history) / len(history)


def _mean_points(history: Sequence[TeamHistoryRow]) -> float:
    return sum(row.points for row in history) / len(history)


def _metric_mean(history: Sequence[TeamHistoryRow], field: str) -> float:
    values = [getattr(row.metrics_for, field) for row in history]
    return sum(float(value) for value in values if value is not None) / len(values)


def _metric_against_mean(history: Sequence[TeamHistoryRow], field: str) -> float:
    values = [getattr(row.metrics_against, field) for row in history]
    return sum(float(value) for value in values if value is not None) / len(values)


def _rest_days(fixture: ResearchFixture, history: Sequence[TeamHistoryRow]) -> float:
    return min((fixture.kickoff - history[-1].kickoff).total_seconds() / 86400, 21.0)


def _ewma_form(history: Sequence[TeamHistoryRow], decay: float = 0.85) -> float:
    weights = tuple(decay ** (len(history) - index - 1) for index in range(len(history)))
    return sum(weight * row.points / 3 for weight, row in zip(weights, history, strict=True)) / sum(
        weights
    )


def _softmax(values: Sequence[float]) -> tuple[float, float, float]:
    maximum = max(values)
    exponentials = tuple(math.exp(value - maximum) for value in values)
    return _normalize(exponentials)


def _normalize(values: Sequence[float]) -> tuple[float, float, float]:
    total = sum(values)
    if total <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    normalized = tuple(value / total for value in values)
    return normalized[0], normalized[1], normalized[2]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _log_loss(probabilities: Sequence[Sequence[float]], actual: Sequence[int]) -> float:
    return -sum(
        math.log(max(values[target], 1e-15))
        for values, target in zip(probabilities, actual, strict=True)
    ) / len(actual)


def _ev_bucket(value: float) -> str:
    if value < 0.02:
        return "0-2%"
    if value < 0.05:
        return "2-5%"
    if value < 0.1:
        return "5-10%"
    return "10%+"


def _confidence_bucket(value: float) -> str:
    if value < 0.45:
        return "<45%"
    if value < 0.55:
        return "45-55%"
    if value < 0.65:
        return "55-65%"
    return "65%+"


def _odds_bucket(value: float) -> str:
    if value < 1.75:
        return "<1.75"
    if value < 2.5:
        return "1.75-2.49"
    if value < 4:
        return "2.50-3.99"
    return "4.00+"
