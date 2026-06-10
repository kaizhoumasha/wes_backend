from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.dialects import postgresql

from src.app.workline.inbox_claim_bucket import build_claim_bucket_key
from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.repositories.inbox_repository import WorklineInboxClaim, WorklineInboxRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

HOT_CLAIM_INDEX = "ix_wes_biz_workline_inbox_hot_claim_bucket_fifo"
NEW_CLAIM_INDEX = "ix_wes_biz_workline_inbox_new_received_at"
RETRY_CLAIM_INDEX = "ix_wes_biz_workline_inbox_retry_next_retry_received_at"
PROCESSING_CLAIM_INDEX = "ix_wes_biz_workline_inbox_processing_updated_received_at"
BROAD_STATUS_INDEX = "ix_wes_biz_workline_inbox_status"


def _inbox(
    *,
    test_prefix: str,
    source_id: str,
    claim_bucket_key: str,
    received_at: datetime,
    status: InboxStatus = InboxStatus.NEW,
    processor_token: str | None = None,
    next_retry_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> WorklineInbox:
    return WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id=f"{test_prefix}:{source_id}",
        idempotency_key=f"{test_prefix}:{source_id}",
        trace_id=f"{test_prefix}:claim-plan:{source_id}",
        claim_bucket_key=claim_bucket_key,
        payload_json={"device_code": claim_bucket_key},
        status=status,
        processor_token=processor_token,
        received_at=received_at,
        next_retry_at=next_retry_at,
        updated_at=updated_at,
    )


async def _claim_in_transaction(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    processor_token: str,
    locked: asyncio.Event,
    release: asyncio.Event,
) -> list[str]:
    repository = WorklineInboxRepository()
    async with session_factory() as db:
        transaction = await db.begin()
        try:
            claims = await repository.claim_pending_messages(
                db,
                limit=1,
                processor_token=processor_token,
            )
            locked.set()
            await release.wait()
            return [claim.claim_bucket_key for claim in claims]
        finally:
            await transaction.rollback()


async def _claim_once_rollback(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
    processor_token: str,
) -> list[WorklineInboxClaim]:
    repository = WorklineInboxRepository()
    async with session_factory() as db:
        transaction = await db.begin()
        try:
            return await repository.claim_pending_messages(
                db,
                limit=limit,
                processor_token=processor_token,
            )
        finally:
            await transaction.rollback()


async def _assert_claim_queue_isolated(db: AsyncSession, *, test_prefix: str) -> None:
    table = WorklineInbox.__table__
    columns = table.c
    external_hot_queue_rows = (
        select(columns.id, columns.trace_id, columns.status)
        .where(
            columns.status.in_([InboxStatus.NEW, InboxStatus.RETRY, InboxStatus.PROCESSING]),
            or_(
                columns.trace_id.is_(None),
                ~columns.trace_id.like(f"{test_prefix}:claim-plan:%"),
            ),
        )
        .limit(5)
    )
    rows = (await db.execute(external_hot_queue_rows)).all()
    assert rows == [], (
        "PostgreSQL claim gate requires an isolated integration queue; "
        f"found external hot-queue WorklineInbox rows: {rows}"
    )


