"""Acquire free longitudinal CSV data and run the unchanged zero-cost baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.football_data import FootballDataCsvAdapter
from app.research.longitudinal import run_longitudinal_validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seasons", nargs="+", default=["2324", "2425", "2526"])
    parser.add_argument("--holdout-season", default="2025/2026")
    args = parser.parse_args()

    adapter = FootballDataCsvAdapter(args.cache_dir)
    dataset = adapter.acquire(args.seasons)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset.write_normalized(args.output_dir / "normalized_fixtures.json")
    dataset.write_provenance(args.output_dir / "source_provenance.json")
    result = run_longitudinal_validation(
        dataset.fixtures,
        dataset.odds,
        holdout_season=args.holdout_season,
    )
    result["dataset_summary"] = dataset.summary()
    (args.output_dir / "experiment_report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "fixtures": result["fixture_count"],
                "odds_matches": result["odds_match_count"],
                "best_holdout_model": result["best_holdout_model"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
