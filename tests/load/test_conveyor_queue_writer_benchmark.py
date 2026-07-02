"""Phase 3 conveyor queue writer lightweight benchmark."""

from __future__ import annotations

from time import perf_counter_ns

from src.app.runtime.orchestration.services.conveyor_queue_writer import (
    ConveyorQueueMembershipSnapshot,
    ConveyorQueueWriter,
    ConveyorQueueWriteRequest,
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


def test_conveyor_queue_writer_benchmark() -> None:
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

    assert write_p95_ms < 1.0
    assert reconciling_count == 100
