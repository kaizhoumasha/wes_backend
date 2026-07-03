"""Phase 3 conveyor queue writer lightweight benchmark."""

from __future__ import annotations

from tests.load.phase3_benchmark_scenarios import run_conveyor_queue_writer_benchmark


def test_conveyor_queue_writer_benchmark() -> None:
    result = run_conveyor_queue_writer_benchmark()

    assert result.metrics["write_p95_ms"] < result.thresholds["write_p95_ms"]
    assert result.metrics["reconciling_count"] == 100
    assert result.metrics["integrity_conflict_recheck_count"] == 100
