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

    assert isinstance(metrics, dict)
    assert metrics["processed_count"] == 1_000
    assert metrics["duplicate_claim_count"] == 0
    assert metrics["claim_p50_ms"] <= metrics["claim_p95_ms"]
    assert metrics["throughput_per_second"] > 0
    assert result["query_plan"]