@pytest.mark.asyncio
async def test_claim_pending_messages_claims_different_bucket_while_first_transaction_holds_lock(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    base_time = timezone.now_for_db()

    async with integration_session_factory() as db:
        db.add_all(
            [
                _inbox(
                    test_prefix=test_prefix,
                    source_id="bucket-a-head",
                    claim_bucket_key=f"{test_prefix}:bucket-a",
                    received_at=base_time,
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="bucket-b-head",
                    claim_bucket_key=f"{test_prefix}:bucket-b",
                    received_at=base_time + timedelta(milliseconds=1),
                ),
            ]
        )
        await db.commit()
        await _assert_claim_queue_isolated(db, test_prefix=test_prefix)

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    first_task = asyncio.create_task(
        _claim_in_transaction(
            integration_session_factory,
            processor_token=f"{test_prefix}:worker-a",
            locked=first_locked,
            release=release_first,
        )
    )

    try:
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        second_claims = await asyncio.wait_for(
            _claim_once_rollback(
                integration_session_factory,
                limit=1,
                processor_token=f"{test_prefix}:worker-b",
            ),
            timeout=5,
        )
    finally:
        release_first.set()
        if not first_task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(first_task, timeout=5)
        if not first_task.done():
            first_task.cancel()
            with suppress(asyncio.CancelledError):
                await first_task

    first_buckets = first_task.result()

    assert first_buckets == [f"{test_prefix}:bucket-a"]
    assert [claim.claim_bucket_key for claim in second_claims] == [f"{test_prefix}:bucket-b"]


@pytest.mark.asyncio
async def test_claim_pending_messages_same_bucket_processing_head_blocks_later_message(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    base_time = timezone.now_for_db()

    async with integration_session_factory() as db:
        db.add_all(
            [
                _inbox(
                    test_prefix=test_prefix,
                    source_id="same-bucket-processing-head",
                    claim_bucket_key=f"{test_prefix}:same-bucket",
                    received_at=base_time,
                    status=InboxStatus.PROCESSING,
                    processor_token=f"{test_prefix}:held",
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="same-bucket-tail",
                    claim_bucket_key=f"{test_prefix}:same-bucket",
                    received_at=base_time + timedelta(milliseconds=1),
                ),
            ]
        )
        await db.commit()
        await _assert_claim_queue_isolated(db, test_prefix=test_prefix)

    claims = await _claim_once_rollback(
        integration_session_factory,
        limit=10,
        processor_token=f"{test_prefix}:worker",
    )

    assert claims == []


@pytest.mark.asyncio
async def test_claim_pending_messages_same_bucket_future_retry_head_blocks_later_new_message(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    base_time = timezone.now_for_db()

    async with integration_session_factory() as db:
        db.add_all(
            [
                _inbox(
                    test_prefix=test_prefix,
                    source_id="same-bucket-future-retry-head",
                    claim_bucket_key=f"{test_prefix}:same-bucket-future-retry",
                    received_at=base_time,
                    status=InboxStatus.RETRY,
                    next_retry_at=base_time + timedelta(seconds=30),
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="same-bucket-later-new",
                    claim_bucket_key=f"{test_prefix}:same-bucket-future-retry",
                    received_at=base_time + timedelta(milliseconds=1),
                ),
            ]
        )
        await db.commit()
        await _assert_claim_queue_isolated(db, test_prefix=test_prefix)

    claims = await _claim_once_rollback(
        integration_session_factory,
        limit=10,
        processor_token=f"{test_prefix}:worker",
    )

    assert claims == []


@pytest.mark.asyncio
async def test_claim_pending_messages_claims_new_retry_and_stale_processing_heads(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    base_time = timezone.now_for_db()
    stale_time = base_time - timedelta(minutes=20)

    async with integration_session_factory() as db:
        db.add_all(
            [
                _inbox(
                    test_prefix=test_prefix,
                    source_id="new-head",
                    claim_bucket_key=f"{test_prefix}:claimable:new",
                    received_at=base_time,
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="retry-ready-head",
                    claim_bucket_key=f"{test_prefix}:claimable:retry",
                    received_at=base_time + timedelta(milliseconds=1),
                    status=InboxStatus.RETRY,
                    next_retry_at=base_time - timedelta(seconds=1),
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="stale-processing-head",
                    claim_bucket_key=f"{test_prefix}:claimable:processing",
                    received_at=base_time + timedelta(milliseconds=2),
                    status=InboxStatus.PROCESSING,
                    processor_token=f"{test_prefix}:stale-worker",
                    updated_at=stale_time,
                ),
            ]
        )
        await db.commit()
        await _assert_claim_queue_isolated(db, test_prefix=test_prefix)

    claims = await _claim_once_rollback(
        integration_session_factory,
        limit=3,
        processor_token=f"{test_prefix}:worker",
    )

    assert [claim.claim_bucket_key for claim in claims] == [
        f"{test_prefix}:claimable:new",
        f"{test_prefix}:claimable:retry",
        f"{test_prefix}:claimable:processing",
    ]


async def _sql_backfill_claim_bucket_key(
    db: AsyncSession,
    *,
    session_id: int | None = None,
    device_id: int | None = None,
    workline_id: int | None = None,
    payload_json: dict[str, Any] | None = None,
) -> str:
    result = await db.execute(
        text(
            """
            WITH source AS (
                SELECT
                    CAST(:session_id AS bigint) AS session_id,
                    CAST(:device_id AS bigint) AS device_id,
                    CAST(:workline_id AS bigint) AS workline_id,
                    CAST(:payload_json AS jsonb) AS payload_json
            ),
            normalized AS (
                SELECT
                    CASE
                        WHEN session_id IS NOT NULL
                            THEN 'session:' || session_id::text
                        WHEN device_id IS NOT NULL
                            THEN 'device:' || device_id::text
                        WHEN NULLIF(btrim(payload_json ->> 'device_code'), '') IS NOT NULL
                            THEN 'device_code:' || NULLIF(btrim(payload_json ->> 'device_code'), '')
                        WHEN NULLIF(btrim(payload_json ->> 'location'), '') IS NOT NULL
                            THEN 'device_code:' || NULLIF(btrim(payload_json ->> 'location'), '')
                        WHEN workline_id IS NOT NULL
                            THEN 'workline:' || workline_id::text
                        ELSE 'serial:unknown'
                    END AS raw_claim_bucket_key
                FROM source
            )
            SELECT CASE
                WHEN length(normalized.raw_claim_bucket_key) <= 200 THEN normalized.raw_claim_bucket_key
                ELSE substring(normalized.raw_claim_bucket_key from 1 for 183)
                    || ':' || substring(md5(normalized.raw_claim_bucket_key) from 1 for 16)
            END
            FROM normalized
            """
        ),
        {
            "session_id": session_id,
            "device_id": device_id,
            "workline_id": workline_id,
            "payload_json": json.dumps(payload_json or {}),
        },
    )
    return str(result.scalar_one())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_id": 10, "device_id": 20, "workline_id": 30, "payload_json": {"device_code": "SCN-01"}},
        {"device_id": 20, "workline_id": 30, "payload_json": {"device_code": "SCN-01"}},
        {"workline_id": 30, "payload_json": {"device_code": "  SCN-01  "}},
        {"workline_id": 30, "payload_json": {"device_code": "   ", "location": "  LOC-01  "}},
        {"workline_id": 30, "payload_json": {"device_code": "", "location": "   "}},
        {"payload_json": {"device_code": "   ", "location": ""}},
        {"payload_json": {"device_code": "S" * 500}},
    ],
)
async def test_claim_bucket_backfill_sql_matches_runtime_helper(
    integration_db_session: AsyncSession,
    kwargs: dict[str, Any],
) -> None:
    sql_key = await _sql_backfill_claim_bucket_key(integration_db_session, **kwargs)
    runtime_key = build_claim_bucket_key(**kwargs)

    assert sql_key == runtime_key


class _CapturedResult:
    def mappings(self) -> _CapturedResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return []


class _CapturingDb:
    def __init__(self) -> None:
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _CapturedResult:
        self.statement = statement
        return _CapturedResult()


def _iter_plan_nodes(plan_node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan_node
    for child in plan_node.get("Plans", []):
        if isinstance(child, dict):
            yield from _iter_plan_nodes(child)


async def _explain_plan_nodes(db: AsyncSession, statement: Any) -> list[dict[str, Any]]:
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    result = await db.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}"))
    raw_plan = result.scalar_one()
    plan_doc = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    return list(_iter_plan_nodes(plan_doc[0]["Plan"]))


def _node_uses_index(plan_node: dict[str, Any], *, index_name: str) -> bool:
    for node in _iter_plan_nodes(plan_node):
        if "Index" in str(node.get("Node Type", "")) and node.get("Index Name") == index_name:
            return True
    return False


def _alias_uses_index(plan_nodes: list[dict[str, Any]], *, alias: str, index_name: str) -> bool:
    for node in plan_nodes:
        if node.get("Alias") != alias:
            continue
        if _node_uses_index(node, index_name=index_name):
            return True
    return False


@pytest.mark.asyncio
async def test_claim_pending_messages_explain_uses_hot_queue_partial_indexes(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    repository = WorklineInboxRepository()
    base_time = timezone.now_for_db()
    stale_time = base_time - timedelta(minutes=20)

    async with integration_session_factory() as db:
        rows: list[WorklineInbox] = []
        for index in range(2000):
            rows.append(
                _inbox(
                    test_prefix=test_prefix,
                    source_id=f"history-{index}",
                    claim_bucket_key=f"{test_prefix}:history:{index}",
                    received_at=base_time - timedelta(days=1, seconds=index),
                    status=InboxStatus.PROCESSED,
                )
            )
        rows.extend(
            [
                _inbox(
                    test_prefix=test_prefix,
                    source_id="new-head",
                    claim_bucket_key=f"{test_prefix}:explain:new",
                    received_at=base_time,
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="retry-ready",
                    claim_bucket_key=f"{test_prefix}:explain:retry",
                    received_at=base_time + timedelta(milliseconds=1),
                    status=InboxStatus.RETRY,
                    next_retry_at=base_time - timedelta(seconds=1),
                ),
                _inbox(
                    test_prefix=test_prefix,
                    source_id="stale-processing",
                    claim_bucket_key=f"{test_prefix}:explain:processing",
                    received_at=base_time + timedelta(milliseconds=2),
                    status=InboxStatus.PROCESSING,
                    processor_token=f"{test_prefix}:stale-worker",
                    updated_at=stale_time,
                ),
            ]
        )
        # Seed a real hot queue, not only processed history. Otherwise PostgreSQL can pick
        # a small-sample status index plan that says little about the production access path.
        for index in range(1500):
            rows.append(
                _inbox(
                    test_prefix=test_prefix,
                    source_id=f"future-retry-noise-{index}",
                    claim_bucket_key=f"{test_prefix}:noise:{index}",
                    received_at=base_time + timedelta(seconds=1, milliseconds=index),
                    status=InboxStatus.RETRY,
                    next_retry_at=base_time + timedelta(hours=1),
                )
            )
        hot_queue_seed_count = sum(
            1 for row in rows if row.status in {InboxStatus.NEW, InboxStatus.RETRY, InboxStatus.PROCESSING}
        )
        assert hot_queue_seed_count >= 1000
        db.add_all(rows)
        await db.commit()
        await _assert_claim_queue_isolated(db, test_prefix=test_prefix)
        await db.execute(text("ANALYZE wes_biz.workline_inbox"))
        await db.commit()

    capturing_db = _CapturingDb()
    await repository.claim_pending_messages(
        capturing_db,  # type: ignore[arg-type]
        limit=5,
        processor_token=f"{test_prefix}:explain-worker",
    )
    assert capturing_db.statement is not None

    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            plan_nodes = await _explain_plan_nodes(db, capturing_db.statement)
        finally:
            await transaction.rollback()

    assert _alias_uses_index(plan_nodes, alias="claim_candidate", index_name=NEW_CLAIM_INDEX)
    assert _alias_uses_index(plan_nodes, alias="claim_candidate", index_name=RETRY_CLAIM_INDEX)
    assert _alias_uses_index(plan_nodes, alias="claim_candidate", index_name=PROCESSING_CLAIM_INDEX)
    assert not _alias_uses_index(plan_nodes, alias="claim_candidate", index_name=BROAD_STATUS_INDEX)
    assert _alias_uses_index(plan_nodes, alias="earlier_inbox", index_name=HOT_CLAIM_INDEX)
