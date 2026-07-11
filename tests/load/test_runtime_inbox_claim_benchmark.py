"""RuntimeInbox claim 真实 PostgreSQL benchmark。"""

from __future__ import annotations

import os
from pathlib import Path

from tests.load.runtime_inbox_postgresql_benchmark import run_runtime_inbox_postgresql_benchmark


def test_runtime_inbox_claim_benchmark() -> None:
    evidence_path_value = os.getenv("RUNTIME_INBOX_BENCHMARK_EVIDENCE")
    evidence_path = Path(evidence_path_value) if evidence_path_value else None
    result = run_runtime_inbox_postgresql_benchmark(evidence_path)
    metrics = result["metrics"]
    thresholds = result["thresholds"]

    assert isinstance(metrics, dict)
    assert metrics["processed_count"] == 1_000
    assert metrics["duplicate_claim_count"] == 0
    assert 0 < metrics["claim_sample_count"] < 1_000
    assert metrics["claim_p50_ms"] <= metrics["claim_p95_ms"]
    assert metrics["claim_p95_ms"] <= thresholds["claim_p95_ms"]
    assert metrics["throughput_per_second"] >= thresholds["throughput_per_second"]
    assert result["query_plan"]
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
