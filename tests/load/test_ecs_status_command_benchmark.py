"""Phase 3 ECS status + command dispatch lightweight benchmark."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter_ns

from src.app.runtime.orchestration.services.device_dispatch_policy import (
    DeviceDispatchPolicy,
    DeviceDispatchRequest,
    DeviceRuntimeSnapshot,
    DeviceRuntimeStatus,
)


def _p95_ms(samples_ns: list[int]) -> float:
    ordered = sorted(samples_ns)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return ordered[index] / 1_000_000


def _measure(operation, *, iterations: int) -> float:
    samples: list[int] = []
    for _ in range(iterations):
        started_at = perf_counter_ns()
        operation()
        samples.append(perf_counter_ns() - started_at)
    return _p95_ms(samples)


def test_ecs_status_command_benchmark() -> None:
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

    assert status_get_p95_ms < 1.0
    assert command_post_p95_ms < 1.0
