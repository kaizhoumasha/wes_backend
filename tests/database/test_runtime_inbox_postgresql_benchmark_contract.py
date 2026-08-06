"""RuntimeInbox PostgreSQL benchmark 的计划与证据合同。"""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from sqlalchemy.dialects import postgresql

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from tests.load.runtime_inbox_postgresql_benchmark import (
    BENCHMARK_EVIDENCE_SCHEMA_VERSION,
    CLAIM_P95_THRESHOLD_MS,
    PRODUCTION_CLAIM_BUILDER,
    PRODUCTION_CLAIM_STATEMENT_KIND,
    THROUGHPUT_THRESHOLD_PER_SECOND,
    _clear_selective_plan_fixture,
    _validate_selective_query_plan,
    validate_runtime_inbox_benchmark_evidence,
)

_CANONICAL_FINGERPRINT_VERSION = "runtime-inbox-claim-statement/v1"
_CANONICAL_FINGERPRINT_INPUTS = {
    "limit": 25,
    "now_ms": 2_000_000_000_000,
    "processor_token": "runtime-inbox-benchmark-fingerprint",
    "stale_after_seconds": 60,
}


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _production_statement_sha256() -> str:
    statement = RuntimeInboxRepository().build_claim_received_statement(**_CANONICAL_FINGERPRINT_INPUTS)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _valid_evidence() -> dict[str, object]:
    commit_sha = "a" * 40
    statement_sha = _production_statement_sha256()
    return {
        "schema_version": BENCHMARK_EVIDENCE_SCHEMA_VERSION,
        "generated_at": "2026-07-13T01:02:03Z",
        "repository": {"commit_sha": commit_sha, "dirty": False},
        "source": {
            "kind": "postgresql",
            "statement": {
                "kind": PRODUCTION_CLAIM_STATEMENT_KIND,
                "builder": PRODUCTION_CLAIM_BUILDER,
                "fingerprint_version": _CANONICAL_FINGERPRINT_VERSION,
                "canonical_inputs": dict(_CANONICAL_FINGERPRINT_INPUTS),
                "sha256": statement_sha,
            },
        },
        "database": {
            "server_version": "PostgreSQL 17.5",
            "settings": {"max_connections": "100", "shared_buffers": "128MB"},
        },
        "config": {
            "pending_inbox_count": 1000,
            "worker_concurrency": 4,
            "claim_batch_size": 25,
            "selective_fixture_row_count": 10000,
        },
        "workload": {"mix": {"received": 700, "failed_due": 200, "stale_processing": 100}},
        "sample_count": 1000,
        "metrics": {
            "claim_p50_ms": 80.0,
            "claim_p95_ms": CLAIM_P95_THRESHOLD_MS - 1,
            "claim_sample_count": 1000,
            "throughput_per_second": 500.0,
            "elapsed_seconds": 2.0,
            "duplicate_claim_count": 0,
            "lock_observation_count": 12,
            "waiting_lock_samples": 0,
            "max_waiting_locks": 0,
            "processed_count": 1000,
        },
        "thresholds": {
            "claim_p95_ms": CLAIM_P95_THRESHOLD_MS,
            "throughput_per_second": THROUGHPUT_THRESHOLD_PER_SECOND,
            "duplicate_claim_count": 0,
            "lock_observation_count": 1,
            "waiting_lock_samples": 0,
            "max_waiting_locks": 0,
        },
        "query_plan": {
            "production_statement_sha256": statement_sha,
            "selective": {
                "node_types": ["LockRows", "Index Scan"],
                "index_names": ["ix_wes_runtime_runtime_inbox_status_received"],
                "runtime_inbox_seq_scan_relations": [],
                "statement_sha256": statement_sha,
                "gate_passed": True,
            },
        },
        "verdict": {"passed": True, "failed_gates": []},
    }


def test_selective_query_plan_requires_runtime_inbox_index_without_critical_seq_scan() -> None:
    accepted = {
        "node_types": ["LockRows", "Index Scan"],
        "index_names": ["ix_wes_runtime_runtime_inbox_status_received"],
        "runtime_inbox_seq_scan_relations": [],
    }
    _validate_selective_query_plan(accepted)

    with pytest.raises(AssertionError, match="runtime_inbox_seq_scan"):
        _validate_selective_query_plan(accepted | {"runtime_inbox_seq_scan_relations": ["claim_candidate"]})
    with pytest.raises(AssertionError, match="runtime_inbox_claim_index"):
        _validate_selective_query_plan(accepted | {"index_names": []})


