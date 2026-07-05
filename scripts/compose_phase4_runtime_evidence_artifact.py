"""Compose a Phase 4 runtime evidence artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


PHASE4_RUNTIME_CAPABILITIES = ["sorter_inbound", "smt_ng_wms_reconciliation"]
PHASE4_EFFECT_PATH = [
    "RuntimeIntentLog",
    "WmsFulfillmentPort.notify_pkg_binding",
    "WmsInventoryTransactionPort.confirm_inbound",
]
PHASE4_CALLBACK_PATH = ["RuntimeInbox"]
PHASE4_SERVICE_BEHAVIOR_INVARIANT = ["provider-contract"]
SITE_PRODUCTION_EVIDENCE_PROFILES = frozenset({"site", "production"})


def _phase4_evidence_manifest(evidence_dir: str) -> dict[str, object]:
    evidence_dir = evidence_dir.rstrip("/")
    return {
        "provider_contracts": {
            "sorter_inbound": {
                "kind": "provider-contract",
                "evidence": f"{evidence_dir}/provider-contracts/sorter-inbound.json",
            },
            "smt_ng_wms_reconciliation": {
                "kind": "provider-contract",
                "evidence": f"{evidence_dir}/provider-contracts/smt-ng-wms-reconciliation.json",
            },
        },
        "effect_dispatch_trace": {
            "kind": "runtime-trace",
            "evidence": f"{evidence_dir}/traces/effect-dispatch.json",
        },
        "callback_worker_trace": {
            "kind": "runtime-trace",
            "evidence": f"{evidence_dir}/traces/runtime-inbox-worker.json",
        },
        "runtime_hold_reconciliation_trace": {
            "kind": "runtime-trace",
            "evidence": f"{evidence_dir}/traces/runtime-hold-reconciliation.json",
        },
        "benchmark": {
            "kind": "phase4-runtime-benchmark",
            "evidence": f"{evidence_dir}/benchmarks/phase4-runtime.json",
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Path to write the Phase4 runtime evidence artifact JSON.")
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
            "Directory containing site/production Phase4 evidence files, relative to the output artifact directory "
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
    """Build the minimal Phase4 runtime evidence artifact accepted by the readiness gate."""

    artifact: dict[str, object] = {
        "profile": {
            "name": profile,
            "environment": environment,
            "generated_at": generated_at,
        },
        "capabilities": list(PHASE4_RUNTIME_CAPABILITIES),
        "effect_path": list(PHASE4_EFFECT_PATH),
        "callback_path": list(PHASE4_CALLBACK_PATH),
        "service_behavior_invariant": list(PHASE4_SERVICE_BEHAVIOR_INVARIANT),
    }
    if profile in SITE_PRODUCTION_EVIDENCE_PROFILES and evidence_dir:
        artifact["evidence_manifest"] = _phase4_evidence_manifest(evidence_dir)
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)
    if args.profile in SITE_PRODUCTION_EVIDENCE_PROFILES and not args.evidence_dir:
        print("Phase4 site/production evidence-dir is required")
        return 1
    artifact = compose_artifact(
        profile=args.profile,
        environment=args.environment,
        generated_at=args.generated_at,
        evidence_dir=args.evidence_dir,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Phase 4 runtime evidence artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
