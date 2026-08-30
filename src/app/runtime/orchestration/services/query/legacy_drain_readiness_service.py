"""Legacy drain 的双样本、只读、fail-closed 判定。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (
    LEGACY_DRAIN_COUNT_KEYS,
    LegacyDrainReadinessRepository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

WAIT_DRAIN_KEYS = (
    "runtime_inbox_processable",
    "runtime_inbox_lease",
    "runtime_intent_active",
    "system_outbox_active",
)
BLOCK_KEYS = tuple(key for key in LEGACY_DRAIN_COUNT_KEYS if key not in WAIT_DRAIN_KEYS)
BROKER_STATES = ("active", "reserved", "scheduled")
_ALLOWED_INVESTIGATION_KEYS = frozenset(
    {
        "kind",
        "table",
        "id",
        "status",
        "dispatch_key",
        "operation_identity",
        "idempotency_key",
        "intent_id",
        "outbox_id",
        "intent_operation_identity",
        "outbox_operation_identity",
        "intent_idempotency_key",
        "outbox_idempotency_key",
        "intent_digest",
        "outbox_digest",
        "source_event_id",
        "scope_key",
        "material_identity_key",
        "reservation_key",
        "task_name",
        "task_id",
        "worker",
    }
)


class LegacyDrainReadinessQueryError(RuntimeError):
    """数据库或 broker inspection 失败；调用方必须保持维护态。"""


@dataclass(frozen=True)
class LegacyBrokerTaskSnapshot:
    counts: dict[str, int]
    investigations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class LegacyDrainReadinessResult:
    state: str
    counts: dict[str, int]
    wait_drain_total: int
    block_total: int
    stable_zero_observations: int
    producer_freeze_at: str
    generated_at: str
    manual_investigations: tuple[dict[str, object], ...]


class CeleryLegacyTaskInspector:
    """只读取 Celery inspect snapshot；共享 queue 仅按精确 task identity 计数。"""

    def __init__(self, *, app: object | None = None, timeout_seconds: float = 5) -> None:
        self._app = app
        self._timeout_seconds = timeout_seconds

    async def inspect(
        self,
        *,
        legacy_task_names: tuple[str, ...],
        required_worker_nodes: tuple[str, ...],
    ) -> LegacyBrokerTaskSnapshot:
        return await asyncio.to_thread(self._inspect_sync, legacy_task_names, required_worker_nodes)

    def _inspect_sync(
        self,
        legacy_task_names: tuple[str, ...],
        required_worker_nodes: tuple[str, ...],
    ) -> LegacyBrokerTaskSnapshot:
        app = self._app
        if app is None:
            from src.celery_app.app import celery_app

            app = celery_app
        inspector = app.control.inspect(timeout=self._timeout_seconds)  # type: ignore[attr-defined]
        raw_by_state = {state: getattr(inspector, state)() for state in BROKER_STATES}
        if any(value is None for value in raw_by_state.values()):
            raise RuntimeError("Celery inspection did not return every worker snapshot")

        legacy = frozenset(legacy_task_names)
        counts = dict.fromkeys(BROKER_STATES, 0)
        investigations: list[dict[str, object]] = []
        for state in BROKER_STATES:
            workers = raw_by_state[state]
            if not isinstance(workers, dict):
                raise TypeError("invalid Celery inspection payload")
            missing_nodes = set(required_worker_nodes) - set(workers)
            if missing_nodes:
                raise RuntimeError(f"Celery inspection omitted required worker nodes for {state}")
            for worker, raw_tasks in sorted(workers.items()):
                if not isinstance(worker, str) or not isinstance(raw_tasks, list):
                    raise TypeError("invalid Celery inspection payload")
                for raw_task in raw_tasks:
                    if not isinstance(raw_task, dict):
                        raise TypeError("invalid Celery task inspection item")
                    task = raw_task.get("request") if state == "scheduled" else raw_task
                    if not isinstance(task, dict):
                        raise TypeError("invalid Celery scheduled task inspection item")
                    task_name = task.get("name")
                    task_id = task.get("id")
                    if task_name not in legacy:
                        continue
                    if not isinstance(task_id, str) or not task_id:
                        raise TypeError("legacy Celery task is missing original task id")
                    counts[state] += 1
                    investigations.append(
                        {
                            "kind": f"celery_{state}",
                            "task_name": task_name,
                            "task_id": task_id,
                            "worker": worker,
                        }
                    )
        return LegacyBrokerTaskSnapshot(counts=counts, investigations=tuple(investigations))


class LegacyDrainReadinessService:
    def __init__(
        self,
        *,
        repository: object | None = None,
        broker_inspector: object | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._repository = repository or LegacyDrainReadinessRepository()
        self._broker_inspector = broker_inspector or CeleryLegacyTaskInspector()
        self._sleep = sleep

    async def check(
        self,
        *,
        session_factory: Callable[[], Any],
        producer_freeze_at: datetime,
        interval_seconds: float,
        legacy_task_names: tuple[str, ...],
        required_worker_nodes: tuple[str, ...],
    ) -> LegacyDrainReadinessResult:
        if producer_freeze_at.tzinfo is None or producer_freeze_at.utcoffset() is None:
            raise ValueError("producer_freeze_at must be timezone-aware")
        if interval_seconds < 0:
            raise ValueError("interval_seconds must not be negative")
        if not legacy_task_names or len(set(legacy_task_names)) != len(legacy_task_names):
            raise ValueError("legacy_task_names must be non-empty and unique")
        if not required_worker_nodes or len(set(required_worker_nodes)) != len(required_worker_nodes):
            raise ValueError("required_worker_nodes must be non-empty and unique")
        if not all(node.startswith("celery@") and len(node) > len("celery@") for node in required_worker_nodes):
            raise ValueError("required_worker_nodes must contain exact Celery node names")

        try:
            observations: list[tuple[object, LegacyBrokerTaskSnapshot]] = []
            for index in range(2):
                async with session_factory() as db:
                    try:
                        database_snapshot = await self._repository.load_snapshot(
                            db,
                            producer_freeze_at=producer_freeze_at,
                        )
                    finally:
                        await db.rollback()
                broker_snapshot = await self._broker_inspector.inspect(
                    legacy_task_names=legacy_task_names,
                    required_worker_nodes=required_worker_nodes,
                )
                self._validate_database_snapshot(database_snapshot)
                self._validate_broker_snapshot(broker_snapshot)
                observations.append((database_snapshot, broker_snapshot))
                if index == 0:
                    await self._sleep(interval_seconds)
        except Exception as exc:
            raise LegacyDrainReadinessQueryError("legacy drain readiness query failed") from exc

        first_database, _first_broker = observations[0]
        second_database, second_broker = observations[1]
        counts = dict(second_database.counts)  # type: ignore[attr-defined]
        counts.update({f"celery_{state}": second_broker.counts[state] for state in BROKER_STATES})
        watermark_growth = first_database.watermarks != second_database.watermarks  # type: ignore[attr-defined]
        counts["legacy_row_watermark_growth_block"] = int(watermark_growth)

        stable_zero_observations = sum(self._is_zero(database, broker) for database, broker in observations)
        if watermark_growth:
            stable_zero_observations = 0
        current_wait_drain_total = sum(counts[key] for key in WAIT_DRAIN_KEYS) + sum(
            counts[f"celery_{state}"] for state in BROKER_STATES
        )
        counts["legacy_stability_observation_wait"] = int(
            current_wait_drain_total == 0 and stable_zero_observations < 2
        )
        wait_drain_total = current_wait_drain_total + counts["legacy_stability_observation_wait"]
        block_total = sum(counts[key] for key in BLOCK_KEYS) + counts["legacy_row_watermark_growth_block"]
        state = "BLOCK" if block_total else "WAIT_DRAIN" if wait_drain_total else "READY"
        investigations = self._deduplicate_investigations(
            (*second_database.investigations, *second_broker.investigations)  # type: ignore[attr-defined]
        )
        return LegacyDrainReadinessResult(
            state=state,
            counts=counts,
            wait_drain_total=wait_drain_total,
            block_total=block_total,
            stable_zero_observations=stable_zero_observations,
            producer_freeze_at=producer_freeze_at.isoformat(),
            generated_at=timezone.now_utc().isoformat(),
            manual_investigations=investigations,
        )

    @staticmethod
    def _validate_database_snapshot(snapshot: object) -> None:
        counts = getattr(snapshot, "counts", None)
        watermarks = getattr(snapshot, "watermarks", None)
        investigations = getattr(snapshot, "investigations", None)
        if not isinstance(counts, dict) or set(counts) != set(LEGACY_DRAIN_COUNT_KEYS):
            raise ValueError("unexpected legacy drain count shape")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ValueError("invalid legacy drain count")
        if not isinstance(watermarks, dict) or not watermarks:
            raise ValueError("invalid legacy drain watermarks")
        for table, watermark in watermarks.items():
            if (
                not isinstance(table, str)
                or not isinstance(watermark, tuple)
                or len(watermark) != 2
                or isinstance(watermark[0], bool)
                or not isinstance(watermark[0], int)
                or watermark[0] < 0
                or (watermark[1] is not None and (isinstance(watermark[1], bool) or not isinstance(watermark[1], int)))
            ):
                raise ValueError("invalid legacy drain watermark")
        LegacyDrainReadinessService._validate_investigations(investigations)

    @staticmethod
    def _validate_broker_snapshot(snapshot: object) -> None:
        counts = getattr(snapshot, "counts", None)
        if not isinstance(counts, dict) or set(counts) != set(BROKER_STATES):
            raise ValueError("invalid legacy broker count shape")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ValueError("invalid legacy broker count")
        LegacyDrainReadinessService._validate_investigations(getattr(snapshot, "investigations", None))

    @staticmethod
    def _validate_investigations(investigations: object) -> None:
        if not isinstance(investigations, tuple):
            raise TypeError("invalid manual investigation collection")
        for item in investigations:
            if not isinstance(item, dict):
                raise TypeError("invalid manual investigation item")
            if not set(item) <= _ALLOWED_INVESTIGATION_KEYS:
                raise ValueError("manual investigation contains non-identity fields")
            if not isinstance(item.get("kind"), str):
                raise TypeError("manual investigation is missing kind")

    @staticmethod
    def _deduplicate_investigations(
        investigations: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        LegacyDrainReadinessService._validate_investigations(investigations)
        unique: list[dict[str, object]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for item in investigations:
            identity = tuple(sorted((key, repr(value)) for key, value in item.items()))
            if identity not in seen:
                seen.add(identity)
                unique.append(item)
        return tuple(unique)

    @staticmethod
    def _is_zero(database_snapshot: object, broker_snapshot: LegacyBrokerTaskSnapshot) -> bool:
        return not any(database_snapshot.counts.values()) and not any(broker_snapshot.counts.values())  # type: ignore[attr-defined]


__all__ = [
    "CeleryLegacyTaskInspector",
    "LegacyBrokerTaskSnapshot",
    "LegacyDrainReadinessQueryError",
    "LegacyDrainReadinessResult",
    "LegacyDrainReadinessService",
]
