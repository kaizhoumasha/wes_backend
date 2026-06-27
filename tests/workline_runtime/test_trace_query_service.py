"""TraceQueryService 测试。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.trace_query_service import TraceQueryResult, TraceQueryService
from src.utils.timezone import timezone
from src.workline_runtime.trace_context import TraceContext


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


def _diagnosis_item(verdict: Any, key: str) -> Any:
    return next(item for item in verdict.evidence_health.items if item.key == key)


def _session_namespace(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 1,
        "session_code": "SESSION-1",
        "trace_id": "trace-1",
        "workline_id": 22,
        "plugin_key": "test_workline_plugin",
        "run_mode": "SIMULATION",
        "business_key": None,
        "barcode": None,
        "status": "RUNNING",
        "started_at": None,
        "ended_at": None,
        "current_wait_type": None,
        "current_wait_timeout_seconds": None,
        "waiting_since": None,
        "deadline_at": None,
        "awaiting_device_command_code": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
        "ingress_count": 0,
        "last_request_id": None,
        "last_ingress_at": None,
        "last_inbox_id": None,
        "context_json": {},
        "contract_version": "1.0",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def test_trace_detail_response_uses_sessions_without_duplicate_session_field() -> None:
    from src.app.workline.models.runtime import TraceDetailResponse
    from src.app.workline.services.trace_response_builder import build_trace_response

    session = _session_namespace(id=21, session_code="SESSION-21", trace_id="trace-session-contract")
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="trace-session-contract").with_session(session),
        session=session,
        sessions=[session],
    )

    response = build_trace_response(result)
    payload = response.model_dump(mode="json")

    assert "session" not in TraceDetailResponse.model_fields
    assert "session" not in payload
    assert payload["sessions"][0]["id"] == 21
    assert payload["sessions"][0]["session_code"] == "SESSION-21"


def test_trace_detail_includes_completed_clear_diagnosis_verdict() -> None:
    from src.app.workline.services.trace_response_builder import build_trace_response

    session = _session_namespace(
        id=1,
        session_code="SEED-ROUGH-SORTER-ACTIVE-RACK-TEMPLATE",
        trace_id="seed-rough-sorter-resource",
        status="COMPLETED",
    )
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="seed-rough-sorter-resource").with_session(session),
        session=session,
        sessions=[session],
    )

    response = build_trace_response(result)
    verdict = response.diagnosis_verdict

    assert verdict.state == "completed_clear"
    assert verdict.severity == "success"
    assert verdict.requires_operator_action is False
    assert verdict.primary_action == "无需现场处置"
    assert verdict.blocking_point == "none"
    assert verdict.evidence_health.level == "complete"
    assert _diagnosis_item(verdict, "session").state == "present"
    assert _diagnosis_item(verdict, "inbox").state == "not_required"


def test_diagnosis_verdict_reports_resource_wait_before_failed_outbox_semantics() -> None:
    from src.app.workline.services.trace_response_builder import build_trace_response

    outbox = SimpleNamespace(
        id=45,
        session_id=11,
        workline_id=22,
        dispatch_key="dispatch-blocked",
        dispatch_type="DEVICE_COMMAND",
        target_type="DEVICE",
        target_code="ARM-01",
        status="BLOCKED_RESOURCE",
        attempt_count=1,
        next_retry_at=None,
        last_error="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=timezone.now_for_db(),
        blocked_wait_seconds=12,
        blocked_check_count=3,
        blocked_detail_json={"device_code": "ARM-01", "last_probe_result": "STATUS_WAIT"},
        created_at=timezone.now_for_db(),
        sent_at=None,
        finished_at=None,
        payload_json={},
    )
    result = TraceQueryResult(trace=TraceContext.from_request(trace_id="trace-resource-wait"), outboxes=[outbox])

    verdict = build_trace_response(result).diagnosis_verdict

    assert verdict.state == "waiting"
    assert verdict.requires_operator_action is False
    assert verdict.blocking_point == "resource"
    assert verdict.owner == "device"
    assert "ARM-01" in (verdict.primary_action or "")
    assert _diagnosis_item(verdict, "resource_wait").state == "present"
    assert _diagnosis_item(verdict, "outbox").state == "present"


def test_diagnosis_verdict_reports_workline_start_admission_wait() -> None:
    outbox = SimpleNamespace(
        id=46,
        session_id=None,
        workline_id=22,
        dispatch_key="dispatch-start-wait",
        dispatch_type="DEVICE_COMMAND",
        target_type="DEVICE",
        target_code="ARM-01",
        status="BLOCKED_RESOURCE",
        last_error="WORKLINE_STOPPED_WAITING_START",
        blocked_reason="WORKLINE_STOPPED_WAITING_START",
        blocked_detail_json={},
    )
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="trace-admission-wait"),
        outboxes=[outbox],
        workline_runtime_status="STOPPED",
        workline_start_admission_status="CHECKING",
        workline_start_admission_message="等待 START 准入恢复",
    )

    verdict = TraceQueryService().build_diagnosis_verdict(result)

    assert verdict.state == "waiting"
    assert verdict.requires_operator_action is False
    assert verdict.blocking_point == "admission"
    assert verdict.owner == "workline"
    assert "START" in (verdict.primary_action or "")
    assert _diagnosis_item(verdict, "workline_admission").state == "present"


def test_trace_detail_and_blocking_point_share_completed_clear_verdict() -> None:
    from src.app.workline.services.trace_response_builder import build_trace_response

    session = _session_namespace(
        id=1,
        session_code="SESSION-COMPLETED",
        trace_id="trace-completed",
        status="COMPLETED",
    )
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="trace-completed").with_session(session),
        session=session,
        sessions=[session],
        workline_runtime_status="STOPPED",
        workline_start_admission_status="FAILED",
        workline_start_admission_message="上一次 START 准入失败不应覆盖已完成案件",
        workline_start_admission_failed_device_code="ARM-01",
    )
    service = TraceQueryService()

    detail_verdict = build_trace_response(result).diagnosis_verdict
    blocking = service._build_blocking_point(result, trace_id="trace-completed")

    assert blocking.diagnosis_verdict == detail_verdict
    assert blocking.blocking_point == "none"
    assert blocking.operator_action == "无需现场处置"
    assert blocking.diagnostic_card.error_code != "UNKNOWN"
    assert "未发现阻塞点" in blocking.diagnostic_card.summary


def test_unknown_verdict_uses_insufficient_diagnosis_wording() -> None:
    result = TraceQueryResult(trace=TraceContext.from_request(trace_id="trace-unknown"))

    blocking = TraceQueryService()._build_blocking_point(result, trace_id="trace-unknown")

    assert blocking.diagnosis_verdict.state == "unknown"
    assert blocking.diagnosis_verdict.requires_operator_action is False
    assert blocking.blocking_point == "unknown"
    assert blocking.operator_action == "补齐会话、Timeline、Inbox、Command、Outbox 证据后重新诊断"
    assert blocking.diagnostic_card.title == "诊断不足"
    assert "诊断不足" in blocking.diagnostic_card.summary


def test_session_only_manual_hold_uses_blocked_session_verdict() -> None:
    session = _session_namespace(
        status="MANUAL_HOLD",
        failure_domain="WORKFLOW",
        failure_code="WMS_UNAVAILABLE",
        failure_message="WMS 依赖不可用",
    )
    result = TraceQueryResult(
        trace=TraceContext.from_request(trace_id="trace-manual-hold").with_session(session),
        session=session,
        sessions=[session],
    )

    blocking = TraceQueryService()._build_blocking_point(result, trace_id="trace-manual-hold")

    assert blocking.diagnosis_verdict.state == "blocked"
    assert blocking.diagnosis_verdict.blocking_point == "session"
    assert blocking.blocking_point == "session"
    assert blocking.diagnostic_card.error_code != "UNKNOWN"


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
        awaiting_device_command_code=33,
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


def test_trace_outbox_includes_resource_wait_diagnostics() -> None:
    from src.app.workline.services.trace_response_builder import build_trace_response
    from src.workline_runtime.trace_context import TraceContext

    blocked_at = timezone.now_for_db() - timedelta(seconds=15)
    last_check_at = timezone.now_for_db() - timedelta(seconds=5)
    outbox = SimpleNamespace(
        id=45,
        session_id=11,
        workline_id=22,
        dispatch_key="dispatch-blocked",
        dispatch_type="DEVICE_COMMAND",
        target_type="DEVICE",
        target_code="ARM-01",
        status="BLOCKED_RESOURCE",
        attempt_count=1,
        next_retry_at=None,
        last_error="设备 ARM-01 实时状态查询返回 HTTP 503，等待下次预检",
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=77,
        blocked_workline_id=22,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=blocked_at,
        last_blocked_check_at=last_check_at,
        blocked_check_count=3,
        blocked_detail_json={
            "device_code": "ARM-01",
            "status_url": "http://mock-ecs.internal:8010/api/v1/device/status?api_key=secret&device_code=ARM-01",
            "http_status": 503,
            "last_probe_result": "STATUS_WAIT",
            "raw_vendor_response": {"large": "should-not-leak"},
        },
        created_at=blocked_at,
        sent_at=None,
        finished_at=last_check_at,
        payload_json={"command_code": "CMD-BLOCKED-001"},
        blocked_location_code="SHOULD-NOT-EXIST",
        blocked_owner_session_id=999,
    )
    result = SimpleNamespace(
        trace=TraceContext.from_request(trace_id="trace-blocked"),
        callback_logs=[],
        inboxes=[],
        session=None,
        sessions=[],
        commands=[],
        outboxes=[outbox],
        dispatch_attempts=[],
        timelines=[],
        diagnostics=[],
        resource_state_events=[],
        rack_bin_mounts=[],
        rack_releases=[],
        rack_release_bin_snapshots=[],
        wms_writeback_evidence=[],
        runtime_holds=[],
    )

    response = build_trace_response(result)
    item = response.outboxes[0]
    data = item.model_dump()

    assert item.blocked_at == blocked_at
    assert item.last_blocked_check_at == last_check_at
    assert item.blocked_wait_seconds is not None and item.blocked_wait_seconds >= 14
    assert item.blocked_check_count == 3
    assert item.blocked_detail_json == {
        "device_code": "ARM-01",
        "status_url": "/api/v1/device/status?device_code=ARM-01",
        "http_status": 503,
        "last_probe_result": "STATUS_WAIT",
    }
    assert "blocked_location_code" not in data
    assert "blocked_owner_session_id" not in data


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
        get_summary_by_trace_id=AsyncMock(return_value=[callback_log_1, callback_log_2]),
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
    service = TraceQueryService(
        callback_log_repo=cast("Any", callback_repo),
        session_repo=cast("Any", session_repo),
        command_repo=cast("Any", command_repo),
        diagnostic_repo=cast("Any", diagnostic_repo),
    )
    service.workline_repo = cast(
        "Any",
        SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(
                    id=22,
                    runtime_status="STOPPED",
                    start_admission_status="FAILED",
                    start_admission_message="设备 ARM-01 非 AUTO/IDLE，等待 START 准入恢复",
                    start_admission_failed_device_code="ARM-01",
                    start_admission_checked_at=timezone.now_for_db(),
                    last_start_request_id="start-req-1",
                    last_start_trace_id="start-trace-1",
                )
            )
        ),
    )
    return service


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
    assert result.workline_runtime_status == "STOPPED"
    assert result.workline_start_admission_status == "FAILED"
    assert result.workline_start_admission_failed_device_code == "ARM-01"
    assert result.workline_last_start_request_id == "start-req-1"
    assert db.execute.await_count == 5
    cast("Any", service.workline_repo).get_by_id.assert_awaited_with(db, 22)


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
async def test_path_query_by_trace_id_loads_slim_runtime_facts_without_dispatch_attempts(
    service: TraceQueryService,
    callback_log_1: SimpleNamespace,
    callback_log_2: SimpleNamespace,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.path_by_trace_id(db, "trace-1")

    assert result.trace.trace_id == "trace-1"
    assert result.callback_logs == [callback_log_1, callback_log_2]
    assert result.session is not None and result.session.id == session_obj.id
    assert result.sessions == [session_obj]
    assert result.commands == [command_obj]
    assert result.outboxes == [outbox_obj]
    assert result.inboxes == [inbox_obj]
    assert result.timelines == [timeline_obj]
    assert result.dispatch_attempts == []
    assert result.resource_state_events == []
    assert result.rack_bin_mounts == []
    assert result.runtime_holds == []
    assert result.workline_start_admission_status == "FAILED"
    cast("Any", service.callback_log_repo).get_summary_by_trace_id.assert_awaited_once_with(db, "trace-1")
    cast("Any", service.callback_log_repo).get_by_trace_id.assert_not_awaited()
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_path_query_by_trace_id_loads_commands_and_outboxes_without_session(
    service: TraceQueryService,
    callback_log_1: SimpleNamespace,
    callback_log_2: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    cast("Any", service.session_repo).get_by_trace_id.return_value = None
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.path_by_trace_id(db, "trace-1")

    assert result.trace.trace_id == "trace-1"
    assert result.callback_logs == [callback_log_1, callback_log_2]
    assert result.session is None
    assert result.sessions == []
    assert result.commands == [command_obj]
    assert result.outboxes == [outbox_obj]
    assert result.inboxes == [inbox_obj]
    assert result.timelines == [timeline_obj]
    assert result.dispatch_attempts == []
    assert any(d.extra.get("source") == "command" for d in result.diagnostics)
    assert any(d.extra.get("source") == "outbox" for d in result.diagnostics)
    assert db.execute.await_count == 4


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
async def test_path_query_by_session_id_loads_slim_runtime_facts_without_dispatch_attempts(
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
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.path_by_session_id(db, 11)

    assert result.trace.session_id == 11
    assert result.trace.trace_id == "trace-1"
    assert result.session is not None and result.session.id == session_obj.id
    assert result.sessions == [session_obj]
    assert result.commands == [command_obj]
    assert result.outboxes == [outbox_obj]
    assert result.inboxes == [inbox_obj]
    assert result.timelines == [timeline_obj]
    assert result.dispatch_attempts == []
    assert result.summary["dispatch_attempts"] == 0
    assert db.execute.await_count == 4


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
async def test_blocking_point_reports_manual_hold_wms_timeout(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    """设备链路完成后进入 MANUAL_HOLD 时，应直接指向 WMS 超时而不是 UNKNOWN。"""

    session_obj.status = "MANUAL_HOLD"
    session_obj.failure_domain = "INTEGRATION"
    session_obj.failure_code = "WMS_TIMEOUT"
    session_obj.failure_message = "WMS 同步调用超时"
    command_obj.status = "COMPLETED"
    command_obj.ack_received_at = 1
    command_obj.completed_at = 2
    timeline_obj.status = "BLOCKED"
    timeline_obj.action_type = "MANUAL_HOLD_CREATED"
    timeline_obj.failure_domain = "INTEGRATION"
    timeline_obj.message = "WMS 同步调用超时"
    timeline_obj.payload_json = {
        "request_id": "rough-sorter:inventory:39222b903aa0f149",
        "trace_id": "trace-1",
        "block_scope": "MATERIAL",
        "reason_code": "WMS_TIMEOUT",
        "target_code": "WMS_INVENTORY",
        "suggested_action": "人工检查粗分机当前物料与依赖状态",
    }
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.get_blocking_point(db, "trace-1")

    assert result.blocking_point == "external_wms"
    assert result.owner == "integration"
    assert result.diagnostic_card.error_code == "WMS_TIMEOUT"
    assert "WMS 同步调用超时" in result.diagnostic_card.summary
    assert result.evidence["session"]["status"] == "MANUAL_HOLD"
    assert result.evidence["timeline"]["reason_code"] == "WMS_TIMEOUT"
    assert result.evidence["timeline"]["target_code"] == "WMS_INVENTORY"
    assert result.evidence["command_chain"]["completed_commands"] == ["CMD-1"]


@pytest.mark.asyncio
async def test_blocking_point_does_not_mask_non_timeout_manual_hold_as_wms_timeout(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    session_obj.status = "MANUAL_HOLD"
    session_obj.failure_domain = "INTEGRATION"
    session_obj.failure_code = "WMS_UNAVAILABLE"
    session_obj.failure_message = "WMS 依赖不可用"
    command_obj.status = "COMPLETED"
    command_obj.ack_received_at = 1
    command_obj.completed_at = 2
    timeline_obj.status = "BLOCKED"
    timeline_obj.action_type = "MANUAL_HOLD_CREATED"
    timeline_obj.failure_domain = "INTEGRATION"
    timeline_obj.message = "WMS 依赖不可用"
    timeline_obj.payload_json = {
        "request_id": "rough-sorter:inventory:39222b903aa0f149",
        "trace_id": "trace-1",
        "block_scope": "MATERIAL",
        "reason_code": "WMS_UNAVAILABLE",
        "target_code": "WMS_INVENTORY",
    }
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.get_blocking_point(db, "trace-1")

    assert result.blocking_point != "external_wms"
    assert result.diagnostic_card.error_code != "WMS_TIMEOUT"
    assert result.blocking_point == "session"
    assert result.diagnosis_verdict.state == "blocked"
    assert result.diagnosis_verdict.blocking_point == "session"
    assert result.diagnostic_card.error_code != "UNKNOWN"
    assert result.operator_action != "联系技术支持"


@pytest.mark.asyncio
async def test_blocking_point_reports_timeout_command_as_command_failure(
    service: TraceQueryService,
    session_obj: SimpleNamespace,
    command_obj: SimpleNamespace,
    outbox_obj: SimpleNamespace,
    inbox_obj: SimpleNamespace,
    timeline_obj: SimpleNamespace,
) -> None:
    command_obj.status = "TIMEOUT"
    command_obj.error_detail = {"message": "device did not respond"}
    db = _db_with_execute_results(
        _ResultStub(rows=[command_obj]),
        _ResultStub(rows=[outbox_obj]),
        _ResultStub(rows=[]),
        _ResultStub(rows=[inbox_obj]),
        _ResultStub(rows=[timeline_obj]),
    )

    result = await service.get_blocking_point(db, "trace-1")

    assert result.blocking_point == "command"
    assert result.diagnosis_verdict.state == "failed"
    assert result.diagnosis_verdict.blocking_point == "command"
    assert result.diagnostic_card.error_code == "DEVICE_TIMEOUT"
    assert result.evidence["command"]["status"] == "TIMEOUT"


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
