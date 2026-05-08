"""TraceQueryService 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.trace_query_service import TraceQueryService


class _ResultStub:
    def __init__(self, *, scalar: object | None = None, rows: list[object] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._rows)


def _db_with_execute_results(*results: _ResultStub) -> Any:
    return SimpleNamespace(execute=AsyncMock(side_effect=list(results)))


@pytest.fixture
def callback_log_1() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        request_id="req-1",
        trace_id="trace-1",
        callback_type="result",
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        response_status=200,
        response_time_ms=12,
    )


@pytest.fixture
def callback_log_2() -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        request_id="req-2",
        trace_id="trace-1",
        callback_type="event",
        ingress_outcome="ACCEPTED",
        failure_stage=None,
        response_status=200,
        response_time_ms=18,
    )


@pytest.fixture
def session_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        trace_id="trace-1",
        workline_id=22,
        status="RUNNING",
        current_wait_type="COMMAND_RESULT",
        awaiting_command_id=33,
        plugin_key="smt_classifier",
        contract_version="1.0",
        last_request_id="req-1",
    )


@pytest.fixture
def command_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=33,
        command_code="CMD-1",
        trace_id="trace-1",
        session_id="11",
        device_id=77,
        workline_id=22,
        status="COMPLETED",
        result="SUCCESS",
        task_type="PICK",
    )


@pytest.fixture
def outbox_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=44,
        session_id=11,
        workline_id=22,
        dispatch_key="dispatch-1",
        dispatch_type="DEVICE_COMMAND",
        target_code="ARM-01",
        status="SENT",
        created_at=1,
    )


@pytest.fixture
def inbox_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=55,
        session_id=11,
        workline_id=22,
        trace_id="trace-1",
        source_message_id="req-1",
        status="PROCESSED",
        received_at=1,
        kind="DEVICE_EVENT",
        attempt_count=0,
    )


@pytest.fixture
def failed_inbox_obj(inbox_obj: SimpleNamespace) -> SimpleNamespace:
    inbox_obj.status = "FAILED"
    inbox_obj.error_message = (
        "Unable to resolve stable business_key from payload: missing plugin business key, business_key, "
        "barcode, and event identity"
    )
    return inbox_obj


@pytest.fixture
def timeline_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=66,
        session_id=11,
        workline_id=22,
        trace_id="trace-1",
        seq_no=1,
        occurred_at=1,
        stage="DECISION",
        action_type="DECISION_MADE",
        actor_type="PLUGIN",
        actor_code="smt_classifier",
        status="SUCCESS",
        payload_json={
            "request_id": "req-1",
            "trace_id": "trace-1",
            "canonical_event_type": "SCAN_COMPLETED",
        },
    )


@pytest.fixture
def failed_outbox_obj(outbox_obj: SimpleNamespace) -> SimpleNamespace:
    outbox_obj.status = "FAILED"
    outbox_obj.last_error = "HTTP 500"
    return outbox_obj


@pytest.fixture
def service(
    callback_log_1: SimpleNamespace,
    callback_log_2: SimpleNamespace,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
) -> TraceQueryService:
    callback_repo = SimpleNamespace(
        get_by_request_id=AsyncMock(return_value=callback_log_1),
        get_by_trace_id=AsyncMock(return_value=[callback_log_1, callback_log_2]),
    )
    session_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=session_obj),
        get_by_trace_id=AsyncMock(return_value=session_obj),
    )
    command_repo = SimpleNamespace(
        get_by_command_code=AsyncMock(return_value=command_obj),
    )
    diagnostic_repo = SimpleNamespace(
        get_active_by_trace_id=AsyncMock(return_value=[]),
    )
    return TraceQueryService(
        callback_log_repo=cast("Any", callback_repo),
        session_repo=cast("Any", session_repo),
        command_repo=cast("Any", command_repo),
        diagnostic_repo=cast("Any", diagnostic_repo),
    )


@pytest.mark.asyncio
async def test_by_request_id_aggregates_full_chain(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_request_id(db, "req-1")

    assert result.trace.request_id == "req-1"
    assert result.trace.trace_id == "trace-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.commands and result.commands[0].command_code == "CMD-1"
    assert result.outboxes and result.outboxes[0].dispatch_key == "dispatch-1"
    assert result.inboxes and result.inboxes[0].source_message_id == "req-1"
    assert result.timelines and result.timelines[0].seq_no == 1
    assert result.summary["callback_logs"] == 2
    assert result.summary["timelines"] == 1
    assert any(d.extra.get("source") == "session_snapshot" for d in result.diagnostics)
    assert any(d.extra.get("source") == "timeline" for d in result.diagnostics)
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_by_trace_id_uses_trace_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_trace_id(db, "trace-1")

    assert result.trace.trace_id == "trace-1"
    assert result.trace.request_id == "req-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.summary["callback_logs"] == 2
    assert any(d.extra.get("source") == "outbox" for d in result.diagnostics)
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_by_session_id_uses_session_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_session_id(db, 11)

    assert result.trace.session_id == 11
    assert result.trace.trace_id == "trace-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.commands and result.commands[0].command_code == "CMD-1"
    assert any(d.extra.get("source") == "session_snapshot" for d in result.diagnostics)
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_by_command_code_uses_command_anchor(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_command_code(db, "CMD-1")

    assert result.trace.command_code == "CMD-1"
    assert result.trace.session_id == 11
    assert result.session is not None and result.session.id == session_obj.id
    assert any(command.command_code == "CMD-1" for command in result.commands)
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_by_dispatch_key_uses_outbox_anchor(
    callback_log_1: SimpleNamespace,
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    cast("Any", service.callback_log_repo).get_by_trace_id = AsyncMock(return_value=[callback_log_1])
    db = _db_with_execute_results(
        _ResultStub(scalar=outbox_obj),
        _ResultStub(rows=[]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_dispatch_key(db, "dispatch-1")

    assert result.trace.dispatch_key == "dispatch-1"
    assert result.trace.session_id == 11
    assert result.outboxes and result.outboxes[0].dispatch_key == "dispatch-1"
    assert any(d.extra.get("source") == "outbox" for d in result.diagnostics)
    assert db.execute.await_count == 6


@pytest.mark.asyncio
async def test_blocking_point_returns_operable_diagnostic_card(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    failed_outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[failed_outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.get_blocking_point(db, "trace-1")

    assert result.trace_id == "trace-1"
    assert result.blocking_point == "outbox"
    assert result.diagnostic_card.error_code == "OUTBOX_DISPATCH_FAILED"
    assert result.diagnostic_card.recoverability == "manual_intervention_required"
    assert result.operator_action
    assert result.evidence["outbox"]["dispatch_key"] == "dispatch-1"
    assert result.evidence["outbox"]["last_error"] == "HTTP 500"


@pytest.mark.asyncio
async def test_by_trace_id_includes_persisted_workline_diagnostics(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    diagnostic = SimpleNamespace(
        id=77,
        request_id="req-1",
        trace_id="trace-1",
        session_id=11,
        inbox_id=55,
        outbox_id=None,
        command_code=None,
        device_code="ARM01",
        workline_id=22,
        plugin_key="smt_classifier",
        diagnostic_code="SESSION_RESOLVE_FAILED",
        error_domain="SESSION",
        severity="ERROR",
        recoverability="manual_retryable",
        problem_class="SOFTWARE",
        owner="integration",
        message="Unable to resolve stable business_key from payload",
        operator_action="补齐 PkgID 后重试",
        technical_summary="SMT business_key resolver returned None",
        next_steps_json=["补齐 PkgID/PONumber/pkg_id"],
        evidence_json={"payload": {"data": {"PkgID": None}}},
    )
    cast("Any", service.diagnostic_repo).get_active_by_trace_id = AsyncMock(return_value=[diagnostic])
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.by_trace_id(db, "trace-1")

    persisted = [item for item in result.diagnostics if item.extra.get("source") == "workline_diagnostic"]
    assert persisted
    assert persisted[0].inbox_id == 55
    assert persisted[0].extra["diagnostic_code"] == "SESSION_RESOLVE_FAILED"
    assert persisted[0].extra["message"] == "Unable to resolve stable business_key from payload"
    assert result.summary["diagnostics"] == len(result.diagnostics)
    cast("Any", service.diagnostic_repo).get_active_by_trace_id.assert_awaited_once_with(db, "trace-1")


@pytest.mark.asyncio
async def test_blocking_point_reports_failed_inbox_with_persisted_diagnostic(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    failed_inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    diagnostic = SimpleNamespace(
        id=77,
        request_id="req-1",
        trace_id="trace-1",
        session_id=11,
        inbox_id=55,
        outbox_id=None,
        command_code=None,
        device_code="ARM01",
        workline_id=22,
        plugin_key="smt_classifier",
        diagnostic_code="SESSION_RESOLVE_FAILED",
        error_domain="SESSION",
        severity="ERROR",
        recoverability="manual_retryable",
        problem_class="SOFTWARE",
        owner="integration",
        message="Unable to resolve stable business_key from payload",
        operator_action="补齐 PkgID 后重试",
        technical_summary="SMT business_key resolver returned None",
        next_steps_json=["补齐 PkgID/PONumber/pkg_id"],
        evidence_json={"payload": {"data": {"PkgID": None}}},
    )
    cast("Any", service.diagnostic_repo).get_active_by_trace_id = AsyncMock(return_value=[diagnostic])
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[failed_inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.get_blocking_point(db, "trace-1")

    assert result.blocking_point == "inbox"
    assert result.diagnostic_card.error_code == "SESSION_RESOLVE_FAILED"
    assert "Unable to resolve stable business_key" in result.diagnostic_card.summary
    assert result.operator_action
    assert result.evidence["inbox"]["id"] == 55
    assert result.evidence["inbox"]["status"] == "FAILED"
    assert result.evidence["diagnostic"]["diagnostic_code"] == "SESSION_RESOLVE_FAILED"
