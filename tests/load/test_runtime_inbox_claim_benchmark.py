"""Phase 3 RuntimeInbox claim lightweight benchmark."""

from __future__ import annotations

from collections import deque
from time import perf_counter_ns


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


def test_runtime_inbox_claim_benchmark() -> None:
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

    assert claim_p95_ms < 1.0
    assert duplicate_claim_count == 0