@pytest.mark.asyncio
async def test_selective_fixture_cleanup_deletes_only_owned_rows_without_truncate_or_cascade() -> None:
    connection = _RecordingConnection()

    await _clear_selective_plan_fixture(connection)  # type: ignore[arg-type]

    assert len(connection.statements) == 1
    statement = connection.statements[0].upper()
    assert statement.startswith("DELETE FROM WES_RUNTIME.RUNTIME_INBOX")
    assert "PROVIDER_CODE = 'BENCHMARK-PLAN'" in statement
    assert "TRUNCATE" not in statement
    assert "CASCADE" not in statement


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda evidence: evidence.pop("database"), "MISSING_FIELD"),
        (lambda evidence: evidence["repository"].update(commit_sha="c" * 40), "COMMIT_MISMATCH"),
        (lambda evidence: evidence["repository"].update(dirty=True), "DIRTY_WORKTREE"),
        (lambda evidence: evidence["verdict"].update(passed=False), "FAILED_VERDICT"),
        (
            lambda evidence: evidence["metrics"].update(claim_p95_ms=CLAIM_P95_THRESHOLD_MS + 1),
            "FAILED_VERDICT",
        ),
        (lambda evidence: evidence["thresholds"].update(claim_p95_ms=999.0), "INVALID_THRESHOLD"),
        (lambda evidence: evidence["config"].update(worker_concurrency=1), "INVALID_CONFIG"),
        (lambda evidence: evidence["query_plan"]["selective"].update(index_names=[]), "FAILED_VERDICT"),
        (
            lambda evidence: evidence["source"]["statement"].update(kind="handwritten-sql"),
            "NON_PRODUCTION_STATEMENT",
        ),
        (
            lambda evidence: evidence["query_plan"].update(production_statement_sha256="c" * 64),
            "NON_PRODUCTION_STATEMENT",
        ),
    ],
)
def test_evidence_validator_rejects_missing_commit_mismatch_failure_and_non_production_statement(
    mutation: object,
    reason: str,
) -> None:
    evidence = deepcopy(_valid_evidence())
    mutation(evidence)  # type: ignore[operator]

    validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit="a" * 40)

    assert validation.valid is False
    assert validation.reason == reason


def test_evidence_validator_accepts_commit_bound_passing_production_evidence() -> None:
    validation = validate_runtime_inbox_benchmark_evidence(_valid_evidence(), expected_commit="a" * 40)

    assert validation.valid is True
    assert validation.reason == "OK"


def test_evidence_validator_rejects_forged_matching_statement_and_plan_sha() -> None:
    evidence = _valid_evidence()
    evidence["source"]["statement"]["sha256"] = "b" * 64
    evidence["query_plan"]["production_statement_sha256"] = "b" * 64

    validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit="a" * 40)

    assert validation.valid is False
    assert validation.reason == "NON_PRODUCTION_STATEMENT"


def test_evidence_validator_rejects_tampered_canonical_fingerprint_inputs() -> None:
    evidence = _valid_evidence()
    evidence["source"]["statement"]["canonical_inputs"]["limit"] = 50

    validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit="a" * 40)

    assert validation.valid is False
    assert validation.reason == "NON_PRODUCTION_STATEMENT"


def test_evidence_validator_rejects_repository_builder_structure_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = _valid_evidence()
    original_builder = RuntimeInboxRepository.build_claim_received_statement

    def altered_builder(self: RuntimeInboxRepository, **kwargs: object) -> object:
        kwargs["processor_token"] = f"{kwargs['processor_token']}-changed"
        return original_builder(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(RuntimeInboxRepository, "build_claim_received_statement", altered_builder)

    validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit="a" * 40)

    assert validation.valid is False
    assert validation.reason == "NON_PRODUCTION_STATEMENT"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda evidence: evidence.update(generated_at=None), "INVALID_METADATA"),
        (lambda evidence: evidence.update(generated_at="2026-07-13T01:02:03"), "INVALID_METADATA"),
        (lambda evidence: evidence["database"].update(server_version=""), "INVALID_DATABASE_METADATA"),
        (
            lambda evidence: evidence["database"]["settings"].update(max_connections="0"),
            "INVALID_DATABASE_METADATA",
        ),
        (
            lambda evidence: evidence["database"]["settings"].update(shared_buffers=""),
            "INVALID_DATABASE_METADATA",
        ),
        (lambda evidence: evidence.update(sample_count=0), "INVALID_SAMPLE_COUNT"),
        (lambda evidence: evidence["metrics"].update(claim_sample_count=999), "INVALID_SAMPLE_COUNT"),
        (lambda evidence: evidence["metrics"].update(processed_count=999), "INVALID_SAMPLE_COUNT"),
        (lambda evidence: evidence["metrics"].update(lock_observation_count=0), "FAILED_VERDICT"),
        (lambda evidence: evidence["config"].update(claim_batch_size=25.0), "INVALID_CONFIG"),
        (lambda evidence: evidence["thresholds"].update(claim_p95_ms=150), "INVALID_THRESHOLD"),
        (lambda evidence: evidence["thresholds"].update(waiting_lock_samples=False), "INVALID_THRESHOLD"),
    ],
)
def test_evidence_validator_rejects_semantically_empty_or_inconsistent_evidence(
    mutation: object,
    reason: str,
) -> None:
    evidence = deepcopy(_valid_evidence())
    mutation(evidence)  # type: ignore[operator]

    validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit="a" * 40)

    assert validation.valid is False
    assert validation.reason == reason
