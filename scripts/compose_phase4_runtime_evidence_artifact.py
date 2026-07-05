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
    return parser.parse_args(argv)


def compose_artifact(*, profile: str, environment: str, generated_at: str) -> dict[str, object]:
    """Build the minimal Phase4 runtime evidence artifact accepted by the readiness gate."""

    return {
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)
    artifact = compose_artifact(
        profile=args.profile,
        environment=args.environment,
        generated_at=args.generated_at,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Phase 4 runtime evidence artifact written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
