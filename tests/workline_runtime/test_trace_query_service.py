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


def test_failed_command_evidence_uses_trace_response_builder_projection() -> None:
    from src.app.workline.services.trace_response_builder import build_failed_command_evidence

    command = SimpleNamespace(
        id=501,
        command_code="CMD-FAILED-TRACE",
        status="FAILED",
        result="FAILED",
        error_detail={"code": "DEVICE_BUSY"},
        result_data={"observed": True},
    )

    evidence = build_failed_command_evidence(command)

    assert evidence.command_id == 501
    assert evidence.command_code == "CMD-FAILED-TRACE"
    assert evidence.status == "FAILED"
    assert evidence.result == "FAILED"
    assert evidence.error_detail == {"code": "DEVICE_BUSY"}
    assert evidence.result_data == {"observed": True}


def test_build_trace_response_includes_resource_evidence() -> None:
    """Trace 响应暴露 Phase B 保留的资源事实、当前投影和 RuntimeHold 证据。"""

    from src.app.workline.services.trace_response_builder import build_trace_response
    from src.workline_runtime.trace_context import TraceContext

    result = SimpleNamespace(
        trace=TraceContext.from_request(trace_id="trace-resource-001"),
        callback_logs=[],
        inboxes=[],
        session=None,
        sessions=[],
        commands=[],
        outboxes=[],
        dispatch_attempts=[],
        timelines=[],
        diagnostics=[],
        resource_state_events=[
            SimpleNamespace(
                id=1,
                event_type="EXCHANGE_STATUS_UPDATED",
                resource_code="external:smt:release-001:FULL_BIN_EXCHANGE",
            )
        ],
        rack_bin_mounts=[SimpleNamespace(rack_code="RACK-001", rack_slot_code="A01", bin_code="BIN-001")],
        runtime_holds=[
            SimpleNamespace(
                id=9001,
                source_reason="POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS",
                evidence_snapshot_json={"exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE"},
            )
        ],
    )

    response = build_trace_response(result)

    assert response.resource_evidence.resource_state_events[0]["event_type"] == "EXCHANGE_STATUS_UPDATED"
    assert response.resource_evidence.rack_releases == []
    assert response.resource_evidence.rack_release_bin_snapshots == []
    assert response.resource_evidence.wms_writeback_evidence == []
    assert response.resource_evidence.rack_bin_mounts[0]["bin_code"] == "BIN-001"
    assert response.resource_evidence.runtime_holds[0]["source_reason"] == (
        "POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS"
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
        plugin_key="test_workline_plugin",
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
def resource_state_event_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=89,
        event_type="EXCHANGE_STATUS_UPDATED",
        resource_type="BIN",
        resource_code="external:smt:release-001:FULL_BIN_EXCHANGE",
        source_event_id="wms-event-001",
        trace_id="trace-1",
    )


@pytest.fixture
def rack_release_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=90,
        rack_release_id="release-001",
        single_layer_rack_code="RACK-001",
        trace_id="trace-1",
    )


@pytest.fixture
def rack_release_snapshot_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=91,
        rack_release_id="release-001",
        slot_code="A01",
        bin_code="BIN-001",
    )


@pytest.fixture
def wms_evidence_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=92,
        evidence_code="wms-confirmed-001",
        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
        trace_id="trace-1",
    )


@pytest.fixture
def rack_bin_mount_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=93,
        rack_code="RACK-001",
        rack_slot_code="A01",
        bin_code="BIN-001",
        source_event_id="wms-event-001",
        trace_id="trace-1",
    )


@pytest.fixture
def runtime_hold_obj() -> SimpleNamespace:
    return SimpleNamespace(
        id=9001,
        trace_id="trace-1",
        source_kind="RESOURCE_RECONCILIATION",
        source_reason="POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS",
        source_idempotency_key="resource-reconciliation:POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS:wms-event-001",
        evidence_snapshot_json={"exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE"},
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
        actor_code="test_workline_plugin",
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
async def test_by_exchange_request_code_aggregates_runtime_and_resource_evidence(
    callback_log_1: SimpleNamespace,
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
    resource_state_event_obj: SimpleNamespace,
    rack_bin_mount_obj: SimpleNamespace,
    runtime_hold_obj: SimpleNamespace,
) -> None:
    """从 exchange_request_code 能追到运行时链路和 Phase B 保留的资源证据链。"""

    exchange_request_code = "external:smt:release-001:FULL_BIN_EXCHANGE"
    outbox_obj.dispatch_key = exchange_request_code
    cast("Any", service.callback_log_repo).get_by_trace_id = AsyncMock(return_value=[callback_log_1])
    db = _db_with_execute_results(
        _ResultStub(scalar=outbox_obj),
        _ResultStub(rows=[]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
        _ResultStub(rows=[resource_state_event_obj]),
        _ResultStub(rows=[rack_bin_mount_obj]),
        _ResultStub(rows=[runtime_hold_obj]),
    )

    result = await service.by_exchange_request_code(db, exchange_request_code)

    assert result.trace.dispatch_key == exchange_request_code
    assert result.trace.session_id == 11
    assert result.outboxes and result.outboxes[0].dispatch_key == exchange_request_code
    assert result.resource_state_events == [resource_state_event_obj]
    assert not hasattr(result, "wms_writeback_evidence")
    assert not hasattr(result, "rack_releases")
    assert not hasattr(result, "rack_release_bin_snapshots")
    assert result.rack_bin_mounts == [rack_bin_mount_obj]
    assert result.runtime_holds == [runtime_hold_obj]
    assert db.execute.await_count == 9


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
        plugin_key="test_workline_plugin",
        diagnostic_code="SESSION_RESOLVE_FAILED",
        error_domain="SESSION",
        severity="ERROR",
        recoverability="manual_retryable",
        problem_class="SOFTWARE",
        owner="integration",
        message="Unable to resolve stable business_key from payload",
        operator_action="补齐 item_id 后重试",
        technical_summary="test plugin business_key resolver returned None",
        next_steps_json=["补齐 item_id"],
        evidence_json={"payload": {"data": {"item_id": None}}},
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
        plugin_key="test_workline_plugin",
        diagnostic_code="SESSION_RESOLVE_FAILED",
        error_domain="SESSION",
        severity="ERROR",
        recoverability="manual_retryable",
        problem_class="SOFTWARE",
        owner="integration",
        message="Unable to resolve stable business_key from payload",
        operator_action="补齐 item_id 后重试",
        technical_summary="test plugin business_key resolver returned None",
        next_steps_json=["补齐 item_id"],
        evidence_json={"payload": {"data": {"item_id": None}}},
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
