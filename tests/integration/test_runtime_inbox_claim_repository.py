"""RuntimeInbox 原子限量 claim 仓储契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import _runtime_claim_bucket_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _runtime_inbox(**values: Any) -> RuntimeInbox:
    """用完整 canonical envelope 构造真实可行动测试记录。"""

    if values.pop("audit_only", False):
        return RuntimeInbox(**values)
    source_event_id = str(values.get("source_event_id", "test-source-event"))
    defaults: dict[str, Any] = {
        "provider_code": "TEST",
        "event_type": "INTERNAL_EVENT",
        "source_event_id": source_event_id,
        "kind": "INTERNAL_EVENT",
        "payload_json": {},
        "payload_hash": f"sha256:{source_event_id}",
        "payload_schema_version": 1,
        "claim_bucket_key": f"source:{source_event_id}",
        "received_at": 1,
    }
    defaults.update(values)
    return RuntimeInbox(**defaults)


class _StatementCaptured(Exception):
    def __init__(self, statement: Any) -> None:
        self.statement = statement


class _CapturingDb:
    async def execute(self, statement: Any) -> None:
        raise _StatementCaptured(statement)


@pytest.mark.asyncio
async def test_claim_compiles_postgresql_atomic_fifo_limit_with_skip_locked() -> None:
    """limit/FIFO/同桶队首围栏必须全部进入单条 PostgreSQL UPDATE。"""

    repository = RuntimeInboxRepository()

    with pytest.raises(_StatementCaptured) as captured:
        await repository.claim_received_with_token(
            _CapturingDb(),  # type: ignore[arg-type]
            limit=3,
            processor_token="worker-1",
            stale_after_seconds=30,
        )

    sql = str(
        captured.value.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()
    assert sql.startswith("UPDATE WES_RUNTIME.RUNTIME_INBOX")
    assert "NOT (EXISTS (SELECT 1" in sql
    assert "CLAIM_BUCKET_KEY" in sql
    assert "ORDER BY" in sql and "RECEIVED_AT" in sql
    assert "LIMIT 3" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ATTEMPT_COUNT <" in sql and "MAX_RETRIES" in sql
    assert "KIND IS NOT NULL" in sql
    assert "SOURCE_EVENT_ID IS NOT NULL" in sql
    assert "PAYLOAD_JSON IS NOT NULL" in sql
    assert "PAYLOAD_HASH IS NOT NULL" in sql
    assert "PAYLOAD_SCHEMA_VERSION IS NOT NULL" in sql
    assert "CLAIM_BUCKET_KEY IS NOT NULL" in sql
    assert "RECEIVED_AT IS NOT NULL" in sql
    assert "PRE_CUTOVER_AUDIT_ONLY" in sql
    assert "RETURNING" in sql

    bound_types = {type(bind.type).__name__ for bind in captured.value.statement.compile().binds.values()}
    assert "BigInteger" in bound_types


@pytest.mark.asyncio
async def test_claim_executes_the_public_production_statement_builder() -> None:
    """Benchmark 与生产 claim 必须能共享同一个公开 statement builder。"""

    repository = RuntimeInboxRepository()
    expected = repository.build_claim_received_statement(
        limit=3,
        processor_token="worker-1",
        stale_after_seconds=30,
        now_ms=1_000,
    )

    with pytest.raises(_StatementCaptured) as captured:
        await repository.claim_received_with_token(
            _CapturingDb(),  # type: ignore[arg-type]
            limit=3,
            processor_token="worker-1",
            stale_after_seconds=30,
            now_ms=1_000,
        )

    assert str(captured.value.statement) == str(expected)


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _RecordingDb:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _MappingResult:
        self.statements.append(statement)
        return _MappingResult(self.rows)


@pytest.mark.asyncio
async def test_claim_returns_only_atomic_update_returning_rows() -> None:
    """claim 结果必须直接来自限量 UPDATE RETURNING，不能再二次查询或 Python 切片。"""

    rows = [
        {
            "id": 1,
            "processor_token": "worker-1",
            "provider_code": "ECS",
            "event_type": "INTERNAL_EVENT",
            "source_event_id": "event-1",
            "payload_json": {"event_type": "SCAN_COMPLETED"},
            "correlation_id": None,
            "execution_session_id": 10,
            "workline_id": 20,
            "device_id": 30,
            "command_id": None,
            "kind": "INTERNAL_EVENT",
            "trace_id": "trace-1",
            "event_id": "event-1",
            "causation_id": None,
            "received_at": 100,
        }
    ]
    db = _RecordingDb(rows)

    claims = await RuntimeInboxRepository().claim_received_with_token(
        db,  # type: ignore[arg-type]
        limit=1,
        processor_token="worker-1",
        stale_after_seconds=30,
    )

    assert len(db.statements) == 1
    assert claims == [rows[0] | {"payload_json": {"event_type": "SCAN_COMPLETED"}}]


@pytest.mark.asyncio
async def test_recovery_compiles_as_atomic_limited_skip_locked_update() -> None:
    """stale recovery 必须在单条 SQL 内锁候选、限量、分流并更新。"""

    repository = RuntimeInboxRepository()

    with pytest.raises(_StatementCaptured) as captured:
        await repository.recover_stale_leases(
            _CapturingDb(),  # type: ignore[arg-type]
            stale_after_seconds=30,
            limit=5,
        )

    sql = str(
        captured.value.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()
    assert sql.startswith("UPDATE WES_RUNTIME.RUNTIME_INBOX")
    assert "STATUS = 'PROCESSING'" in sql
    assert "LEASE_UNTIL <=" in sql
    assert "ORDER BY" in sql and "RECEIVED_AT" in sql
    assert "LIMIT 5" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "CASE WHEN" in sql
    assert "DEAD_LETTER" in sql and "RECEIVED" in sql
    assert "RETURNING" in sql


@pytest.mark.asyncio
async def test_legacy_terminal_failed_rows_do_not_block_bucket_head(db_session: AsyncSession) -> None:
    """无推进路径的 legacy FAILED 不能永久阻塞同 bucket 后续消息。"""

    rows = [
        _runtime_inbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="legacy-exhausted",
            payload_json={},
            status="FAILED",
            claim_bucket_key="bucket-legacy",
            received_at=1,
            attempt_count=3,
            max_retries=3,
            next_retry_at=0,
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="legacy-nonretry",
            payload_json={},
            status="FAILED",
            claim_bucket_key="bucket-legacy",
            received_at=2,
            attempt_count=1,
            max_retries=3,
            next_retry_at=None,
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="current-received",
            payload_json={},
            status="RECEIVED",
            claim_bucket_key="bucket-legacy",
            received_at=3,
            attempt_count=0,
            max_retries=3,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    claims = await RuntimeInboxRepository().claim_received_with_token(
        db_session,
        limit=1,
        processor_token="worker-current",
        stale_after_seconds=30,
    )

    assert [claim["source_event_id"] for claim in claims] == ["current-received"]


@pytest.mark.asyncio
async def test_same_numeric_id_in_distinct_session_namespaces_does_not_cross_block(
    db_session: AsyncSession,
) -> None:
    """WorklineSession 与 ExecutionSession 同号时必须落入不同 FIFO bucket。"""
    common = {
        "provider_code": "TEST",
        "event_type": "INTERNAL_EVENT",
        "source_event_id": "unused",
    }
    rows = [
        _runtime_inbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="workline-session-event",
            payload_json={"data": {"session_id": 41}},
            status="RECEIVED",
            claim_bucket_key=_runtime_claim_bucket_key(session_id=41, **common),
            received_at=1,
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="execution-session-event",
            payload_json={"data": {}},
            status="RECEIVED",
            claim_bucket_key=_runtime_claim_bucket_key(execution_session_id=41, **common),
            received_at=2,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    claims = await RuntimeInboxRepository().claim_received_with_token(
        db_session,
        limit=2,
        processor_token="worker-namespaces",
        stale_after_seconds=30,
    )

    assert {claim["source_event_id"] for claim in claims} == {
        "workline-session-event",
        "execution-session-event",
    }


@pytest.mark.asyncio
async def test_sli_snapshot_counts_states_and_oldest_claimable_age(db_session: AsyncSession) -> None:
    rows = [
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id="received",
            status="RECEIVED",
            received_at=1_000,
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id="failed-due",
            status="FAILED",
            received_at=2_000,
            next_retry_at=0,
            last_error_code="RESOURCE_WAIT",
            last_error_message="RESOURCE_WAIT",
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id="failed-future",
            status="FAILED",
            received_at=500,
            next_retry_at=20_000,
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id="processing-stale",
            status="PROCESSING",
            received_at=3_000,
            lease_until=0,
            processor_token="old-owner",
        ),
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id="dead-letter",
            status="DEAD_LETTER",
            received_at=100,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    snapshot = await RuntimeInboxRepository().get_sli_snapshot(db_session, now_ms=10_000)

    assert snapshot.status_counts == {
        "RECEIVED": 1,
        "PROCESSING": 1,
        "PROCESSED": 0,
        "FAILED": 2,
        "DEAD_LETTER": 1,
    }
    assert snapshot.oldest_claimable_age_ms == 9_000
    assert snapshot.stale_processing_count == 1
    assert snapshot.resource_wait_count == 1


@pytest.mark.asyncio
async def test_sli_snapshot_excludes_pre_cutover_audit_only_from_actionable_dead_letters(
    db_session: AsyncSession,
) -> None:
    """pre-cutover audit-only 是审计证据，不得触发操作型 dead-letter SLI。"""

    db_session.add_all(
        [
            _runtime_inbox(
                audit_only=True,
                provider_code="LEGACY",
                event_type="PRE_CUTOVER",
                status="DEAD_LETTER",
                last_error_code="PRE_CUTOVER_AUDIT_ONLY",
                last_error_message="Pre-cutover row has no canonical envelope; retained for audit only",
                received_at=1,
                failed_at=1,
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="actionable-dead-letter",
                kind="INTERNAL_EVENT",
                payload_json={},
                payload_hash="sha256:actionable",
                payload_schema_version=1,
                claim_bucket_key="source:actionable-dead-letter",
                status="DEAD_LETTER",
                received_at=2,
                failed_at=3,
            ),
        ]
    )
    await db_session.commit()

    snapshot = await RuntimeInboxRepository().get_sli_snapshot(db_session, now_ms=10)

    assert snapshot.status_counts["DEAD_LETTER"] == 1


@pytest.mark.asyncio
async def test_sli_oldest_claimable_age_respects_bucket_fifo_blocker(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            _runtime_inbox(
                provider_code="TEST",
                event_type="SLI",
                source_event_id="active-head",
                status="PROCESSING",
                received_at=100,
                lease_until=20_000,
                processor_token="active-owner",
                claim_bucket_key="bucket-a",
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="SLI",
                source_event_id="blocked-tail",
                status="RECEIVED",
                received_at=200,
                claim_bucket_key="bucket-a",
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="SLI",
                source_event_id="claimable-other-bucket",
                status="RECEIVED",
                received_at=500,
                claim_bucket_key="bucket-b",
            ),
        ]
    )
    await db_session.commit()

    snapshot = await RuntimeInboxRepository().get_sli_snapshot(db_session, now_ms=1_000)

    assert snapshot.oldest_claimable_age_ms == 500


@pytest.mark.asyncio
async def test_repository_emits_reclaim_fencing_resource_wait_and_dead_letter_sli(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration.observability import runtime_observability_registry

    rows = [
        _runtime_inbox(
            provider_code="TEST",
            event_type="SLI",
            source_event_id=f"event-{index}",
            status="PROCESSING",
            received_at=index,
            lease_until=0 if index == 1 else 9_000_000_000_000_000,
            processor_token=f"token-{index}",
        )
        for index in range(1, 5)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    emit_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_observability_registry, "emit", lambda name, attrs: emit_calls.append((name, attrs)))
    repository = RuntimeInboxRepository()

    assert await repository.recover_stale_leases(db_session, stale_after_seconds=60, limit=10) == 1
    assert await repository.update_terminal_state(
        db_session,
        inbox_id=rows[1].id,
        lease_token="token-2",
        target_state="FAILED",
        extra_values={"last_error_code": "RESOURCE_WAIT", "last_error_message": "RESOURCE_WAIT"},
    )
    assert await repository.update_terminal_state(
        db_session,
        inbox_id=rows[2].id,
        lease_token="token-3",
        target_state="DEAD_LETTER",
    )
    assert not await repository.update_terminal_state(
        db_session,
        inbox_id=rows[3].id,
        lease_token="wrong-token",
        target_state="PROCESSED",
    )

    assert [name for name, _attrs in emit_calls] == [
        "runtime_inbox.resource_wait",
        "runtime_inbox.dead_letter",
        "runtime_inbox.fencing_reject",
    ]


@pytest.mark.asyncio
async def test_latest_by_workline_session_uses_explicit_namespace_column(db_session: AsyncSession) -> None:
    """WorklineSession 热查询必须使用独立显式列，不能借用 ExecutionSession 或扫描 JSON。"""

    db_session.add_all(
        [
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="older",
                payload_json={"data": {"session_id": 41}},
                workline_session_id=41,
                execution_session_id=None,
                kind="INTERNAL_EVENT",
                status="PROCESSED",
                received_at=1,
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="latest",
                payload_json={"data": {"session_id": 41}},
                workline_session_id=41,
                execution_session_id=99,
                kind="INTERNAL_EVENT",
                status="RECEIVED",
                received_at=2,
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="same-execution-namespace",
                payload_json={"data": {"session_id": 99}},
                workline_session_id=99,
                execution_session_id=41,
                kind="INTERNAL_EVENT",
                status="RECEIVED",
                received_at=3,
            ),
        ]
    )
    await db_session.commit()

    result = await RuntimeInboxRepository().latest_by_workline_session_refs(
        db_session,
        workline_session_refs=[41],
    )

    assert set(result) == {41}
    assert result[41].source_event_id == "latest"
    assert result[41].workline_session_ref == 41
    assert result[41].execution_session_id == 99


@pytest.mark.asyncio
async def test_trace_query_uses_explicit_trace_column(db_session: AsyncSession) -> None:
    """trace 读取只匹配显式列，不从 canonical payload 猜测 trace 关系。"""

    db_session.add_all(
        [
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="explicit-trace",
                payload_json={"data": {}},
                trace_id="trace-explicit",
                status="PROCESSED",
                received_at=1,
            ),
            _runtime_inbox(
                provider_code="TEST",
                event_type="INTERNAL_EVENT",
                source_event_id="payload-only-trace",
                payload_json={"trace_id": "trace-explicit", "data": {}},
                trace_id=None,
                status="PROCESSED",
                received_at=2,
            ),
        ]
    )
    await db_session.commit()

    rows = await RuntimeInboxRepository().list_by_trace_id(db_session, "trace-explicit")

    assert [row.source_event_id for row in rows] == ["explicit-trace"]
