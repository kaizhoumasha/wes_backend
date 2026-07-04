"""Validate the Phase 3 closure evidence set for the current project profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _ensure_repo_root_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--closure-profile",
        choices=("auto", "mock", "development-mock", "test-mock", "production"),
        default="auto",
        help="Closure profile. auto uses production when both artifacts are supplied, otherwise mock.",
    )
    parser.add_argument("--p0-e2e-artifact", help="Path to the production P0 E2E artifact JSON.")
    parser.add_argument(
        "--benchmark-artifact",
        help="Path to the production-scale runtime benchmark artifact JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    args = parse_args(argv)
    artifact_paths = {}
    if args.p0_e2e_artifact:
        artifact_paths["p0_e2e"] = Path(args.p0_e2e_artifact)
    if args.benchmark_artifact:
        artifact_paths["benchmark"] = Path(args.benchmark_artifact)

    closure_profile = args.closure_profile
    if closure_profile == "auto":
        closure_profile = "production" if set(artifact_paths) == {"p0_e2e", "benchmark"} else "mock"

    if closure_profile == "production" and set(artifact_paths) != {"p0_e2e", "benchmark"}:
        print("Phase 3 closure evidence failed validation: MISSING_PHASE3_CLOSURE_ARTIFACTS")
        missing = sorted({"p0_e2e", "benchmark"} - set(artifact_paths))
        if missing:
            print(f"missing_artifacts={','.join(missing)}")
        return 2

    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        artifact_paths,
        closure_profile=closure_profile,
    )
    if not validation.valid:
        print(f"Phase 3 closure evidence failed validation: {validation.reason}")
        if validation.missing_artifacts:
            print(f"missing_artifacts={','.join(validation.missing_artifacts)}")
        if validation.invalid_artifacts:
            print(f"invalid_artifacts={','.join(validation.invalid_artifacts)}")
        if validation.missing_evidence_files:
            print(f"missing_evidence_files={','.join(validation.missing_evidence_files)}")
        if validation.mismatched_evidence_files:
            print(f"mismatched_evidence_files={','.join(validation.mismatched_evidence_files)}")
        return 1

    if validation.reason == "MOCK_PHASE3_CLOSURE":
        print(f"Phase 3 closure mock evidence passed: closure_profile={closure_profile}")
        return 0

    print("Phase 3 closure evidence passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
