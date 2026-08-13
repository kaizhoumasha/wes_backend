"""RuntimeInbox typed projection 的 runtime query / trace 消费合同。"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.app.contracts.runtime_inbox_query import RuntimeInboxProjection
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.query.runtime_query_service import (
    RuntimeQueryService,
    _device_session_clause,
)
from src.app.runtime.orchestration.services.trace.trace_query_service import TraceQueryResult, TraceQueryService
from src.app.workline.trace_context import TraceContext


async def _persist_projection(
    db_session,
    *,
    status: str = "FAILED",
    workline_session_ref: int | None = 701,
    execution_session_id: int | None = 9701,
):
    inbox = RuntimeInbox(
        provider_code="TEST",
        event_type=f"PROJECTION_{status}",
        source_event_id=f"projection:{status}:{workline_session_ref}:{execution_session_id}",
        payload_hash=f"hash-{status}-{workline_session_ref}-{execution_session_id}",
        kind="INTERNAL_EVENT",
        payload_json={"event_type": "DEVICE_RESULT", "device_code": "DEVICE-901"},
        payload_schema_version=1,
        workline_session_id=workline_session_ref,
        execution_session_id=execution_session_id,
        workline_id=81,
        device_id=901,
        trace_id=f"trace-{status.lower()}-{workline_session_ref}-{execution_session_id}",
        status=status,
        claim_bucket_key=f"workline-session:{workline_session_ref}",
        received_at=1,
        last_error_code="INBOX_RETRY_EXHAUSTED" if status == "DEAD_LETTER" else "SESSION_RESOLVE_FAILED",
        last_error_message=f"{status} 原始错误证据",
    )
    db_session.add(inbox)
    await db_session.flush()
    projections = await RuntimeInboxRepository().list_by_trace_id(db_session, inbox.trace_id)
    assert len(projections) == 1
    return projections[0]


@pytest.mark.asyncio
async def test_runtime_trace_path_reads_device_projection_last_error_without_attribute_error(db_session) -> None:
    projection = await _persist_projection(db_session)
    result = TraceQueryResult(trace=TraceContext.from_request(trace_id=projection.trace_id), inboxes=[projection])

    response = RuntimeQueryService()._build_trace_path(result)

    assert response.devices[0].device_id == 901
    assert response.devices[0].actions[0].message == "FAILED 原始错误证据"
    assert response.diagnosis_verdict.summary == "FAILED 原始错误证据"


@pytest.mark.parametrize("status", ["FAILED", "DEAD_LETTER"])
@pytest.mark.asyncio
async def test_trace_blocking_point_preserves_projection_error_evidence(db_session, status: str) -> None:
    projection = await _persist_projection(db_session, status=status)
    service = TraceQueryService()
    trace = TraceContext.from_request(trace_id=projection.trace_id)
    diagnostics = service._diagnostic_for_inboxes(trace, [projection])
    result = TraceQueryResult(trace=trace.with_inbox(projection), inboxes=[projection], diagnostics=diagnostics)

    response = service._build_blocking_point(result, trace_id=projection.trace_id or "missing")

    assert diagnostics[0].extra["last_error_code"] == projection.last_error_code
    assert diagnostics[0].extra["last_error_message"] == projection.last_error_message
    assert response.diagnosis_verdict.summary == projection.last_error_message
    assert response.diagnostic_card.summary == projection.last_error_message
    assert response.evidence["inbox"]["last_error_code"] == projection.last_error_code
    assert response.evidence["inbox"]["last_error_message"] == projection.last_error_message


@pytest.mark.asyncio
async def test_trace_context_propagates_only_workline_session_ref_from_projection(db_session) -> None:
    projection = await _persist_projection(db_session, workline_session_ref=701, execution_session_id=701)
    trace = TraceContext.from_request().with_inbox(projection)

    assert trace.session_id == 701

    execution_only = await _persist_projection(db_session, workline_session_ref=None, execution_session_id=702)
    assert TraceContext.from_request().with_inbox(execution_only).session_id is None
    assert TraceContext.from_request().with_inbox(SimpleNamespace(session_id=703)).session_id == 703


def test_device_filter_compiles_correlated_epoch_binding_exists_without_materialized_id_list() -> None:
    """设备过滤通过冻结绑定关联命令 trace，保持单 SQL correlated EXISTS。"""

    session_columns = WorklineSession.__table__.c
    statement = select(session_columns.id).where(_device_session_clause(session_columns, 901))

    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "EXISTS" in sql
    assert "line_run_epoch_device_bindings.device_id = 901" in sql
    assert "device_commands.trace_id = wes_biz.workline_sessions.trace_id" in sql
    assert "workline_sessions.id IN" not in sql


@pytest.mark.asyncio
async def test_active_session_device_filter_executes_one_statement_with_epoch_binding_exists() -> None:
    """查询服务只提交一条按冻结绑定关联 DeviceCommand 的 SQL。"""

    statements: list[object] = []

    class _Result:
        def scalars(self) -> _Result:
            return self

        def all(self) -> list[object]:
            return []

    class _Db:
        async def execute(self, statement: object) -> _Result:
            statements.append(statement)
            return _Result()

    service = RuntimeQueryService(inbox_query=RuntimeInboxRepository())
    sessions = await service._load_active_sessions_for_device(_Db(), device_id=901, limit=200)

    assert sessions == []
    assert len(statements) == 1
    sql = str(
        statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
    )
    assert "EXISTS" in sql
    assert "device_commands.trace_id = wes_biz.workline_sessions.trace_id" in sql
    assert "workline_sessions.id IN" not in sql


def test_runtime_inbox_projection_declares_payload_as_read_only_mapping() -> None:
    """冻结外壳的 payload 合同使用 Mapping，避免类型层暗示 dict 可写。"""

    assert get_type_hints(RuntimeInboxProjection)["payload_json"].__origin__ is Mapping


@pytest.mark.asyncio
async def test_runtime_inbox_projection_payload_is_deep_copy_isolated_from_orm(db_session) -> None:
    """修改 DTO payload 快照不得回写 ORM JSON。"""

    inbox = RuntimeInbox(
        provider_code="TEST",
        event_type="PROJECTION_ISOLATION",
        source_event_id="projection-isolation",
        payload_hash="hash-projection-isolation",
        kind="INTERNAL_EVENT",
        payload_json={"data": {"nested": "original"}},
        payload_schema_version=1,
        status="RECEIVED",
        claim_bucket_key="source:projection-isolation",
        received_at=1,
    )
    db_session.add(inbox)
    await db_session.flush()

    projection = RuntimeInboxRepository()._to_projection(inbox)
    projection.payload_json["data"]["nested"] = "changed"  # type: ignore[index]

    assert inbox.payload_json == {"data": {"nested": "original"}}
