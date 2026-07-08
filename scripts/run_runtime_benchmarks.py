"""Run Phase 3 lightweight runtime benchmarks and emit a gate-valid artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _ensure_repo_root_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _default_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="reports/benchmarks/runtime-benchmark.json",
        help="Path to write the benchmark artifact JSON.",
    )
    parser.add_argument(
        "--environment",
        default="local-lightweight",
        help="Environment label stored in the artifact.",
    )
    parser.add_argument(
        "--generated-at",
        default=None,
        help="ISO timestamp for deterministic test runs. Defaults to current UTC time.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.benchmark_gate import RuntimeBenchmarkGate
    from tests.load.runtime_benchmark_scenarios import build_runtime_benchmark_artifact

    args = parse_args(argv)
    output_path = Path(args.output)
    artifact = build_runtime_benchmark_artifact(
        environment=args.environment,
        generated_at=args.generated_at or _default_generated_at(),
    )
    validation = RuntimeBenchmarkGate().validate_artifact(artifact)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not validation.valid:
        print(f"Phase 3 benchmark artifact failed validation: {validation.reason}")
        return 1

    print(f"Phase 3 benchmark artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
