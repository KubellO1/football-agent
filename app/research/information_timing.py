"""Point-in-time-safe research into zero-cost information and market timing.

The module is deliberately isolated from production DI, repositories and betting
rules. Closing prices are evaluation targets only and never enter model features.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

from app.research.market_improvement import (
    ModelRow,
    PlattCalibrator,
    build_model_rows,
    fit_platt,
    fit_softmax,
    remove_overround,
)
from app.research.zero_cost import OUTCOMES, Prediction, evaluate

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from app.research.football_data import HistoricalOddsQuote
    from app.research.zero_cost import ResearchFixture


@dataclass(frozen=True, slots=True)
class SignalContract:
    name: str
    zero_cost_source: str
    availability_timestamp: str
    historically_reconstructable: bool
    leakage_risk: str
    five_league_coverage: str
    automation_legality: str
    research_status: str
    reason: str


SIGNAL_CONTRACTS = (
    SignalContract(
        "opening_to_later_movement",
        "Football-Data.co.uk public CSV",
        "Opening-context and closing labels exist; row-level capture time does not",
        False,
        "HIGH",
        "Five leagues, three audited seasons",
        "Public direct CSV; production/commercial rights unconfirmed",
        "AUDIT_ONLY",
        "No timestamped intermediate quote exists; closing cannot be an earlier feature.",
    ),
    SignalContract(
        "cross_bookmaker_disagreement",
        "Football-Data.co.uk Bet365 and Bwin PRE_CLOSING columns",
        "Same source-labelled pre-closing collection context; exact row time unavailable",
        False,
        "MEDIUM",
        "Five leagues where both quote triplets are present",
        "Public direct CSV; production/commercial rights unconfirmed",
        "RESEARCH_BENCHMARK_ONLY",
        "Can test historical association, but cannot yet reproduce a live decision timestamp.",
    ),
    SignalContract(
        "confirmed_lineups",
        "StatsBomb Open Data selected competitions",
        "Dataset publication/update time, not historical public-release time",
        False,
        "HIGH",
        "Not longitudinal coverage of all five leagues",
        "Open-data research use with attribution",
        "INSUFFICIENT_LONGITUDINAL_SAMPLE",
        "No legal zero-cost five-league archive of confirmed lineups with release timestamps.",
    ),
    SignalContract(
        "player_absences_suspensions",
        "Official club/team news pages",
        "Article time may exist, but normalized historical availability is incomplete",
        False,
        "HIGH",
        "No complete five-league historical archive identified",
        "Per-site terms vary; no collector approved",
        "AUDIT_ONLY",
        "Absence impact and historical availability cannot be reconstructed uniformly.",
    ),
    SignalContract(
        "official_team_news",
        "Official club websites",
        "Publication timestamp where retained",
        False,
        "HIGH",
        "Fragmented across clubs and changing sites",
        "Per-site terms/robots review required",
        "AUDIT_ONLY",
        "No stable normalized five-league archive or historical edit trail.",
    ),
    SignalContract(
        "rest_days_congestion",
        "Football-Data.co.uk completed fixture schedule",
        "Fixture kickoff known before each target fixture",
        True,
        "LOW",
        "Five leagues, three audited seasons",
        "Public direct CSV; research rights established",
        "MODEL_ELIGIBLE_RESEARCH_ONLY",
        "Computed solely from prior completed fixtures; no future result is used.",
    ),
    SignalContract(
        "weather_forecast",
        "Open-Meteo Previous Runs / Single Runs",
        "Fixed lead-time forecasts are reconstructable for limited date ranges",
        True,
        "LOW_IF_FIXED_RUN_USED",
        "Geographic coverage is sufficient; venue coordinates are absent locally",
        "Official public API under its published terms",
        "INSUFFICIENT_ALIGNED_SAMPLE",
        "Most fixed-run archives begin in 2024 and local venue coordinates are unavailable.",
    ),
    SignalContract(
        "post_information_market_movement",
        "No accepted zero-cost aligned source",
        "Requires both news release and quote-event timestamps",
        False,
        "CRITICAL",
        "Unavailable",
        "Unavailable",
        "UNAVAILABLE",
        "Cannot causally align information arrival with market movement.",
    ),
)


@dataclass(frozen=True, slots=True)
class BetObservation:
    profit: float
    clv: float
    league: str
    season: str


def fair_closing_value(offered_odds: float, closing_odds: Sequence[float], selection: int) -> float:
    """Return price-versus-fair-closing CLV after removing closing overround."""

    return offered_odds * remove_overround(closing_odds)[selection] - 1


def build_signal_rows(
    fixtures: Sequence[ResearchFixture],
    odds: Iterable[HistoricalOddsQuote],
    *,
    feature_set: str,
) -> tuple[ModelRow, ...]:
    """Build rows for a fixed signal set without exposing closing prices as features."""

    odds_tuple = tuple(odds)
    base_rows = build_model_rows(fixtures, odds_tuple, window=20)
    by_book = {
        (quote.fixture_id, quote.bookmaker.casefold()): quote
        for quote in odds_tuple
        if quote.context == "PRE_CLOSING"
    }
    rows: list[ModelRow] = []
    for row in base_rows:
        bet365 = by_book.get((row.fixture.fixture_id, "bet365"))
        bwin = by_book.get((row.fixture.fixture_id, "bwin"))
        cross: tuple[float, ...] = ()
        if bet365 is not None and bwin is not None:
            first = remove_overround((bet365.home, bet365.draw, bet365.away))
            second = remove_overround((bwin.home, bwin.draw, bwin.away))
            cross = (
                *(first[index] - second[index] for index in range(3)),
                *(abs(first[index] - second[index]) for index in range(3)),
                max(abs(first[index] - second[index]) for index in range(3)),
            )
        if feature_set in {"CROSS_BOOK", "CROSS_BOOK_REST", "ALL"} and not cross:
            continue
        features = {
            "REST": (row.features[10],),
            "CROSS_BOOK": cross,
            "CROSS_BOOK_REST": (*cross, row.features[10]),
            "FOOTBALL": row.features,
            "ALL": (*row.features, *cross),
        }.get(feature_set)
        if features is None:
            raise ValueError(f"unknown feature set: {feature_set}")
        rows.append(replace(row, features=tuple(features)))
    return tuple(rows)


def run_information_timing_experiment(
    fixtures: Sequence[ResearchFixture],
    odds: Iterable[HistoricalOddsQuote],
    *,
    selection_season: str = "2024/2025",
    holdout_season: str = "2025/2026",
) -> dict[str, Any]:
    """Evaluate fixed zero-cost signals against the de-vigged market baseline."""

    odds_tuple = tuple(odds)
    common = build_signal_rows(fixtures, odds_tuple, feature_set="CROSS_BOOK_REST")
    train = tuple(row for row in common if row.fixture.season < selection_season)
    selection = tuple(row for row in common if row.fixture.season == selection_season)
    holdout = tuple(row for row in common if row.fixture.season == holdout_season)
    if not train or len(selection) < 100 or len(holdout) < 100:
        raise ValueError("multi-season cross-book train, selection and holdout rows are required")

    feature_sets = ("REST", "CROSS_BOOK", "CROSS_BOOK_REST", "FOOTBALL", "ALL")
    rows_by_set = {
        name: _rows_for_ids(
            build_signal_rows(fixtures, odds_tuple, feature_set=name),
            {row.fixture.fixture_id for row in common},
        )
        for name in feature_sets
    }
    candidates: dict[str, dict[str, Any]] = {}
    fitted: dict[str, tuple[PlattCalibrator, str]] = {}
    models: dict[str, Any] = {}
    for name, rows in rows_by_set.items():
        by_season = _split(rows, selection_season, holdout_season)
        model = fit_softmax(by_season[0], market_offset=True)
        models[name] = model
        selection_probabilities = tuple(model.predict(row) for row in by_season[1])
        calibrator = fit_platt(
            selection_probabilities[: len(selection_probabilities) // 2],
            tuple(row.actual_index for row in by_season[1][: len(selection_probabilities) // 2]),
        )
        later = by_season[1][len(selection_probabilities) // 2 :]
        later_raw = selection_probabilities[len(selection_probabilities) // 2 :]
        later_platt = tuple(calibrator.apply(values) for values in later_raw)
        raw_score = _score(later_raw, later)
        platt_score = _score(later_platt, later)
        calibration = "PLATT" if _ranking(platt_score) < _ranking(raw_score) else "RAW"
        candidates[name] = {
            "selection_raw": raw_score,
            "selection_platt": platt_score,
            "selected_calibration": calibration,
        }
        fitted[name] = (calibrator, calibration)

    selected_name = min(
        candidates,
        key=lambda name: _ranking(
            candidates[name]["selection_" + candidates[name]["selected_calibration"].lower()]
        ),
    )
    signal_oos_evaluation = _evaluate_oos_candidates(
        rows_by_set,
        models,
        fitted,
        selection_season=selection_season,
        holdout_season=holdout_season,
    )
    final_rows = rows_by_set[selected_name]
    final_train, _, final_holdout = _split(final_rows, selection_season, holdout_season)
    final_model = fit_softmax(
        (*final_train, *_season(final_rows, selection_season)), market_offset=True
    )
    raw_holdout = tuple(final_model.predict(row) for row in final_holdout)
    calibrator, calibration = fitted[selected_name]
    holdout_probabilities = (
        tuple(calibrator.apply(values) for values in raw_holdout)
        if calibration == "PLATT"
        else raw_holdout
    )
    market_probabilities = tuple(row.market_probabilities for row in final_holdout)
    market_score = _score(market_probabilities, final_holdout)
    model_score = _score(holdout_probabilities, final_holdout)
    betting = _betting(holdout_probabilities, final_holdout)
    closing = _closing_benchmark(final_holdout)
    robustness = _robustness(holdout_probabilities, market_probabilities, final_holdout)
    walk_forward = _walk_forward(rows_by_set[selected_name], selected_name)

    beats_market = _ranking(model_score) < _ranking(market_score)
    stable = int(robustness["leagues_model_beats_market"]) >= 3
    positive_clv = betting["mean_clv"] is not None and float(betting["mean_clv"]) > 0
    clv_ci = betting["clv_95ci"]
    clv_supported = clv_ci is not None and float(clv_ci[0]) > 0
    not_tiny = int(betting["bet_count"]) >= 200
    timestamp_valid = selected_name == "REST"
    edge = all((beats_market, stable, positive_clv, clv_supported, not_tiny, timestamp_valid))

    return {
        "task_id": "TASK-20260913-057",
        "status": "PASS",
        "decision": (
            "ZERO_COST_BETTING_EDGE_DEMONSTRATED"
            if edge
            else "ZERO_COST_BETTING_EDGE_NOT_DEMONSTRATED"
        ),
        "signal_contracts": [asdict(item) for item in SIGNAL_CONTRACTS],
        "dataset": {
            "fixtures": len(fixtures),
            "common_cross_book_rows": len(common),
            "train": len(train),
            "selection": len(selection),
            "holdout": len(holdout),
            "leagues": sorted({row.fixture.competition for row in common}),
            "seasons": sorted({row.fixture.season for row in common}),
        },
        "point_in_time_controls": {
            "closing_odds_as_feature": False,
            "closing_odds_usage": "EVALUATION_AND_CLV_ONLY",
            "intermediate_odds_inferred": False,
            "future_results_used": False,
            "pre_closing_capture_time_available": False,
            "production_deployable_signal": False,
        },
        "candidate_selection": candidates,
        "signal_oos_evaluation": signal_oos_evaluation,
        "selected_signal_model": selected_name,
        "market_baseline": market_score,
        "best_model": model_score,
        "closing_market_evaluation": closing,
        "betting": betting,
        "robustness": robustness,
        "walk_forward": walk_forward,
        "required_outputs": {
            "BEST_ZERO_COST_SIGNAL": selected_name,
            "MARKET_BASELINE_BRIER": market_score["brier"],
            "BEST_MODEL_BRIER": model_score["brier"],
            "INCREMENTAL_EDGE": float(market_score["brier"]) - float(model_score["brier"]),
            "MEAN_CLV": betting["mean_clv"],
            "OOS_ROI": betting["roi"],
            "ROI_95_CI": betting["roi_95ci"],
            "EDGE_EVIDENCE": "yes" if edge else "no",
        },
        "edge_criteria": {
            "beats_market_brier_and_logloss": beats_market,
            "beats_market_in_at_least_three_leagues": stable,
            "positive_clv": positive_clv,
            "clv_confidence_interval_above_zero": clv_supported,
            "minimum_200_bets": not_tiny,
            "historically_reconstructable_timestamp": timestamp_valid,
            "thresholds_optimized_on_holdout": False,
        },
        "production_candidate": False,
    }


def _evaluate_oos_candidates(
    rows_by_set: dict[str, tuple[ModelRow, ...]],
    models: dict[str, Any],
    fitted: dict[str, tuple[PlattCalibrator, str]],
    *,
    selection_season: str,
    holdout_season: str,
) -> dict[str, dict[str, Any]]:
    """Evaluate every selected-in-advance signal specification on one OOS holdout."""

    result: dict[str, dict[str, Any]] = {}
    for name, rows in rows_by_set.items():
        _, _, holdout = _split(rows, selection_season, holdout_season)
        model = models[name]
        raw_probabilities = tuple(model.predict(row) for row in holdout)
        calibrator, calibration = fitted[name]
        probabilities = (
            tuple(calibrator.apply(values) for values in raw_probabilities)
            if calibration == "PLATT"
            else raw_probabilities
        )
        market_probabilities = tuple(row.market_probabilities for row in holdout)
        model_score = _score(probabilities, holdout)
        market_score = _score(market_probabilities, holdout)
        result[name] = {
            "sample_size": len(holdout),
            "calibration_selected_before_holdout": calibration,
            "model": model_score,
            "market": market_score,
            "incremental_brier": float(market_score["brier"]) - float(model_score["brier"]),
            "incremental_log_loss": float(market_score["log_loss"])
            - float(model_score["log_loss"]),
            "betting": _betting(probabilities, holdout),
        }
    return result


def _rows_for_ids(rows: Sequence[ModelRow], fixture_ids: set[str]) -> tuple[ModelRow, ...]:
    return tuple(row for row in rows if row.fixture.fixture_id in fixture_ids)


def _season(rows: Sequence[ModelRow], season: str) -> tuple[ModelRow, ...]:
    return tuple(row for row in rows if row.fixture.season == season)


def _split(
    rows: Sequence[ModelRow], selection_season: str, holdout_season: str
) -> tuple[tuple[ModelRow, ...], tuple[ModelRow, ...], tuple[ModelRow, ...]]:
    return (
        tuple(row for row in rows if row.fixture.season < selection_season),
        _season(rows, selection_season),
        _season(rows, holdout_season),
    )


def _score(
    probabilities: Sequence[tuple[float, float, float]], rows: Sequence[ModelRow]
) -> dict[str, Any]:
    predictions = tuple(
        Prediction(
            row.fixture.fixture_id,
            row.fixture.competition,
            row.fixture.kickoff,
            "INFORMATION_TIMING_RESEARCH",
            20,
            *values,
            OUTCOMES[row.actual_index],
        )
        for values, row in zip(probabilities, rows, strict=True)
    )
    return asdict(evaluate(predictions))


def _ranking(score: dict[str, Any]) -> tuple[float, float]:
    return float(score["brier"]), float(score["log_loss"])


def _betting(
    probabilities: Sequence[tuple[float, float, float]], rows: Sequence[ModelRow]
) -> dict[str, Any]:
    observations: list[BetObservation] = []
    for values, row in zip(probabilities, rows, strict=True):
        selection = max(range(3), key=lambda index: values[index] * row.offered_odds[index] - 1)
        if values[selection] * row.offered_odds[selection] - 1 <= 0 or row.closing_odds is None:
            continue
        observations.append(
            BetObservation(
                row.offered_odds[selection] - 1 if selection == row.actual_index else -1,
                fair_closing_value(row.offered_odds[selection], row.closing_odds, selection),
                row.fixture.competition,
                row.fixture.season,
            )
        )
    profits = tuple(item.profit for item in observations)
    clv = tuple(item.clv for item in observations)
    return {
        "bet_count": len(observations),
        "roi": sum(profits) / len(profits) if profits else None,
        "roi_95ci": _bootstrap_ci(profits),
        "mean_clv": sum(clv) / len(clv) if clv else None,
        "clv_95ci": _bootstrap_ci(clv),
        "by_league": {
            league: {
                "bet_count": len(items),
                "roi": sum(item.profit for item in items) / len(items),
                "mean_clv": sum(item.clv for item in items) / len(items),
            }
            for league in sorted({item.league for item in observations})
            if (items := [item for item in observations if item.league == league])
        },
        "decision_rule": "One maximum-model-EV selection per match when model EV > 0; unit stake",
    }


def _bootstrap_ci(values: Sequence[float], repetitions: int = 1000) -> tuple[float, float] | None:
    if not values:
        return None
    generator = random.Random(570)
    estimates = sorted(
        sum(generator.choice(values) for _ in values) / len(values) for _ in range(repetitions)
    )
    return estimates[24], estimates[974]


def _closing_benchmark(rows: Sequence[ModelRow]) -> dict[str, Any]:
    complete = tuple(row for row in rows if row.closing_odds is not None)
    probabilities = tuple(remove_overround(row.closing_odds or ()) for row in complete)
    movement = tuple(
        sum(
            abs(current - closing)
            for current, closing in zip(row.market_probabilities, values, strict=True)
        )
        / 3
        for row, values in zip(complete, probabilities, strict=True)
    )
    return {
        **_score(probabilities, complete),
        "sample_size": len(complete),
        "mean_absolute_probability_movement": sum(movement) / len(movement),
        "usage": "EVALUATION_ONLY_NOT_A_FEATURE",
    }


def _robustness(
    probabilities: Sequence[tuple[float, float, float]],
    market: Sequence[tuple[float, float, float]],
    rows: Sequence[ModelRow],
) -> dict[str, Any]:
    by_league: dict[str, Any] = {}
    wins = 0
    for league in sorted({row.fixture.competition for row in rows}):
        indexes = tuple(
            index for index, row in enumerate(rows) if row.fixture.competition == league
        )
        league_rows = tuple(rows[index] for index in indexes)
        model_score = _score(tuple(probabilities[index] for index in indexes), league_rows)
        market_score = _score(tuple(market[index] for index in indexes), league_rows)
        beat = _ranking(model_score) < _ranking(market_score)
        wins += int(beat)
        by_league[league] = {"model": model_score, "market": market_score, "model_beats": beat}
    return {"by_league": by_league, "leagues_model_beats_market": wins}


def _walk_forward(rows: Sequence[ModelRow], feature_set: str) -> dict[str, Any]:
    seasons = sorted({row.fixture.season for row in rows})
    result: dict[str, Any] = {}
    for season in seasons[1:]:
        train = tuple(row for row in rows if row.fixture.season < season)
        test = _season(rows, season)
        model = fit_softmax(train, market_offset=True)
        probabilities = tuple(model.predict(row) for row in test)
        result[season] = {
            "feature_set": feature_set,
            "model": _score(probabilities, test),
            "market": _score(tuple(row.market_probabilities for row in test), test),
        }
    return result
