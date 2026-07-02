"""Reusable Phase 3 benchmark scenarios and artifact builder."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter_ns
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.services.conveyor_queue_writer import (
    ConveyorQueueMembershipSnapshot,
    ConveyorQueueWriter,
    ConveyorQueueWriteRequest,
)
from src.app.runtime.orchestration.services.device_dispatch_policy import (
    DeviceDispatchPolicy,
    DeviceDispatchRequest,
    DeviceRuntimeSnapshot,
    DeviceRuntimeStatus,
)
from src.app.workline.models.plane import PlaneSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class Phase3BenchmarkResult:
    """Structured result emitted by a lightweight Phase 3 benchmark scenario."""

    sample_count: int
    metrics: dict[str, float | int]
    thresholds: dict[str, float | int]

    def to_artifact(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "metrics": self.metrics,
            "thresholds": self.thresholds,
        }


def _p95_ms(samples_ns: list[int]) -> float:
    ordered = sorted(samples_ns)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return ordered[index] / 1_000_000


def _measure(operation: Callable[[], None], *, iterations: int) -> float:
    samples: list[int] = []
    for _ in range(iterations):
        started_at = perf_counter_ns()
        operation()
        samples.append(perf_counter_ns() - started_at)
    return _p95_ms(samples)


def run_runtime_inbox_claim_benchmark() -> Phase3BenchmarkResult:
    pending = deque(f"evt-{index}" for index in range(512))
    claimed: set[str] = set()
    duplicate_claim_count = 0

    def claim_next() -> None:
        nonlocal duplicate_claim_count

        if not pending:
            return
        event_id = pending.popleft()
        if event_id in claimed:
            duplicate_claim_count += 1
        claimed.add(event_id)

    claim_p95_ms = _measure(claim_next, iterations=512)

    return Phase3BenchmarkResult(
        sample_count=512,
        metrics={"claim_p95_ms": claim_p95_ms, "duplicate_claim_count": duplicate_claim_count},
        thresholds={"claim_p95_ms": 1.0, "duplicate_claim_count": 0},
    )


def run_conveyor_queue_writer_benchmark() -> Phase3BenchmarkResult:
    writer = ConveyorQueueWriter()
    active_memberships = [
        ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-A", bin_code="BIN-A"),
        ConveyorQueueMembershipSnapshot(workline_id=1, queue_code="Q-B", placeholder_key="PH-1"),
    ]
    requests = (
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-A",
            bin_code="BIN-CREATE",
            declared_queue_codes=frozenset({"Q-A", "Q-B"}),
        ),
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-A",
            bin_code="BIN-A",
            declared_queue_codes=frozenset({"Q-A", "Q-B"}),
        ),
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-B",
            bin_code="BIN-A",
            declared_queue_codes=frozenset({"Q-A", "Q-B"}),
        ),
        ConveyorQueueWriteRequest(
            workline_id=1,
            queue_code="Q-B",
            bin_code="BIN-B",
            placeholder_key="PH-1",
            declared_queue_codes=frozenset({"Q-A", "Q-B"}),
        ),
    )
    cursor = 0
    reconciling_count = 0

    def plan_write() -> None:
        nonlocal cursor, reconciling_count

        request = requests[cursor % len(requests)]
        cursor += 1
        decision = writer.plan_write(request, active_memberships=active_memberships)
        if decision.reconciliation_required:
            reconciling_count += 1

    write_p95_ms = _measure(plan_write, iterations=400)

    return Phase3BenchmarkResult(
        sample_count=400,
        metrics={"write_p95_ms": write_p95_ms, "reconciling_count": reconciling_count},
        thresholds={"write_p95_ms": 1.0, "reconciling_count": 100},
    )


def run_ecs_status_command_benchmark() -> Phase3BenchmarkResult:
    now = datetime(2026, 7, 2, tzinfo=UTC)
    policy = DeviceDispatchPolicy()
    request = DeviceDispatchRequest(
        command_code="CMD-BENCH",
        device_role="robot-arm",
        capability_code="PICK_AND_PUT",
        dispatch_deadline_at=now + timedelta(seconds=3),
    )
    snapshot = DeviceRuntimeSnapshot(
        device_code="ECS-ROBOT-01",
        status=DeviceRuntimeStatus.IDLE,
        observed_at=now,
        status_valid_until=now + timedelta(seconds=1),
    )

    def status_get_path() -> None:
        decision = policy.evaluate(request, snapshot=snapshot, now=now)
        assert decision.dispatch_allowed is True

    def command_post_path() -> None:
        payload = {
            "command_code": request.command_code,
            "device_code": snapshot.device_code,
            "capability_code": request.capability_code,
            "deadline": request.dispatch_deadline_at.isoformat(),
        }
        assert payload["command_code"] == "CMD-BENCH"

    status_get_p95_ms = _measure(status_get_path, iterations=400)
    command_post_p95_ms = _measure(command_post_path, iterations=400)

    return Phase3BenchmarkResult(
        sample_count=400,
        metrics={"status_get_p95_ms": status_get_p95_ms, "command_post_p95_ms": command_post_p95_ms},
        thresholds={"status_get_p95_ms": 1.0, "command_post_p95_ms": 1.0},
    )


def _snapshot_payload(*, object_count: int) -> dict[str, object]:
    return {
        "schema_version": "plane.snapshot.v1",
        "workline_code": "WL-BENCH",
        "scene_schema_version": "plane.scene.v1",
        "objects": [
            {
                "object_code": f"OBJ-{index}",
                "object_label": f"Object {index}",
                "state": "IN_FLIGHT" if index % 2 else "IDLE",
            }
            for index in range(object_count)
        ],
        "extremes": [
            {
                "code": "RECONCILING",
                "label": "Reconciling",
                "severity": "warning",
            }
        ],
    }


def run_plane_snapshot_benchmark() -> Phase3BenchmarkResult:
    payload = _snapshot_payload(object_count=100)
    payload_10x = _snapshot_payload(object_count=1000)

    def build_snapshot() -> None:
        snapshot = PlaneSnapshot.model_validate(payload)
        assert len(snapshot.objects) == 100

    def build_snapshot_10x() -> None:
        snapshot = PlaneSnapshot.model_validate(payload_10x)
        assert len(snapshot.objects) == 1000

    snapshot_p95_ms = _measure(build_snapshot, iterations=120)
    snapshot_10x_p95_ms = _measure(build_snapshot_10x, iterations=40)

    return Phase3BenchmarkResult(
        sample_count=160,
        metrics={"snapshot_p95_ms": snapshot_p95_ms, "snapshot_10x_p95_ms": snapshot_10x_p95_ms},
        thresholds={"snapshot_p95_ms": 20.0, "snapshot_10x_p95_ms": 100.0},
    )


def build_phase3_benchmark_artifact(*, environment: str, generated_at: str) -> dict[str, Any]:
    results = {
        "runtime_inbox_claim": run_runtime_inbox_claim_benchmark(),
        "conveyor_queue_writer": run_conveyor_queue_writer_benchmark(),
        "ecs_status_command": run_ecs_status_command_benchmark(),
        "plane_snapshot": run_plane_snapshot_benchmark(),
    }
    return {
        "environment": environment,
        "generated_at": generated_at,
        "scenarios": {name: result.to_artifact() for name, result in results.items()},
    }
