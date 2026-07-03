"""Validate the complete Phase 3 production closure evidence set."""

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
    parser.add_argument("--p0-e2e-artifact", required=True, help="Path to the production P0 E2E artifact JSON.")
    parser.add_argument(
        "--benchmark-artifact",
        required=True,
        help="Path to the production-scale runtime benchmark artifact JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

    args = parse_args(argv)
    validation = RuntimePhase3ClosureGate().validate_artifact_files(
        {
            "p0_e2e": Path(args.p0_e2e_artifact),
            "benchmark": Path(args.benchmark_artifact),
        }
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

    print("Phase 3 closure evidence passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
