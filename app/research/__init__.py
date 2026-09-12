"""Research-only utilities that are intentionally disconnected from production wiring."""

from app.research.football_data import (
    FootballDataCsvAdapter,
    HistoricalOddsQuote,
    LongitudinalDataset,
)
from app.research.longitudinal import run_longitudinal_validation
from app.research.zero_cost import (
    ExperimentReport,
    HistoricalOddsObservation,
    ResearchFixture,
    TeamMatchMetrics,
    ZeroCostExperimentRunner,
    load_research_fixtures,
)

__all__ = [
    "FootballDataCsvAdapter",
    "HistoricalOddsQuote",
    "LongitudinalDataset",
    "ExperimentReport",
    "HistoricalOddsObservation",
    "ResearchFixture",
    "TeamMatchMetrics",
    "ZeroCostExperimentRunner",
    "load_research_fixtures",
    "run_longitudinal_validation",
]
