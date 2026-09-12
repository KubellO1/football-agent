"""Run the TASK-056 zero-cost market and model comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.football_data import FootballDataCsvAdapter
from app.research.market_improvement import run_market_improvement_experiment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seasons", nargs="+", default=["2324", "2425", "2526"])
    parser.add_argument("--selection-season", default="2024/2025")
    parser.add_argument("--holdout-season", default="2025/2026")
    args = parser.parse_args()

    dataset = FootballDataCsvAdapter(args.cache_dir).acquire(args.seasons)
    result = run_market_improvement_experiment(
        dataset.fixtures,
        dataset.odds,
        selection_season=args.selection_season,
        holdout_season=args.holdout_season,
    )
    result["dataset_summary"] = dataset.summary()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "market_improvement_report.json"
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "decision": result["decision"],
                "selected_model": result["selected_model"],
                **result["required_outputs"],
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
