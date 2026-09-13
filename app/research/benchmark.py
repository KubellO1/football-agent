"""Immutable verification and reproduction contract for zero-cost research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.research.football_data import FootballDataCsvAdapter
from app.research.information_timing import run_information_timing_experiment
from app.research.longitudinal import run_longitudinal_validation
from app.research.market_improvement import run_market_improvement_experiment

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_CONCLUSION = "ZERO_COST_BETTING_EDGE_NOT_DEMONSTRATED"
MANDATORY_MARKET_BASELINE = "DE_VIGGED_1X2_MARKET"


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    path: str
    reason: str
    expected: str | int | None
    actual: str | int | None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    valid: bool
    checked_artifacts: int
    issues: tuple[VerificationIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "checked_artifacts": self.checked_artifacts,
            "issues": [asdict(issue) for issue in self.issues],
        }


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and enforce the immutable benchmark methodology contract."""

    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest_contract(payload)
    return payload


def validate_manifest_contract(manifest: dict[str, Any]) -> None:
    """Reject any manifest that weakens the accepted research methodology."""

    methodology = manifest["methodology"]
    market = methodology["market_baseline"]
    closing = methodology["closing_odds"]
    production = manifest["production_constraints"]
    if manifest["conclusion"] != EXPECTED_CONCLUSION:
        raise ValueError("zero-cost closure conclusion changed")
    if manifest["production_candidate"] is not False:
        raise ValueError("closed benchmark cannot be a production candidate")
    if not market["mandatory"] or market["name"] != MANDATORY_MARKET_BASELINE:
        raise ValueError("de-vigged market baseline must remain mandatory")
    if not market["overround_removed"]:
        raise ValueError("market baseline must remove overround")
    if closing["allowed_as_earlier_decision_feature"]:
        raise ValueError("closing odds cannot be an earlier decision feature")
    if methodology["model_selection_uses_final_holdout"]:
        raise ValueError("final holdout cannot be used for model selection")
    if methodology["future_data_allowed"]:
        raise ValueError("future information is forbidden")
    if methodology["league_exclusion_allowed"]:
        raise ValueError("league cherry-picking is forbidden")
    if methodology["final_oos_threshold_optimization_allowed"]:
        raise ValueError("final OOS threshold optimization is forbidden")
    prohibited = (
        "production_deployment_pr_allowed",
        "production_database_writes_allowed",
        "scheduler_changes_allowed",
        "paid_api_allowed",
        "frozen_changes_allowed",
    )
    if production["production_model_unchanged"] is not True or any(
        production[key] is not False for key in prohibited
    ):
        raise ValueError("production mutation must remain prohibited")
    task_056 = manifest["accepted_benchmarks"]["TASK-20260913-056"]
    task_057 = manifest["accepted_benchmarks"]["TASK-20260913-057"]
    if task_056["market_brier"] != task_057["market_baseline_brier"]:
        raise ValueError("TASK-056 and TASK-057 must share the market baseline")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_artifacts(
    manifest: dict[str, Any], *, artifact_root: Path, repository_root: Path
) -> VerificationReport:
    """Verify research data, outputs and runner source without changing them."""

    issues: list[VerificationIssue] = []
    entries = (
        *((artifact_root, item) for item in manifest["artifacts"]),
        *((repository_root, item) for item in manifest["runner_files"]),
    )
    checked = 0
    for root, item in entries:
        checked += 1
        path = root / item["path"]
        if not path.is_file():
            issues.append(VerificationIssue(item["path"], "MISSING", item["sha256"], None))
            continue
        expected_size = item.get("bytes")
        actual_size = path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            issues.append(
                VerificationIssue(item["path"], "SIZE_MISMATCH", expected_size, actual_size)
            )
            continue
        actual_hash = sha256_file(path)
        if actual_hash != item["sha256"]:
            issues.append(
                VerificationIssue(item["path"], "HASH_MISMATCH", item["sha256"], actual_hash)
            )
    return VerificationReport(not issues, checked, tuple(issues))


