"""Compose a production-scale runtime benchmark artifact."""

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
    parser.add_argument("--output", required=True, help="Path to write the composed benchmark artifact JSON.")
    parser.add_argument("--environment", required=True, help="Production or pre-production environment label.")
    parser.add_argument("--generated-at", required=True, help="ISO timestamp for the composed artifact.")
    parser.add_argument(
        "--dependency-profile", required=True, help="Real dependency profile label, e.g. postgresql-wms-ecs-http."
    )
    parser.add_argument(
        "--concurrency-level", required=True, type=int, help="Observed production benchmark concurrency level."
    )
    parser.add_argument("--duration-seconds", required=True, type=int, help="Observed production benchmark duration.")
    parser.add_argument(
        "--scenario-evidence",
        action="append",
        default=[],
        metavar="SCENARIO=PATH",
        help="Per-scenario evidence JSON. Repeat for all required runtime benchmark scenarios.",
    )
    return parser.parse_args(argv)


def _parse_scenario_evidence(raw_values: list[str]) -> dict[str, Path]:
    scenario_evidence: dict[str, Path] = {}
    for raw_value in raw_values:
        if "=" not in raw_value:
            raise ValueError(f"scenario evidence must use SCENARIO=PATH: {raw_value}")
        scenario_name, raw_path = raw_value.split("=", 1)
        if not scenario_name.strip() or not raw_path.strip():
            raise ValueError(f"scenario evidence must use SCENARIO=PATH: {raw_value}")
        scenario_name = scenario_name.strip()
        if scenario_name in scenario_evidence:
            raise ValueError(f"DUPLICATE_SCENARIO_EVIDENCE: {scenario_name}")
        scenario_evidence[scenario_name] = Path(raw_path)
    return scenario_evidence


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.benchmark_artifact_composer import (
        RuntimeBenchmarkArtifactComposer,
        RuntimeBenchmarkArtifactCompositionError,
    )

    args = parse_args(argv)
    output_path = Path(args.output)
    try:
        artifact = RuntimeBenchmarkArtifactComposer().compose_production_scale(
            environment=args.environment,
            generated_at=args.generated_at,
            dependency_profile=args.dependency_profile,
            concurrency_level=args.concurrency_level,
            duration_seconds=args.duration_seconds,
            scenario_evidence_paths=_parse_scenario_evidence(args.scenario_evidence),
        )
    except (RuntimeBenchmarkArtifactCompositionError, ValueError) as exc:
        print(f"Runtime production benchmark artifact failed composition: {exc}")
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Runtime production benchmark artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
