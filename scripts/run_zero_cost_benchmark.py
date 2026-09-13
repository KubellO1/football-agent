"""Verify or reproduce the closed zero-cost research benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.benchmark import load_manifest, reproduce_benchmark, verify_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("research/benchmarks/zero_cost_v1/manifest.json"),
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("verify", "reproduce"), default="verify")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    if args.mode == "verify":
        report = verify_artifacts(
            manifest,
            artifact_root=args.artifact_root,
            repository_root=args.repository_root,
        ).to_dict()
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["valid"] else 1

    if args.output_dir is None:
        parser.error("--output-dir is required in reproduce mode")
    result = reproduce_benchmark(
        manifest,
        artifact_root=args.artifact_root,
        repository_root=args.repository_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
