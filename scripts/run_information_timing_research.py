"""Run TASK-057 point-in-time information and market-timing research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.football_data import FootballDataCsvAdapter
from app.research.information_timing import run_information_timing_experiment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seasons", nargs="+", default=["2324", "2425", "2526"])
    args = parser.parse_args()

    dataset = FootballDataCsvAdapter(args.cache_dir).acquire(args.seasons)
    result = run_information_timing_experiment(dataset.fixtures, dataset.odds)
    result["dataset_summary"] = dataset.summary()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "information_timing_report.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({**result["required_outputs"], "decision": result["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
