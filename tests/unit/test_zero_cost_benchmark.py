from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.research.benchmark import (
    EXPECTED_CONCLUSION,
    compare_accepted_benchmarks,
    load_manifest,
    reopening_reason_allowed,
    validate_manifest_contract,
    verify_artifacts,
)

MANIFEST = Path("research/benchmarks/zero_cost_v1/manifest.json")


def test_repository_manifest_enforces_closed_research_contract() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest["conclusion"] == EXPECTED_CONCLUSION
    assert manifest["production_candidate"] is False
    assert manifest["methodology"]["market_baseline"]["mandatory"] is True
    assert manifest["methodology"]["closing_odds"]["allowed_as_earlier_decision_feature"] is False


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("conclusion",), "EDGE_FOUND", "conclusion changed"),
        (("production_candidate",), True, "production candidate"),
        (("methodology", "market_baseline", "mandatory"), False, "must remain mandatory"),
        (
            ("methodology", "closing_odds", "allowed_as_earlier_decision_feature"),
            True,
            "cannot be an earlier decision feature",
        ),
        (("methodology", "model_selection_uses_final_holdout"), True, "final holdout"),
        (("methodology", "league_exclusion_allowed"), True, "cherry-picking"),
    ],
)
def test_contract_rejects_methodology_weakening(
    path: tuple[str, ...], value: object, message: str
) -> None:
    manifest = load_manifest(MANIFEST)
    changed = deepcopy(manifest)
    target: dict[str, Any] = changed
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        validate_manifest_contract(changed)


def test_reopening_requires_materially_new_information() -> None:
    manifest = load_manifest(MANIFEST)

    assert reopening_reason_allowed(manifest, "TIMESTAMPED_MARKET_MOVEMENT") is True
    assert reopening_reason_allowed(manifest, "LEGAL_HISTORICAL_LINEUPS") is True
    assert reopening_reason_allowed(manifest, "ANOTHER_ALGORITHM") is False
    assert reopening_reason_allowed(manifest, "THRESHOLD_CHANGE") is False
    assert reopening_reason_allowed(manifest, "LEAGUE_FILTERING") is False
    assert reopening_reason_allowed(manifest, "HISTORICAL_ROI_OPTIMIZATION") is False


def test_artifact_verification_detects_hash_drift(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    repository_root = tmp_path / "repository"
    artifact = artifact_root / "data.csv"
    runner = repository_root / "runner.py"
    artifact.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True)
    artifact.write_bytes(b"fixture")
    runner.write_bytes(b"runner")
    manifest = {
        "artifacts": [
            {
                "path": "data.csv",
                "bytes": len(b"fixture"),
                "sha256": hashlib.sha256(b"fixture").hexdigest(),
            }
        ],
        "runner_files": [{"path": "runner.py", "sha256": hashlib.sha256(b"runner").hexdigest()}],
    }

    first = verify_artifacts(manifest, artifact_root=artifact_root, repository_root=repository_root)
    artifact.write_bytes(b"changed")
    second = verify_artifacts(
        manifest, artifact_root=artifact_root, repository_root=repository_root
    )

    assert first.valid is True
    assert first.checked_artifacts == 2
    assert second.valid is False
    assert second.issues[0].reason == "HASH_MISMATCH"


def test_accepted_benchmark_comparison_is_exact() -> None:
    manifest = load_manifest(MANIFEST)
    results = _accepted_results(manifest["accepted_benchmarks"])

    assert compare_accepted_benchmarks(manifest, results) == {
        "TASK-20260912-055": True,
        "TASK-20260913-056": True,
        "TASK-20260913-057": True,
    }

    results["market"]["required_outputs"]["MODEL_BRIER"] += 0.0001
    assert compare_accepted_benchmarks(manifest, results)["TASK-20260913-056"] is False


def test_manifest_is_valid_json() -> None:
    assert json.loads(MANIFEST.read_text(encoding="utf-8"))["schema_version"] == 1


def _accepted_results(accepted: dict[str, Any]) -> dict[str, dict[str, Any]]:
    task_055 = accepted["TASK-20260912-055"]
    task_056 = accepted["TASK-20260913-056"]
    task_057 = accepted["TASK-20260913-057"]
    return {
        "longitudinal": {
            "no_xg_ablation": {
                "variants": {
                    "20": {
                        "variants": {
                            "C": {
                                "holdout_evaluation": {
                                    "brier": task_055["oos_brier"],
                                    "log_loss": task_055["oos_log_loss"],
                                },
                                "holdout_market_backtest": {"unit_stake_roi": task_055["oos_roi"]},
                            }
                        }
                    }
                }
            }
        },
        "market": {
            "required_outputs": {
                "MARKET_BRIER": task_056["market_brier"],
                "MARKET_LOGLOSS": task_056["market_log_loss"],
                "MODEL_BRIER": task_056["model_brier"],
                "MODEL_LOGLOSS": task_056["model_log_loss"],
            }
        },
        "timing": {
            "required_outputs": {
                "BEST_ZERO_COST_SIGNAL": task_057["best_zero_cost_signal"],
                "MARKET_BASELINE_BRIER": task_057["market_baseline_brier"],
                "BEST_MODEL_BRIER": task_057["best_model_brier"],
                "INCREMENTAL_EDGE": task_057["incremental_edge"],
                "MEAN_CLV": task_057["mean_clv"],
                "OOS_ROI": task_057["oos_roi"],
                "EDGE_EVIDENCE": task_057["edge_evidence"],
            }
        },
    }
