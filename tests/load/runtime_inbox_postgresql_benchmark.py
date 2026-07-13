"""RuntimeInbox 真实 PostgreSQL 并发 claim benchmark。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
    from pathlib import Path

    import asyncpg

PENDING_INBOX_COUNT = 1_000
WORKER_CONCURRENCY = 4
LOCK_MONITOR_CONNECTION_COUNT = 1
CLAIM_BATCH_SIZE = 25
CLAIM_P95_THRESHOLD_MS = 150.0
THROUGHPUT_THRESHOLD_PER_SECOND = 1_000.0
SELECTIVE_FIXTURE_ROW_COUNT = 10_000
BENCHMARK_EVIDENCE_SCHEMA_VERSION = "runtime-inbox-claim-benchmark/v1"
PRODUCTION_CLAIM_STATEMENT_KIND = "runtime-inbox-repository-claim"
CLAIM_STATEMENT_FINGERPRINT_VERSION = "runtime-inbox-claim-statement/v1"
PRODUCTION_CLAIM_BUILDER = (
    "src.app.runtime.orchestration.repositories.runtime_inbox_repository."
    "RuntimeInboxRepository.build_claim_received_statement"
)
_CANONICAL_CLAIM_STATEMENT_INPUTS = {
    "limit": CLAIM_BATCH_SIZE,
    "now_ms": 2_000_000_000_000,
    "processor_token": "runtime-inbox-benchmark-fingerprint",
    "stale_after_seconds": 60,
}
_CLAIM_INDEX_NAMES = frozenset(
    {
        "ix_wes_runtime_runtime_inbox_status_received",
        "ix_wes_runtime_runtime_inbox_failed_retry_at",
        "ix_wes_runtime_runtime_inbox_processing_lease",
        "ix_wes_runtime_runtime_inbox_bucket_fifo",
    }
)
_EXPECTED_CONFIG = {
    "pending_inbox_count": PENDING_INBOX_COUNT,
    "worker_concurrency": WORKER_CONCURRENCY,
    "claim_batch_size": CLAIM_BATCH_SIZE,
    "selective_fixture_row_count": SELECTIVE_FIXTURE_ROW_COUNT,
}
_EXPECTED_THRESHOLDS = {
    "claim_p95_ms": CLAIM_P95_THRESHOLD_MS,
    "throughput_per_second": THROUGHPUT_THRESHOLD_PER_SECOND,
    "duplicate_claim_count": 0,
    "waiting_lock_samples": 0,
    "max_waiting_locks": 0,
}


@dataclass(slots=True)
class _BenchmarkState:
    claimed_ids: set[int] = field(default_factory=set)
    claim_samples_ms: list[float] = field(default_factory=list)
    duplicate_claim_count: int = 0
    waiting_lock_samples: int = 0
    max_waiting_locks: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True, slots=True)
class RuntimeInboxBenchmarkEvidenceValidation:
    """Commit-bound benchmark evidence validation result。"""

    valid: bool
    reason: str = "OK"
    field: str | None = None


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile) - 1))
    return ordered[index]


async def _seed_mixed_backlog(connection: asyncpg.Connection) -> None:
    now_ms = int(time.time() * 1000)
    rows: list[tuple[object, ...]] = []
    for index in range(PENDING_INBOX_COUNT):
        status_slot = index % 10
        status = "RECEIVED" if status_slot < 7 else "FAILED" if status_slot < 9 else "PROCESSING"
        rows.append(
            (
                f"benchmark-event-{index}",
                status,
                0 if status != "PROCESSING" else 1,
                5,
                0 if status == "FAILED" else None,
                0 if status == "PROCESSING" else None,
                f"stale-owner-{index}" if status == "PROCESSING" else None,
                f"benchmark-bucket-{index % 100}",
                now_ms + index,
            )
        )
    await connection.executemany(
        """
        INSERT INTO wes_runtime.runtime_inbox (
            kind, provider_code, event_type, source_event_id, status, attempt_count,
            max_retries, next_retry_at, lease_until, processor_token,
            claim_bucket_key, received_at, payload_json, payload_hash, payload_schema_version
        ) VALUES (
            'INTERNAL_EVENT', 'benchmark', 'MIXED_BACKLOG', $1, $2, $3, $4, $5, $6, $7, $8, $9,
            '{}'::json, 'sha256:benchmark', 1
        )
        """,
        rows,
    )


async def _seed_selective_plan_fixture(connection: asyncpg.Connection) -> None:
    """建立独立的大表高选择性 fixture，避免小表 Seq Scan 误判。"""

    now_ms = int(time.time() * 1000)
    rows = [
        (
            f"selective-event-{index}",
            "RECEIVED" if index == SELECTIVE_FIXTURE_ROW_COUNT - 1 else "PROCESSED",
            f"selective-bucket-{index}",
            now_ms + index,
        )
        for index in range(SELECTIVE_FIXTURE_ROW_COUNT)
    ]
    await connection.executemany(
        """
        INSERT INTO wes_runtime.runtime_inbox (
            kind, provider_code, event_type, source_event_id, status, attempt_count, max_retries,
            claim_bucket_key, received_at, payload_json, payload_hash, payload_schema_version
        ) VALUES (
            'INTERNAL_EVENT', 'benchmark-plan', 'SELECTIVE_PLAN', $1, $2, 0, 5,
            $3, $4, '{}'::json, 'sha256:benchmark-plan', 1
        )
        """,
        rows,
    )
    await connection.execute("ANALYZE wes_runtime.runtime_inbox")


async def _clear_selective_plan_fixture(connection: asyncpg.Connection) -> None:
    """只清理 benchmark 自有 plan fixture，避免跨 FK 表扩大 mutation。"""

    await connection.execute("DELETE FROM wes_runtime.runtime_inbox WHERE provider_code = 'benchmark-plan'")


def _compile_production_claim_statement() -> tuple[str, str]:
    """使用固定输入编译生产 statement，运行时随机 token/时间不进入 fingerprint。"""

    statement = RuntimeInboxRepository().build_claim_received_statement(**_CANONICAL_CLAIM_STATEMENT_INPUTS)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    return sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()


async def _claim_query_plan(connection: asyncpg.Connection) -> dict[str, object]:
    sql, statement_sha256 = _compile_production_claim_statement()
    raw_plan = await connection.fetchval(f"EXPLAIN (FORMAT JSON) {sql}")
    document = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    plan = document[0]["Plan"]
    node_types: list[str] = []
    index_names: list[str] = []
    runtime_inbox_seq_scan_relations: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        node_types.append(str(node["Node Type"]))
        if node.get("Index Name"):
            index_names.append(str(node["Index Name"]))
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "runtime_inbox":
            runtime_inbox_seq_scan_relations.append(str(node.get("Alias") or "runtime_inbox"))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    summary: dict[str, object] = {
        "node_types": node_types,
        "index_names": sorted(set(index_names)),
        "runtime_inbox_seq_scan_relations": sorted(set(runtime_inbox_seq_scan_relations)),
        "total_cost": float(plan["Total Cost"]),
        "plan_rows": int(plan["Plan Rows"]),
        "statement_sha256": statement_sha256,
    }
    try:
        _validate_selective_query_plan(summary)
    except AssertionError as exc:
        summary["gate_passed"] = False
        summary["gate_error"] = str(exc)
    else:
        summary["gate_passed"] = True
    return summary


def _validate_selective_query_plan(plan: dict[str, object]) -> None:
    seq_scans = plan.get("runtime_inbox_seq_scan_relations")
    if isinstance(seq_scans, list) and seq_scans:
        raise AssertionError(f"runtime_inbox_seq_scan: {','.join(str(value) for value in seq_scans)}")
    index_names = plan.get("index_names")
    used_indexes = set(index_names) if isinstance(index_names, list) else set()
    if not used_indexes.intersection(_CLAIM_INDEX_NAMES):
        raise AssertionError("runtime_inbox_claim_index: production claim plan did not use a claim index")


def _repository_metadata() -> dict[str, object]:
    repo_root = str(__file__).split("/tests/load/", maxsplit=1)[0]
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required for benchmark evidence")
    commit_sha = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            [git, "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return {"commit_sha": commit_sha, "dirty": dirty}


async def _database_metadata(connection: asyncpg.Connection) -> dict[str, object]:
    return {
        "server_version": str(await connection.fetchval("SELECT version()")),
        "settings": {
            "max_connections": str(await connection.fetchval("SELECT current_setting('max_connections')")),
            "shared_buffers": str(await connection.fetchval("SELECT current_setting('shared_buffers')")),
        },
    }


def _nested_value(document: Mapping[str, object], path: str) -> object:
    value: object = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


_REQUIRED_EVIDENCE_FIELDS = (
    "schema_version",
    "generated_at",
    "repository.commit_sha",
    "repository.dirty",
    "source.kind",
    "source.statement.kind",
    "source.statement.builder",
    "source.statement.fingerprint_version",
    "source.statement.canonical_inputs.limit",
    "source.statement.canonical_inputs.now_ms",
    "source.statement.canonical_inputs.processor_token",
    "source.statement.canonical_inputs.stale_after_seconds",
    "source.statement.sha256",
    "database.server_version",
    "database.settings.max_connections",
    "database.settings.shared_buffers",
    "config.pending_inbox_count",
    "config.worker_concurrency",
    "config.claim_batch_size",
    "config.selective_fixture_row_count",
    "workload.mix",
    "sample_count",
    "metrics.claim_p95_ms",
    "metrics.throughput_per_second",
    "metrics.duplicate_claim_count",
    "metrics.waiting_lock_samples",
    "metrics.max_waiting_locks",
    "metrics.processed_count",
    "thresholds.claim_p95_ms",
    "thresholds.throughput_per_second",
    "thresholds.duplicate_claim_count",
    "thresholds.waiting_lock_samples",
    "thresholds.max_waiting_locks",
    "query_plan.production_statement_sha256",
    "query_plan.selective.statement_sha256",
    "query_plan.selective.node_types",
    "query_plan.selective.index_names",
    "query_plan.selective.runtime_inbox_seq_scan_relations",
    "query_plan.selective.gate_passed",
    "verdict.passed",
    "verdict.failed_gates",
)


def validate_runtime_inbox_benchmark_evidence(
    evidence: Mapping[str, object],
    *,
    expected_commit: str,
) -> RuntimeInboxBenchmarkEvidenceValidation:
    """严格校验正式 benchmark evidence 的字段、来源、commit 与 gate 结论。"""

    for field_name in _REQUIRED_EVIDENCE_FIELDS:
        try:
            _nested_value(evidence, field_name)
        except KeyError:
            return RuntimeInboxBenchmarkEvidenceValidation(False, "MISSING_FIELD", field_name)

    if evidence["schema_version"] != BENCHMARK_EVIDENCE_SCHEMA_VERSION:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "INVALID_SCHEMA_VERSION", "schema_version")
    commit_sha = _nested_value(evidence, "repository.commit_sha")
    if commit_sha != expected_commit:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "COMMIT_MISMATCH", "repository.commit_sha")
    if (
        not isinstance(commit_sha, str)
        or len(commit_sha) != 40
        or any(char not in "0123456789abcdef" for char in commit_sha)
    ):
        return RuntimeInboxBenchmarkEvidenceValidation(False, "INVALID_COMMIT", "repository.commit_sha")
    if _nested_value(evidence, "repository.dirty") is not False:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "DIRTY_WORKTREE", "repository.dirty")
    for field_name, expected_value in _EXPECTED_CONFIG.items():
        if _nested_value(evidence, f"config.{field_name}") != expected_value:
            return RuntimeInboxBenchmarkEvidenceValidation(False, "INVALID_CONFIG", f"config.{field_name}")
    for field_name, expected_value in _EXPECTED_THRESHOLDS.items():
        if _nested_value(evidence, f"thresholds.{field_name}") != expected_value:
            return RuntimeInboxBenchmarkEvidenceValidation(False, "INVALID_THRESHOLD", f"thresholds.{field_name}")
    if (
        _nested_value(evidence, "source.kind") != "postgresql"
        or _nested_value(evidence, "source.statement.kind") != PRODUCTION_CLAIM_STATEMENT_KIND
        or _nested_value(evidence, "source.statement.builder") != PRODUCTION_CLAIM_BUILDER
        or _nested_value(evidence, "source.statement.fingerprint_version") != CLAIM_STATEMENT_FINGERPRINT_VERSION
        or _nested_value(evidence, "source.statement.canonical_inputs") != _CANONICAL_CLAIM_STATEMENT_INPUTS
    ):
        return RuntimeInboxBenchmarkEvidenceValidation(False, "NON_PRODUCTION_STATEMENT", "source.statement")
    statement_sha = _nested_value(evidence, "source.statement.sha256")
    if (
        not isinstance(statement_sha, str)
        or len(statement_sha) != 64
        or any(char not in "0123456789abcdef" for char in statement_sha)
    ):
        return RuntimeInboxBenchmarkEvidenceValidation(False, "NON_PRODUCTION_STATEMENT", "source.statement.sha256")
    try:
        _canonical_sql, expected_statement_sha = _compile_production_claim_statement()
    except Exception:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "NON_PRODUCTION_STATEMENT", "source.statement.sha256")
    if (
        statement_sha != expected_statement_sha
        or statement_sha != _nested_value(evidence, "query_plan.production_statement_sha256")
        or statement_sha != _nested_value(evidence, "query_plan.selective.statement_sha256")
    ):
        return RuntimeInboxBenchmarkEvidenceValidation(False, "NON_PRODUCTION_STATEMENT", "source.statement.sha256")
    if _evidence_gate_failures(evidence):
        return RuntimeInboxBenchmarkEvidenceValidation(False, "FAILED_VERDICT", "metrics")
    selective_plan = _nested_value(evidence, "query_plan.selective")
    try:
        _validate_selective_query_plan(dict(selective_plan) if isinstance(selective_plan, Mapping) else {})
    except AssertionError:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "FAILED_VERDICT", "query_plan.selective")
    if _nested_value(evidence, "query_plan.selective.gate_passed") is not True:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "FAILED_VERDICT", "query_plan.selective")
    if _nested_value(evidence, "verdict.passed") is not True or _nested_value(evidence, "verdict.failed_gates") != []:
        return RuntimeInboxBenchmarkEvidenceValidation(False, "FAILED_VERDICT", "verdict")
    return RuntimeInboxBenchmarkEvidenceValidation(True)


def _evidence_gate_failures(evidence: Mapping[str, object]) -> list[str]:
    failures: list[str] = []
    comparisons = (
        ("claim_p95_ms", "maximum"),
        ("throughput_per_second", "minimum"),
        ("duplicate_claim_count", "exact"),
        ("waiting_lock_samples", "exact"),
        ("max_waiting_locks", "exact"),
    )
    for metric_name, comparison in comparisons:
        metric = _nested_value(evidence, f"metrics.{metric_name}")
        threshold = _nested_value(evidence, f"thresholds.{metric_name}")
        numeric_values = (
            isinstance(metric, int | float)
            and not isinstance(metric, bool)
            and isinstance(threshold, int | float)
            and not isinstance(threshold, bool)
        )
        failed_comparison = numeric_values and (
            (comparison == "maximum" and metric > threshold)
            or (comparison == "minimum" and metric < threshold)
            or (comparison == "exact" and metric != threshold)
        )
        if not numeric_values or failed_comparison:
            failures.append(metric_name)
    if _nested_value(evidence, "metrics.processed_count") != PENDING_INBOX_COUNT:
        failures.append("processed_count")
    return failures


async def _monitor_waiting_locks(database: str, state: _BenchmarkState, done: asyncio.Event) -> None:
    connection = await connect(database)
    try:
        while not done.is_set():
            waiting = int(
                await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database())
                      AND NOT granted
                    """
                )
                or 0
            )
            state.waiting_lock_samples += waiting
            state.max_waiting_locks = max(state.max_waiting_locks, waiting)
            await asyncio.sleep(0.001)
    finally:
        await connection.close()


