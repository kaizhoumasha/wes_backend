"""RuntimeInbox typed projection 的 runtime query / trace 消费合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService
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
        kind="DEVICE_EVENT",
        payload_json={"event_type": "DEVICE_RESULT", "device_code": "DEVICE-901"},
        workline_session_id=workline_session_ref,
        execution_session_id=execution_session_id,
        workline_id=81,
        device_id=901,
        trace_id=f"trace-{status.lower()}-{workline_session_ref}-{execution_session_id}",
        status=status,
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
