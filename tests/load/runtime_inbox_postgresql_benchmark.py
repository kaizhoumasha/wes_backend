"""RuntimeInbox 真实 PostgreSQL 并发 claim benchmark。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

if TYPE_CHECKING:
    from pathlib import Path

    import asyncpg

PENDING_INBOX_COUNT = 1_000
WORKER_CONCURRENCY = 4
CLAIM_BATCH_SIZE = 25
CLAIM_P95_THRESHOLD_MS = 150.0
THROUGHPUT_THRESHOLD_PER_SECOND = 1_000.0


@dataclass(slots=True)
class _BenchmarkState:
    claimed_ids: set[int] = field(default_factory=set)
    claim_samples_ms: list[float] = field(default_factory=list)
    duplicate_claim_count: int = 0
    waiting_lock_samples: int = 0
    max_waiting_locks: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
            provider_code, event_type, source_event_id, status, attempt_count,
            max_retries, next_retry_at, lease_until, processor_token,
            claim_bucket_key, received_at, payload_json
        ) VALUES (
            'benchmark', 'MIXED_BACKLOG', $1, $2, $3, $4, $5, $6, $7, $8, $9, '{}'::json
        )
        """,
        rows,
    )


async def _claim_query_plan(connection: asyncpg.Connection) -> dict[str, object]:
    now_ms = int(time.time() * 1000)
    raw_plan = await connection.fetchval(
        """
        EXPLAIN (FORMAT JSON)
        SELECT candidate.id
        FROM wes_runtime.runtime_inbox AS candidate
        WHERE (
            candidate.status = 'RECEIVED'
            OR (candidate.status = 'FAILED' AND candidate.next_retry_at <= $1)
            OR (candidate.status = 'PROCESSING' AND candidate.lease_until <= $1)
        )
          AND candidate.attempt_count < candidate.max_retries
          AND NOT EXISTS (
              SELECT 1
              FROM wes_runtime.runtime_inbox AS earlier
              WHERE earlier.claim_bucket_key = candidate.claim_bucket_key
                AND (earlier.received_at, earlier.id) < (candidate.received_at, candidate.id)
                AND (
                    earlier.status IN ('RECEIVED', 'PROCESSING')
                    OR (
                        earlier.status = 'FAILED'
                        AND earlier.next_retry_at IS NOT NULL
                        AND earlier.attempt_count < earlier.max_retries
                    )
                )
          )
        ORDER BY candidate.received_at, candidate.id
        LIMIT $2
        FOR UPDATE OF candidate SKIP LOCKED
        """,
        now_ms,
        CLAIM_BATCH_SIZE,
    )
    document = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    plan = document[0]["Plan"]
    node_types: list[str] = []
    index_names: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        node_types.append(str(node["Node Type"]))
        if node.get("Index Name"):
            index_names.append(str(node["Index Name"]))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return {
        "node_types": node_types,
        "index_names": sorted(set(index_names)),
        "total_cost": float(plan["Total Cost"]),
        "plan_rows": int(plan["Plan Rows"]),
    }


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


async def _run_benchmark() -> dict[str, object]:
    async with temporary_database() as (database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        connection = await connect(database)
        try:
            await _seed_mixed_backlog(connection)
            query_plan = await _claim_query_plan(connection)
        finally:
            await connection.close()

        engine = create_async_engine(database_url, pool_size=WORKER_CONCURRENCY + 1, max_overflow=0)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        service = RuntimeInboxService()
        repository = RuntimeInboxRepository()
        async with session_factory() as db:
            sli_before = asdict(await repository.get_sli_snapshot(db, now_ms=int(time.time() * 1_000)))
        state = _BenchmarkState()
        done = asyncio.Event()
        monitor = asyncio.create_task(_monitor_waiting_locks(database, state, done))
        started_at = time.perf_counter()
        try:
            await asyncio.gather(
                *(_claim_worker(worker_id, session_factory, service, state) for worker_id in range(WORKER_CONCURRENCY))
            )
        finally:
            elapsed_seconds = time.perf_counter() - started_at
            done.set()
            await monitor

        async with session_factory() as db:
            processed_count = int(
                await db.scalar(
                    select(func.count()).select_from(RuntimeInbox).where(RuntimeInbox.status == "PROCESSED")
                )
                or 0
            )
            sli_after = asdict(await repository.get_sli_snapshot(db, now_ms=int(time.time() * 1_000)))
        await engine.dispose()

        evidence: dict[str, object] = {
            "scenario": "runtime_inbox_claim",
            "source": {"kind": "postgresql"},
            "workload": {
                "pending_inbox_count": PENDING_INBOX_COUNT,
                "worker_concurrency": WORKER_CONCURRENCY,
                "claim_batch_size": CLAIM_BATCH_SIZE,
                "mix": {"received": 700, "failed_due": 200, "stale_processing": 100},
            },
            "metrics": {
                "claim_p50_ms": round(_percentile(state.claim_samples_ms, 0.50), 3),
                "claim_p95_ms": round(_percentile(state.claim_samples_ms, 0.95), 3),
                "claim_sample_count": len(state.claim_samples_ms),
                "throughput_per_second": round(PENDING_INBOX_COUNT / elapsed_seconds, 3),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "duplicate_claim_count": state.duplicate_claim_count,
                "waiting_lock_samples": state.waiting_lock_samples,
                "max_waiting_locks": state.max_waiting_locks,
                "processed_count": processed_count,
            },
            "query_plan": query_plan,
            "thresholds": {
                "claim_p95_ms": CLAIM_P95_THRESHOLD_MS,
                "throughput_per_second": THROUGHPUT_THRESHOLD_PER_SECOND,
                "duplicate_claim_count": 0,
            },
            "sli_before": sli_before,
            "sli_after": sli_after,
        }
        return evidence


def run_runtime_inbox_postgresql_benchmark(evidence_path: Path | None = None) -> dict[str, object]:
    evidence = asyncio.run(_run_benchmark())
    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


__all__ = ["run_runtime_inbox_postgresql_benchmark"]
