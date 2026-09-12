"""Research-only longitudinal evaluation around the unchanged TASK-054 runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.research.zero_cost import (
    VARIANT_FEATURES,
    Prediction,
    ResearchFixture,
    ZeroCostExperimentRunner,
    build_temporal_samples,
    evaluate,
    predict_sample,
    variant_is_available,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from app.research.football_data import HistoricalOddsQuote


@dataclass(frozen=True, slots=True)
class MarketBacktest:
    status: str
    sample_size: int
    odds_match_count: int
    signal_count: int
    unit_stake_roi: float | None
    max_drawdown_units: float | None
    note: str


def run_longitudinal_validation(
    fixtures: Iterable[ResearchFixture],
    odds: Iterable[HistoricalOddsQuote],
    *,
    holdout_season: str,
    windows: Sequence[int] = (5, 10, 20),
) -> dict[str, Any]:
    """Run TASK-054 unchanged and validate on a season not used for selection."""

    ordered = tuple(sorted(fixtures, key=lambda item: item.kickoff))
    baseline = ZeroCostExperimentRunner().run(ordered, windows=windows).to_dict()
    seasons = sorted({fixture.season for fixture in ordered})
    leagues = sorted({fixture.competition for fixture in ordered})
    selection_season = _selection_season(seasons, holdout_season)
    holdout: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    best: tuple[float, str, int] | None = None
    for window in windows:
        all_samples = build_temporal_samples(ordered, window)
        holdout_samples = tuple(
            sample for sample in all_samples if sample.fixture.season == holdout_season
        )
        selection_samples = tuple(
            sample for sample in all_samples if sample.fixture.season == selection_season
        )
        holdout_variants: dict[str, Any] = {}
        selection_variants: dict[str, Any] = {}
        for variant in VARIANT_FEATURES:
            holdout_predictions = _predictions(holdout_samples, variant)
            holdout_result = evaluate(holdout_predictions)
            selection_predictions = _predictions(selection_samples, variant)
            selection_result = evaluate(selection_predictions)
            holdout_variants[variant] = {
                "evaluation": asdict(holdout_result),
                "by_league": {
                    league: asdict(
                        evaluate(
                            tuple(
                                item for item in holdout_predictions if item.competition == league
                            )
                        )
                    )
                    for league in leagues
                },
            }
            selection_variants[variant] = {"evaluation": asdict(selection_result)}
            if selection_result.brier is not None and (
                best is None or selection_result.brier < best[0]
            ):
                best = (selection_result.brier, variant, window)
        holdout[str(window)] = {
            "sample_count": len(holdout_samples),
            "variants": holdout_variants,
        }
        selection[str(window)] = {
            "sample_count": len(selection_samples),
            "variants": selection_variants,
        }
    quote_index = _preferred_closing_quotes(odds)
    if best is None:
        backtest = MarketBacktest(
            "INSUFFICIENT_LONGITUDINAL_SAMPLE",
            0,
            0,
            0,
            None,
            None,
            "No holdout prediction was available.",
        )
        best_model = "UNAVAILABLE"
        selected_evaluation = asdict(evaluate(()))
        selected_by_league: dict[str, Any] = {}
        season_stability: dict[str, Any] = {}
    else:
        _, variant, window = best
        best_model = f"{variant}_WINDOW_{window}"
        selected_holdout = _predictions(
            tuple(
                sample
                for sample in build_temporal_samples(ordered, window)
                if sample.fixture.season == holdout_season
            ),
            variant,
        )
        selected_evaluation = asdict(evaluate(selected_holdout))
        backtest = evaluate_market_targets(selected_holdout, quote_index)
        selected_by_league = {
            league: {
                "evaluation": asdict(
                    evaluate(tuple(item for item in selected_holdout if item.competition == league))
                ),
                "market_backtest": asdict(
                    evaluate_market_targets(
                        tuple(item for item in selected_holdout if item.competition == league),
                        quote_index,
                    )
                ),
            }
            for league in leagues
        }
        season_stability = _season_stability(
            ordered,
            quote_index,
            seasons=seasons,
            window=window,
            variant=variant,
        )
    return {
        "status": "PASS" if best is not None else "INSUFFICIENT_LONGITUDINAL_SAMPLE",
        "baseline_runner_modified": False,
        "baseline": baseline,
        "holdout_season": holdout_season,
        "selection_season": selection_season,
        "selection": selection,
        "holdout": holdout,
        "best_holdout_model": best_model,
        "selected_holdout_evaluation": selected_evaluation,
        "selected_holdout_by_league": selected_by_league,
        "season_stability": season_stability,
        "market_backtest": asdict(backtest),
        "fixture_count": len(ordered),
        "odds_match_count": len(quote_index),
        "leagues": leagues,
        "seasons": seasons,
        "production_candidate": False,
        "production_blocker": (
            "Football-Data usage is suitable for this research validation, but commercial "
            "production rights and the frozen 90% completeness contract are not satisfied."
        ),
    }


def _selection_season(seasons: Sequence[str], holdout_season: str) -> str | None:
    earlier = [season for season in seasons if season < holdout_season]
    return max(earlier, default=None)


def _predictions(
    samples: Sequence[Any],
    variant: str,
) -> tuple[Prediction, ...]:
    return tuple(
        predict_sample(sample, variant)
        for sample in samples
        if variant_is_available(sample, variant)
    )


def _season_stability(
    fixtures: Sequence[ResearchFixture],
    quote_index: dict[str, HistoricalOddsQuote],
    *,
    seasons: Sequence[str],
    window: int,
    variant: str,
) -> dict[str, Any]:
    samples = build_temporal_samples(fixtures, window)
    results: dict[str, Any] = {}
    for season in seasons:
        predictions = _predictions(
            tuple(sample for sample in samples if sample.fixture.season == season),
            variant,
        )
        results[season] = {
            "evaluation": asdict(evaluate(predictions)),
            "market_backtest": asdict(evaluate_market_targets(predictions, quote_index)),
        }
    return results


def evaluate_market_targets(
    predictions: Sequence[Prediction],
    quote_index: dict[str, HistoricalOddsQuote],
) -> MarketBacktest:
    """Evaluate closing prices as targets, never as model features.

    This is not a production Gate decision. It uses the frozen EV boundary only to
    describe research signals and uses one unit per signal for transparent ROI.
    """

    pnl: list[float] = []
    odds_matches = 0
    for prediction in predictions:
        quote = quote_index.get(prediction.fixture_id)
        if quote is None:
            continue
        odds_matches += 1
        probabilities = (prediction.home, prediction.draw, prediction.away)
        prices = (quote.home, quote.draw, quote.away)
        expected_values = tuple(
            probability * price - 1
            for probability, price in zip(probabilities, prices, strict=True)
        )
        selected = max(range(3), key=expected_values.__getitem__)
        if expected_values[selected] < 0.05:
            continue
        actual_index = ("home", "draw", "away").index(prediction.actual)
        pnl.append(prices[selected] - 1 if selected == actual_index else -1.0)
    if not pnl:
        return MarketBacktest(
            "NO_RESEARCH_SIGNALS",
            len(predictions),
            odds_matches,
            0,
            None,
            None,
            "Closing odds are evaluation targets; production Gate and formal BET were not run.",
        )
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in pnl:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return MarketBacktest(
        "RESEARCH_ONLY",
        len(predictions),
        odds_matches,
        len(pnl),
        sum(pnl) / len(pnl),
        max_drawdown,
        "Unit-stake closing-price evaluation only; not a production Gate or betting record.",
    )


def _preferred_closing_quotes(
    odds: Iterable[HistoricalOddsQuote],
) -> dict[str, HistoricalOddsQuote]:
    priority = {"market-average": 3, "Pinnacle": 2, "Bet365": 1}
    selected: dict[str, HistoricalOddsQuote] = {}
    for quote in odds:
        if quote.context != "CLOSING":
            continue
        current = selected.get(quote.fixture_id)
        if current is None or priority.get(quote.bookmaker, 0) > priority.get(current.bookmaker, 0):
            selected[quote.fixture_id] = quote
    return selected