def reopening_reason_allowed(manifest: dict[str, Any], reason: str) -> bool:
    """Return true only for an explicitly approved materially new input."""

    policy = manifest["reopening_policy"]
    return (
        reason in policy["allowed_material_inputs"] and reason not in policy["disallowed_reasons"]
    )


def reproduce_benchmark(
    manifest: dict[str, Any], *, artifact_root: Path, repository_root: Path, output_dir: Path
) -> dict[str, object]:
    """Re-run the accepted runners against verified local cache only."""

    verification = verify_artifacts(
        manifest, artifact_root=artifact_root, repository_root=repository_root
    )
    if not verification.valid:
        raise ValueError("artifact verification failed; reproduction aborted")

    dataset = FootballDataCsvAdapter(artifact_root / "TASK-20260912-055" / "cache").acquire(
        manifest["dataset"]["season_codes"]
    )
    longitudinal = run_longitudinal_validation(
        dataset.fixtures,
        dataset.odds,
        holdout_season=manifest["methodology"]["final_holdout_seasons"][0],
    )
    market = run_market_improvement_experiment(
        dataset.fixtures,
        dataset.odds,
        selection_season=manifest["methodology"]["selection_seasons"][0],
        holdout_season=manifest["methodology"]["final_holdout_seasons"][0],
    )
    timing = run_information_timing_experiment(
        dataset.fixtures,
        dataset.odds,
        selection_season=manifest["methodology"]["selection_seasons"][0],
        holdout_season=manifest["methodology"]["final_holdout_seasons"][0],
    )
    results = {"longitudinal": longitudinal, "market": market, "timing": timing}
    comparisons = compare_accepted_benchmarks(manifest, results)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in results.items():
        (output_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    summary: dict[str, object] = {
        "benchmark_id": manifest["benchmark_id"],
        "reproduced": all(comparisons.values()),
        "comparisons": comparisons,
        "conclusion": manifest["conclusion"],
    }
    (output_dir / "reproduction_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def compare_accepted_benchmarks(
    manifest: dict[str, Any], results: dict[str, dict[str, Any]], *, tolerance: float = 1e-12
) -> dict[str, bool]:
    """Compare the critical accepted outputs without re-selecting a result."""

    accepted = manifest["accepted_benchmarks"]
    longitudinal = results["longitudinal"]["no_xg_ablation"]["variants"]["20"]["variants"]["C"]
    task_055 = accepted["TASK-20260912-055"]
    task_056 = accepted["TASK-20260913-056"]
    task_057 = accepted["TASK-20260913-057"]
    required_056 = results["market"]["required_outputs"]
    required_057 = results["timing"]["required_outputs"]
    return {
        "TASK-20260912-055": all(
            (
                _close(
                    longitudinal["holdout_evaluation"]["brier"], task_055["oos_brier"], tolerance
                ),
                _close(
                    longitudinal["holdout_evaluation"]["log_loss"],
                    task_055["oos_log_loss"],
                    tolerance,
                ),
                _close(
                    longitudinal["holdout_market_backtest"]["unit_stake_roi"],
                    task_055["oos_roi"],
                    tolerance,
                ),
            )
        ),
        "TASK-20260913-056": all(
            _close(required_056[key], value, tolerance)
            for key, value in {
                "MARKET_BRIER": task_056["market_brier"],
                "MARKET_LOGLOSS": task_056["market_log_loss"],
                "MODEL_BRIER": task_056["model_brier"],
                "MODEL_LOGLOSS": task_056["model_log_loss"],
            }.items()
        ),
        "TASK-20260913-057": (
            required_057["BEST_ZERO_COST_SIGNAL"] == task_057["best_zero_cost_signal"]
            and required_057["EDGE_EVIDENCE"] == task_057["edge_evidence"]
            and all(
                _close(required_057[key], value, tolerance)
                for key, value in {
                    "MARKET_BASELINE_BRIER": task_057["market_baseline_brier"],
                    "BEST_MODEL_BRIER": task_057["best_model_brier"],
                    "INCREMENTAL_EDGE": task_057["incremental_edge"],
                    "MEAN_CLV": task_057["mean_clv"],
                    "OOS_ROI": task_057["oos_roi"],
                }.items()
            )
        ),
    }


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance
