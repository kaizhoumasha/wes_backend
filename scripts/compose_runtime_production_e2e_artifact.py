"""Compose a runtime production P0 E2E artifact."""

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
    parser.add_argument("--output", required=True, help="Path to write the composed P0 E2E artifact JSON.")
    parser.add_argument("--environment", required=True, help="Production or pre-production environment label.")
    parser.add_argument("--dependency-profile", required=True, help="Real dependency profile label, e.g. wms-ecs-http.")
    parser.add_argument(
        "--trace-recording", required=True, help="Sanitized trace recording JSON produced from TraceQueryResult."
    )
    parser.add_argument("--p95-seconds", required=True, type=float, help="Observed end-to-end P95 latency in seconds.")
    parser.add_argument(
        "--exception-evidence",
        action="append",
        default=[],
        metavar="PATH_NAME=FILE",
        help="Exception evidence JSON. Repeat for callback_out_of_order, ecs_timeout and wms_reject.",
    )
    return parser.parse_args(argv)


def _parse_exception_evidence(raw_values: list[str]) -> dict[str, Path]:
    exception_evidence: dict[str, Path] = {}
    for raw_value in raw_values:
        if "=" not in raw_value:
            raise ValueError(f"exception evidence must use PATH_NAME=FILE: {raw_value}")
        path_name, raw_path = raw_value.split("=", 1)
        if not path_name.strip() or not raw_path.strip():
            raise ValueError(f"exception evidence must use PATH_NAME=FILE: {raw_value}")
        path_name = path_name.strip()
        if path_name in exception_evidence:
            raise ValueError(f"DUPLICATE_EXCEPTION_EVIDENCE: {path_name}")
        exception_evidence[path_name] = Path(raw_path)
    return exception_evidence


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_repo_root_on_path()

    from src.app.runtime.orchestration.p0_e2e_artifact_composer import (
        RuntimeP0E2EArtifactComposer,
        RuntimeP0E2EArtifactCompositionError,
    )

    args = parse_args(argv)
    output_path = Path(args.output)
    try:
        artifact = RuntimeP0E2EArtifactComposer().compose_production_e2e(
            environment=args.environment,
            dependency_profile=args.dependency_profile,
            trace_recording_path=args.trace_recording,
            p95_seconds=args.p95_seconds,
            exception_evidence_paths=_parse_exception_evidence(args.exception_evidence),
        )
    except (RuntimeP0E2EArtifactCompositionError, ValueError) as exc:
        print(f"Runtime production E2E artifact failed composition: {exc}")
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Runtime production E2E artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
