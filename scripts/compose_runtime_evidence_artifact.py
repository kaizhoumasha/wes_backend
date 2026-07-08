"""Compose a runtime evidence artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


RUNTIME_EVIDENCE_CAPABILITIES = ["sorter_inbound", "smt_ng_wms_reconciliation"]
RUNTIME_EVIDENCE_EFFECT_PATH = [
    "RuntimeIntentLog",
    "WmsFulfillmentPort.notify_pkg_binding",
    "WmsInventoryTransactionPort.confirm_inbound",
]
RUNTIME_EVIDENCE_CALLBACK_PATH = ["RuntimeInbox"]
RUNTIME_EVIDENCE_SERVICE_BEHAVIOR_INVARIANT = ["provider-contract"]
SITE_PRODUCTION_EVIDENCE_PROFILES = frozenset({"site", "production"})


def _evidence_manifest_entry(*, evidence_dir: Path, relative_path: str, kind: str) -> dict[str, object]:
    evidence_path = (evidence_dir / relative_path).resolve()
    if not evidence_path.is_file():
        raise ValueError(f"MISSING_RUNTIME_EVIDENCE_FILE: {relative_path}")
    return {
        "kind": kind,
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    }


def _runtime_evidence_manifest(evidence_dir: str | Path) -> dict[str, object]:
    evidence_dir = Path(evidence_dir).resolve()
    return {
        "provider_contracts": {
            "sorter_inbound": _evidence_manifest_entry(
                evidence_dir=evidence_dir,
                relative_path="provider-contracts/sorter-inbound.json",
                kind="provider-contract",
            ),
            "smt_ng_wms_reconciliation": _evidence_manifest_entry(
                evidence_dir=evidence_dir,
                relative_path="provider-contracts/smt-ng-wms-reconciliation.json",
                kind="provider-contract",
            ),
        },
        "effect_dispatch_trace": _evidence_manifest_entry(
            evidence_dir=evidence_dir,
            relative_path="traces/effect-dispatch.json",
            kind="runtime-trace",
        ),
        "callback_worker_trace": _evidence_manifest_entry(
            evidence_dir=evidence_dir,
            relative_path="traces/runtime-inbox-worker.json",
            kind="runtime-trace",
        ),
        "runtime_hold_reconciliation_trace": _evidence_manifest_entry(
            evidence_dir=evidence_dir,
            relative_path="traces/runtime-hold-reconciliation.json",
            kind="runtime-trace",
        ),
        "benchmark": _evidence_manifest_entry(
            evidence_dir=evidence_dir,
            relative_path="benchmarks/runtime-evidence.json",
            kind="runtime-evidence-benchmark",
        ),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Path to write the Runtime evidence artifact JSON.")
    parser.add_argument(
        "--profile",
        required=True,
        choices=("simulator", "site", "production"),
        help="Evidence profile name. This changes evidence expectations only, not runtime behavior.",
    )
    parser.add_argument("--environment", required=True, help="Environment/provider label that produced the evidence.")
    parser.add_argument("--generated-at", required=True, help="ISO timestamp for the composed artifact.")
    parser.add_argument(
        "--evidence-dir",
        help=(
            "Directory containing site/production runtime evidence files, relative to the output artifact directory "
            "or absolute. Required for site and production profiles."
        ),
    )
    return parser.parse_args(argv)


def compose_artifact(
    *,
    profile: str,
    environment: str,
    generated_at: str,
    evidence_dir: str | None = None,
) -> dict[str, object]:
    """Build the minimal Runtime evidence artifact accepted by the readiness gate."""

    artifact: dict[str, object] = {
        "profile": {
            "name": profile,
            "environment": environment,
            "generated_at": generated_at,
        },
        "capabilities": list(RUNTIME_EVIDENCE_CAPABILITIES),
        "effect_path": list(RUNTIME_EVIDENCE_EFFECT_PATH),
        "callback_path": list(RUNTIME_EVIDENCE_CALLBACK_PATH),
        "service_behavior_invariant": list(RUNTIME_EVIDENCE_SERVICE_BEHAVIOR_INVARIANT),
    }
    if profile in SITE_PRODUCTION_EVIDENCE_PROFILES and not evidence_dir:
        raise ValueError("MISSING_RUNTIME_EVIDENCE_DIR")
    if profile in SITE_PRODUCTION_EVIDENCE_PROFILES:
        artifact["evidence_manifest"] = _runtime_evidence_manifest(evidence_dir)
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)
    if args.profile in SITE_PRODUCTION_EVIDENCE_PROFILES and not args.evidence_dir:
        print("Runtime evidence site/production evidence-dir is required")
        return 1
    evidence_dir = args.evidence_dir
    if evidence_dir:
        evidence_dir_path = Path(evidence_dir)
        if not evidence_dir_path.is_absolute():
            evidence_dir = str(output_path.parent / evidence_dir_path)
    try:
        artifact = compose_artifact(
            profile=args.profile,
            environment=args.environment,
            generated_at=args.generated_at,
            evidence_dir=evidence_dir,
        )
    except ValueError as exc:
        print(f"Runtime evidence artifact failed composition: {exc}")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Runtime evidence artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
