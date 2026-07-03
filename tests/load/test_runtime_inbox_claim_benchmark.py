"""Phase 3 RuntimeInbox claim lightweight benchmark."""

from __future__ import annotations

from tests.load.phase3_benchmark_scenarios import run_runtime_inbox_claim_benchmark


def test_runtime_inbox_claim_benchmark() -> None:
    result = run_runtime_inbox_claim_benchmark()

    assert result.metrics["claim_p95_ms"] < result.thresholds["claim_p95_ms"]
    assert result.metrics["duplicate_claim_count"] == 0
