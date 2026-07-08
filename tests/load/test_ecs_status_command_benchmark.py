"""ECS status + command dispatch lightweight benchmark."""

from __future__ import annotations

from tests.load.runtime_benchmark_scenarios import run_ecs_status_command_benchmark


def test_ecs_status_command_benchmark() -> None:
    result = run_ecs_status_command_benchmark()

    assert result.metrics["status_get_p95_ms"] < result.thresholds["status_get_p95_ms"]
    assert result.metrics["command_post_p95_ms"] < result.thresholds["command_post_p95_ms"]
