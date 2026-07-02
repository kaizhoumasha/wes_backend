"""Validate a Phase 3 production P0 E2E artifact."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument("artifact", help="Path to the Phase 3 production P0 E2E artifact JSON.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.p0_e2e_gate import RuntimeP0E2EGate

    args = parse_args(argv)
    artifact_path = Path(args.artifact)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    validation = RuntimeP0E2EGate().validate_artifact(artifact)
    if not validation.valid:
        print(f"Phase 3 P0 E2E artifact failed validation: {validation.reason}")
        return 1

    print(f"Phase 3 P0 E2E artifact passed: {artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