async def _claim_worker(
    worker_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    service: RuntimeInboxService,
    state: _BenchmarkState,
) -> None:
    async with session_factory() as db:
        while True:
            token = f"benchmark-worker-{worker_id}-{uuid4()}"
            started_at = time.perf_counter()
            claims = await service.claim_for_processing(
                db,
                limit=CLAIM_BATCH_SIZE,
                processor_token=token,
                stale_after_seconds=60,
            )
            await db.commit()
            elapsed_ms = (time.perf_counter() - started_at) * 1_000
            if not claims:
                break
            async with state.lock:
                state.claim_samples_ms.append(elapsed_ms)
                for claim in claims:
                    inbox_id = int(claim["id"])
                    if inbox_id in state.claimed_ids:
                        state.duplicate_claim_count += 1
                    state.claimed_ids.add(inbox_id)
            for claim in claims:
                updated = await service.mark_processed(
                    db,
                    inbox_id=int(claim["id"]),
                    lease_token=str(claim["processor_token"]),
                )
                if not updated:
                    raise AssertionError(f"benchmark terminal fencing failed: inbox_id={claim['id']}")
            await db.commit()


@asynccontextmanager
async def _managed_engine(engine: Any) -> AsyncIterator[None]:
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
    try:
        await engine.dispose()
    except BaseException:
        if primary_error is None:
            raise
        primary_error.add_note("cleanup=engine_dispose_failed")
    if primary_error is not None:
        raise primary_error from None


