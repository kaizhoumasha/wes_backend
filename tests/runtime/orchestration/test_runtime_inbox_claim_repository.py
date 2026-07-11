"""RuntimeInbox 原子限量 claim 仓储契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository import (
    RuntimeInboxClaimRepository,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _StatementCaptured(Exception):
    def __init__(self, statement: Any) -> None:
        self.statement = statement


class _CapturingDb:
    async def execute(self, statement: Any) -> None:
        raise _StatementCaptured(statement)


@pytest.mark.asyncio
async def test_claim_compiles_postgresql_atomic_fifo_limit_with_skip_locked() -> None:
    """limit/FIFO/同桶队首围栏必须全部进入单条 PostgreSQL UPDATE。"""

    repository = RuntimeInboxClaimRepository()

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
    assert "RETURNING" in sql

    bound_types = {type(bind.type).__name__ for bind in captured.value.statement.compile().binds.values()}
    assert "BigInteger" in bound_types


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
            "event_type": "DEVICE_EVENT",
            "source_event_id": "event-1",
            "payload_json": {"event_type": "SCAN_COMPLETED"},
            "correlation_id": None,
            "execution_session_id": 10,
            "workline_id": 20,
            "device_id": 30,
            "command_id": None,
            "kind": "DEVICE_EVENT",
            "trace_id": "trace-1",
            "event_id": "event-1",
            "causation_id": None,
            "received_at": 100,
        }
    ]
    db = _RecordingDb(rows)

    claims = await RuntimeInboxClaimRepository().claim_received_with_token(
        db,  # type: ignore[arg-type]
        limit=1,
        processor_token="worker-1",
        stale_after_seconds=30,
    )

    assert len(db.statements) == 1
    assert claims == [rows[0] | {"payload_json": {"event_type": "SCAN_COMPLETED"}}]


@pytest.mark.asyncio
async def test_legacy_terminal_failed_rows_do_not_block_bucket_head(db_session: AsyncSession) -> None:
    """无推进路径的 legacy FAILED 不能永久阻塞同 bucket 后续消息。"""

    rows = [
        RuntimeInbox(
            provider_code="TEST",
            event_type="DEVICE_EVENT",
            source_event_id="legacy-exhausted",
            payload_json={},
            status="FAILED",
            claim_bucket_key="bucket-legacy",
            received_at=1,
            attempt_count=3,
            max_retries=3,
            next_retry_at=0,
        ),
        RuntimeInbox(
            provider_code="TEST",
            event_type="DEVICE_EVENT",
            source_event_id="legacy-nonretry",
            payload_json={},
            status="FAILED",
            claim_bucket_key="bucket-legacy",
            received_at=2,
            attempt_count=1,
            max_retries=3,
            next_retry_at=None,
        ),
        RuntimeInbox(
            provider_code="TEST",
            event_type="DEVICE_EVENT",
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

    claims = await RuntimeInboxClaimRepository().claim_received_with_token(
        db_session,
        limit=1,
        processor_token="worker-current",
        stale_after_seconds=30,
    )

    assert [claim["source_event_id"] for claim in claims] == ["current-received"]
