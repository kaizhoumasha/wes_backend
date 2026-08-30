#!/usr/bin/env python3
"""输出 Phase 10 legacy drain 双样本只读判定。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (  # noqa: E402
    LEGACY_DRAIN_PAIR_SCOPE,
    LegacyDrainPairScope,
)
from src.app.runtime.orchestration.services.query.legacy_drain_readiness_service import (  # noqa: E402
    LegacyDrainReadinessService,
)

DEFAULT_MANIFEST_PATH = BACKEND_ROOT / "docs/architecture/phase10-legacy-cutover-manifest.json"
EXIT_CODES = {"READY": 0, "BLOCK": 2, "WAIT_DRAIN": 3}


@dataclass(frozen=True)
class LegacyCutoverManifest:
    raw: dict[str, object]
    producers: tuple[dict[str, object], ...]
    excluded_target_producers: tuple[str, ...]
    paired_outbox_contract: LegacyDrainPairScope
    legacy_task_names: tuple[str, ...]
    legacy_beat_schedule: dict[str, str]
    broker_states: tuple[str, ...]
    required_worker_nodes: tuple[str, ...]
    shared_queues: tuple[str, ...]
    purge_shared_queues: bool
    stable_zero_observations: int
    candidate_ready_observations: int
    old_services: dict[str, object]
    candidate_readiness_command: str
    legacy_readiness_command: str
    immutable_digest_inputs: tuple[str, ...]
    maintenance_steps: tuple[dict[str, object], ...]
    interval_seconds: int


def _load_manifest(path: Path) -> LegacyCutoverManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("kind") != "phase10-legacy-cutover-manifest@v1":
        raise ValueError("invalid legacy cutover manifest")
    broker = raw.get("broker")
    observations = raw.get("observations")
    cutover = raw.get("cutover")
    disposition = raw.get("manual_disposition")
    if not all(isinstance(item, dict) for item in (broker, observations, cutover, disposition)):
        raise ValueError("invalid legacy cutover manifest")
    if broker.get("purge_shared_queues") is not False or disposition.get("automatic_actions") != []:
        raise ValueError("legacy cutover manifest must be non-destructive")

    producers = raw.get("producer_seal")
    excluded_target_producers = raw.get("excluded_target_producers")
    paired_outbox_contract = raw.get("paired_outbox_contract")
    task_names = broker.get("legacy_task_names")
    beat_schedule = broker.get("legacy_beat_schedule")
    broker_states = broker.get("inspect_states")
    required_worker_nodes = broker.get("required_worker_nodes")
    shared_queues = broker.get("shared_queues")
    digest_inputs = cutover.get("immutable_digest_inputs")
    maintenance_steps = cutover.get("maintenance_steps")
    old_services = cutover.get("old_services")
    if not isinstance(producers, list) or not all(isinstance(item, dict) for item in producers):
        raise ValueError("invalid producer seal manifest")
    if not isinstance(paired_outbox_contract, dict):
        raise TypeError("invalid paired outbox contract")
    if not all(
        isinstance(item, list)
        for item in (
            excluded_target_producers,
            task_names,
            broker_states,
            required_worker_nodes,
            shared_queues,
            digest_inputs,
            maintenance_steps,
        )
    ):
        raise ValueError("invalid legacy cutover manifest")
    if not isinstance(old_services, dict) or not isinstance(beat_schedule, dict):
        raise TypeError("invalid legacy cutover manifest")
    for items in (
        excluded_target_producers,
        task_names,
        broker_states,
        required_worker_nodes,
        shared_queues,
        digest_inputs,
    ):
        if not items or not all(isinstance(item, str) and item for item in items) or len(set(items)) != len(items):
            raise ValueError("invalid legacy cutover manifest")
    if not all(node.startswith("celery@") and len(node) > len("celery@") for node in required_worker_nodes):
        raise ValueError("invalid required worker node manifest")
    paired_operations = paired_outbox_contract.get("operation_identities")
    if (
        not isinstance(paired_outbox_contract.get("operation_domain"), str)
        or not isinstance(paired_outbox_contract.get("dispatch_type"), str)
        or not isinstance(paired_outbox_contract.get("producer"), str)
        or not isinstance(paired_operations, list)
        or not paired_operations
        or not all(isinstance(item, str) and item for item in paired_operations)
        or len(set(paired_operations)) != len(paired_operations)
    ):
        raise ValueError("invalid paired outbox contract")
    manifest_pair_scope = LegacyDrainPairScope(
        operation_domain=paired_outbox_contract["operation_domain"],
        dispatch_type=paired_outbox_contract["dispatch_type"],
        producer=paired_outbox_contract["producer"],
        operation_identities=tuple(paired_operations),
    )
    if manifest_pair_scope != LEGACY_DRAIN_PAIR_SCOPE:
        raise ValueError("paired outbox contract does not match repository scope")
    if not beat_schedule or not all(
        isinstance(key, str) and key and isinstance(value, str) and value in task_names
        for key, value in beat_schedule.items()
    ):
        raise ValueError("invalid legacy Beat schedule manifest")
    if not all(
        isinstance(step, dict) and step.get("on_failure") == "CUTOVER_FAILED_MAINTENANCE_HELD"
        for step in maintenance_steps
    ):
        raise ValueError("maintenance cutover must fail closed")

    interval_seconds = observations.get("interval_seconds")
    stable_zero = observations.get("legacy_stable_zero_observations")
    candidate_ready = observations.get("candidate_ready_observations")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (interval_seconds, stable_zero, candidate_ready)
    ):
        raise ValueError("invalid observation policy")
    candidate_command = cutover.get("candidate_readiness_command")
    legacy_command = cutover.get("legacy_readiness_command")
    if not isinstance(candidate_command, str) or not isinstance(legacy_command, str):
        raise TypeError("invalid readiness command")
    return LegacyCutoverManifest(
        raw=raw,
        producers=tuple(producers),
        excluded_target_producers=tuple(excluded_target_producers),
        paired_outbox_contract=LEGACY_DRAIN_PAIR_SCOPE,
        legacy_task_names=tuple(task_names),
        legacy_beat_schedule=beat_schedule,
        broker_states=tuple(broker_states),
        required_worker_nodes=tuple(required_worker_nodes),
        shared_queues=tuple(shared_queues),
        purge_shared_queues=False,
        stable_zero_observations=stable_zero,
        candidate_ready_observations=candidate_ready,
        old_services=old_services,
        candidate_readiness_command=candidate_command,
        legacy_readiness_command=legacy_command,
        immutable_digest_inputs=tuple(digest_inputs),
        maintenance_steps=tuple(maintenance_steps),
        interval_seconds=interval_seconds,
    )


def _canonical_payload(result: object) -> dict[str, object]:
    state = getattr(result, "state", None)
    counts = getattr(result, "counts", None)
    wait_drain_total = getattr(result, "wait_drain_total", None)
    block_total = getattr(result, "block_total", None)
    stable_zero = getattr(result, "stable_zero_observations", None)
    producer_freeze_at = getattr(result, "producer_freeze_at", None)
    generated_at = getattr(result, "generated_at", None)
    investigations = getattr(result, "manual_investigations", None)
    if state not in EXIT_CODES or not isinstance(counts, dict):
        raise ValueError("invalid legacy drain readiness result")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (*counts.values(), wait_drain_total, block_total, stable_zero)
    ):
        raise ValueError("invalid legacy drain readiness result")
    if (
        not isinstance(producer_freeze_at, str)
        or not isinstance(generated_at, str)
        or not isinstance(investigations, tuple)
    ):
        raise TypeError("invalid legacy drain readiness result")
    if not all(isinstance(item, dict) for item in investigations):
        raise ValueError("invalid legacy drain readiness result")
    return {
        "state": state,
        "counts": counts,
        "wait_drain_total": wait_drain_total,
        "block_total": block_total,
        "stable_zero_observations": stable_zero,
        "producer_freeze_at": producer_freeze_at,
        "generated_at": generated_at,
        "manual_investigations": list(investigations),
    }


async def run(
    *,
    producer_freeze_at: datetime,
    service: object | None = None,
    session_factory: Callable[[], Any] | None = None,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    owns_database = session_factory is None
    try:
        try:
            manifest = _load_manifest(manifest_path)
            if session_factory is None:
                from src.database import db as db_module

                await db_module.init_db()
                if db_module.AsyncSessionLocal is None:
                    raise RuntimeError("database unavailable")
                session_factory = db_module.AsyncSessionLocal
            readiness_service = service or LegacyDrainReadinessService()
            result = await readiness_service.check(
                session_factory=session_factory,
                producer_freeze_at=producer_freeze_at,
                interval_seconds=manifest.interval_seconds,
                legacy_task_names=manifest.legacy_task_names,
                required_worker_nodes=manifest.required_worker_nodes,
            )
            payload = _canonical_payload(result)
        finally:
            if owns_database:
                from src.database import db as db_module

                await db_module.close_db()
        stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return EXIT_CODES[str(payload["state"])]
    except Exception:
        stderr.write("LEGACY_DRAIN_READINESS_QUERY_FAILED\n")
        return 1


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("producer freeze timestamp must include timezone")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Phase 10 legacy drain readiness without changing state")
    parser.add_argument("--producer-freeze-at", required=True, type=_parse_timestamp)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(producer_freeze_at=args.producer_freeze_at, manifest_path=args.manifest)))


if __name__ == "__main__":
    main()