async def _run_workers_with_monitor(
    workers: Iterable[Coroutine[Any, Any, None]],
    monitor: Coroutine[Any, Any, None],
    *,
    done: asyncio.Event,
    clock: Callable[[], float] = time.perf_counter,
) -> float:
    worker_tasks = [
        asyncio.create_task(worker, name=f"runtime-inbox-benchmark-worker-{index}")
        for index, worker in enumerate(workers)
    ]
    monitor_task = asyncio.create_task(monitor, name="runtime-inbox-benchmark-monitor")

    async def wait_for_workers() -> None:
        await asyncio.gather(*worker_tasks)

    worker_supervisor = asyncio.create_task(wait_for_workers(), name="runtime-inbox-benchmark-workers")
    primary_error: BaseException | None = None
    workers_finished_at: float | None = None
    try:
        completed, _pending = await asyncio.wait(
            {worker_supervisor, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_supervisor in completed:
            await worker_supervisor
            workers_finished_at = clock()
        else:
            await monitor_task
            if not worker_supervisor.done():
                raise RuntimeError("runtime inbox benchmark monitor exited before workers")
    except BaseException as exc:
        primary_error = exc
    finally:
        done.set()
        for task in worker_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        if not worker_supervisor.done():
            worker_supervisor.cancel()
        await asyncio.gather(worker_supervisor, return_exceptions=True)
        monitor_result = await asyncio.gather(monitor_task, return_exceptions=True)
        if primary_error is None and isinstance(monitor_result[0], BaseException):
            primary_error = monitor_result[0]

    if primary_error is not None:
        raise primary_error from None
    assert workers_finished_at is not None
    return workers_finished_at


async def _run_benchmark() -> dict[str, object]:
    async with temporary_database(required_free_slots=WORKER_CONCURRENCY + LOCK_MONITOR_CONNECTION_COUNT) as (
        database,
        database_url,
    ):
        run_alembic("upgrade", "head", database_url=database_url)
        connection = await connect(database)
        try:
            database_metadata = await _database_metadata(connection)
            await _seed_selective_plan_fixture(connection)
            selective_query_plan = await _claim_query_plan(connection)
            await _clear_selective_plan_fixture(connection)
            await _seed_mixed_backlog(connection)
        finally:
            await connection.close()

        engine = create_async_engine(database_url, pool_size=WORKER_CONCURRENCY + 1, max_overflow=0)
        async with _managed_engine(engine):
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            service = RuntimeInboxService()
            repository = RuntimeInboxRepository()
            async with session_factory() as db:
                sli_before = asdict(await repository.get_sli_snapshot(db, now_ms=int(time.time() * 1_000)))
            state = _BenchmarkState()
            done = asyncio.Event()
            started_at = time.perf_counter()
            workers_finished_at = await _run_workers_with_monitor(
                (_claim_worker(worker_id, session_factory, service, state) for worker_id in range(WORKER_CONCURRENCY)),
                _monitor_waiting_locks(database, state, done),
                done=done,
            )
            elapsed_seconds = workers_finished_at - started_at

            async with session_factory() as db:
                processed_count = int(
                    await db.scalar(
                        select(func.count()).select_from(RuntimeInbox).where(RuntimeInbox.status == "PROCESSED")
                    )
                    or 0
                )
                sli_after = asdict(await repository.get_sli_snapshot(db, now_ms=int(time.time() * 1_000)))

            metrics: dict[str, int | float] = {
                "claim_p50_ms": round(_percentile(state.claim_samples_ms, 0.50), 3),
                "claim_p95_ms": round(_percentile(state.claim_samples_ms, 0.95), 3),
                "claim_sample_count": len(state.claim_samples_ms),
                "throughput_per_second": round(PENDING_INBOX_COUNT / elapsed_seconds, 3),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "duplicate_claim_count": state.duplicate_claim_count,
                "waiting_lock_samples": state.waiting_lock_samples,
                "max_waiting_locks": state.max_waiting_locks,
                "processed_count": processed_count,
            }
            thresholds: dict[str, int | float] = dict(_EXPECTED_THRESHOLDS)
            failed_gates: list[str] = []
            if metrics["claim_p95_ms"] > thresholds["claim_p95_ms"]:
                failed_gates.append("claim_p95_ms")
            if metrics["throughput_per_second"] < thresholds["throughput_per_second"]:
                failed_gates.append("throughput_per_second")
            for metric_name in ("duplicate_claim_count", "waiting_lock_samples", "max_waiting_locks"):
                if metrics[metric_name] != thresholds[metric_name]:
                    failed_gates.append(metric_name)
            if processed_count != PENDING_INBOX_COUNT:
                failed_gates.append("processed_count")
            if selective_query_plan["gate_passed"] is not True:
                failed_gates.append("selective_query_plan")

            statement_sha256 = str(selective_query_plan["statement_sha256"])
            evidence: dict[str, object] = {
                "schema_version": BENCHMARK_EVIDENCE_SCHEMA_VERSION,
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "repository": _repository_metadata(),
                "scenario": "runtime_inbox_claim",
                "source": {
                    "kind": "postgresql",
                    "statement": {
                        "kind": PRODUCTION_CLAIM_STATEMENT_KIND,
                        "builder": PRODUCTION_CLAIM_BUILDER,
                        "fingerprint_version": CLAIM_STATEMENT_FINGERPRINT_VERSION,
                        "canonical_inputs": dict(_CANONICAL_CLAIM_STATEMENT_INPUTS),
                        "sha256": statement_sha256,
                    },
                },
                "database": database_metadata,
                "config": dict(_EXPECTED_CONFIG),
                "workload": {
                    "pending_inbox_count": PENDING_INBOX_COUNT,
                    "worker_concurrency": WORKER_CONCURRENCY,
                    "claim_batch_size": CLAIM_BATCH_SIZE,
                    "mix": {"received": 700, "failed_due": 200, "stale_processing": 100},
                },
                "sample_count": len(state.claim_samples_ms),
                "metrics": metrics,
                "query_plan": {
                    "production_statement_sha256": statement_sha256,
                    "selective": selective_query_plan,
                },
                "thresholds": thresholds,
                "verdict": {"passed": not failed_gates, "failed_gates": failed_gates},
                "sli_before": sli_before,
                "sli_after": sli_after,
            }
            return evidence


def run_runtime_inbox_postgresql_benchmark(evidence_path: Path | None = None) -> dict[str, object]:
    evidence = asyncio.run(_run_benchmark())
    if evidence_path is not None:
        expected_commit = str(_nested_value(evidence, "repository.commit_sha"))
        validation = validate_runtime_inbox_benchmark_evidence(evidence, expected_commit=expected_commit)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not validation.valid:
            raise AssertionError(f"runtime inbox benchmark evidence invalid: {validation.reason}:{validation.field}")
    return evidence


__all__ = [
    "BENCHMARK_EVIDENCE_SCHEMA_VERSION",
    "CLAIM_STATEMENT_FINGERPRINT_VERSION",
    "PRODUCTION_CLAIM_BUILDER",
    "PRODUCTION_CLAIM_STATEMENT_KIND",
    "RuntimeInboxBenchmarkEvidenceValidation",
    "run_runtime_inbox_postgresql_benchmark",
    "validate_runtime_inbox_benchmark_evidence",
]
