"""RuntimeInbox claim 真实 PostgreSQL benchmark。"""

from __future__ import annotations

import os
from pathlib import Path

from tests.load.runtime_inbox_postgresql_benchmark import (
    CLAIM_P95_THRESHOLD_MS,
    THROUGHPUT_THRESHOLD_PER_SECOND,
    run_runtime_inbox_postgresql_benchmark,
)


def test_runtime_inbox_claim_benchmark_uses_ci_regression_budget() -> None:
    assert CLAIM_P95_THRESHOLD_MS == 300.0
    assert THROUGHPUT_THRESHOLD_PER_SECOND == 400.0


def test_runtime_inbox_claim_benchmark() -> None:
    evidence_path_value = os.getenv("RUNTIME_INBOX_BENCHMARK_EVIDENCE")
    evidence_path = Path(evidence_path_value) if evidence_path_value else None
    result = run_runtime_inbox_postgresql_benchmark(evidence_path)
    metrics = result["metrics"]
    thresholds = result["thresholds"]

    assert isinstance(metrics, dict)
    assert metrics["processed_count"] == 1_000
    assert metrics["duplicate_claim_count"] == 0
    assert result["sample_count"] == metrics["claim_sample_count"] == metrics["processed_count"] == 1_000
    assert metrics["claim_p50_ms"] <= metrics["claim_p95_ms"]
    assert metrics["claim_p95_ms"] <= thresholds["claim_p95_ms"]
    assert metrics["throughput_per_second"] >= thresholds["throughput_per_second"]
    assert metrics["lock_observation_count"] >= thresholds["lock_observation_count"] > 0
    assert metrics["waiting_lock_samples"] == thresholds["waiting_lock_samples"] == 0
    assert metrics["max_waiting_locks"] == thresholds["max_waiting_locks"] == 0
    assert result["query_plan"]["selective"]["gate_passed"] is True
    assert result["query_plan"]["selective"]["runtime_inbox_seq_scan_relations"] == []
    assert result["query_plan"]["production_statement_sha256"] == result["source"]["statement"]["sha256"]
    assert result["verdict"] == {"passed": True, "failed_gates": []}
    assert result["sli_before"] == {
        "status_counts": {
            "RECEIVED": 700,
            "PROCESSING": 100,
            "PROCESSED": 0,
            "FAILED": 200,
            "DEAD_LETTER": 0,
        },
        "oldest_claimable_age_ms": result["sli_before"]["oldest_claimable_age_ms"],
        "stale_processing_count": 100,
        "resource_wait_count": 0,
    }
    assert result["sli_before"]["oldest_claimable_age_ms"] >= 0
    assert result["sli_after"]["status_counts"]["PROCESSED"] == 1_000
