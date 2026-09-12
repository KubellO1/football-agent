"""Run the isolated MARVIS_ZERO_COST_V1 research experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.zero_cost import ZeroCostExperimentRunner, load_research_fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = ZeroCostExperimentRunner().run(load_research_fixtures(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
