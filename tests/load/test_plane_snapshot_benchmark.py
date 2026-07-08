"""Plane snapshot lightweight benchmark."""

from __future__ import annotations

from tests.load.runtime_benchmark_scenarios import run_plane_snapshot_benchmark


def test_plane_snapshot_benchmark() -> None:
    result = run_plane_snapshot_benchmark()

    assert result.metrics["snapshot_p95_ms"] < result.thresholds["snapshot_p95_ms"]
    assert result.metrics["snapshot_10x_p95_ms"] < result.thresholds["snapshot_10x_p95_ms"]
