from typing import Any, cast

import pytest
from sqlalchemy import select

from src.app.device.models import CommandResult, CommandStatus, Device, DeviceCommand
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.inbox import InboxKind, WorklineInbox
from src.app.workline.models.runtime_hold import (
    MaterialDisposition,
    NgReasonSource,
    NgReturnItem,
    NgReturnItemStatus,
    RuntimeHold,
    RuntimeHoldStatus,
    RuntimeHoldType,
)
from src.app.workline.models.runtime_hold_api import (
    NgReasonInput,
    PhysicalHandoffEvidenceInput,
    ResolveRuntimeHoldRequest,
)
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationResolution,
    RuntimeReconciliationState,
    SessionStatus,
    WorklineSession,
)
from src.app.workline.services.runtime_hold_release_service import RuntimeHoldReleaseError, RuntimeHoldReleaseService

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("registered_test_workline_plugin")]


async def _create_workline(db_session, *, code: str = "WL-HOLD-001") -> WorkLine:
    workline = WorkLine(
        line_code=code,
        line_name=code,
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        runtime_config_json={"runtime_hold": {"ng_locations": [{"code": "NG-01", "label": "NG 暂存位 01"}]}},
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        stopped_reason="RUNTIME_HOLD",
    )
    db_session.add(workline)
    await db_session.flush()
    return workline


async def _create_session(
    db_session,
    workline: WorkLine,
    *,
    code: str = "S-HOLD-001",
    context: dict[str, Any] | None = None,
) -> WorklineSession:
    session = WorklineSession(
        session_code=code,
        workline_id=cast("int", workline.id),
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        status=SessionStatus.MANUAL_HOLD,
        context_json=context or {},
    )
    db_session.add(session)
    await db_session.flush()
    return session


async def _create_hold(
    db_session,
    workline: WorkLine,
    *,
    session: WorklineSession | None = None,
    key: str = "hold:1",
    evidence: dict[str, Any] | None = None,
    hold_type: RuntimeHoldType = RuntimeHoldType.RUNTIME_RECONCILIATION,
) -> RuntimeHold:
    hold = RuntimeHold(
        hold_type=hold_type,
        workline_id=cast("int", workline.id),
        session_id=cast("int", session.id) if session is not None else None,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key=key,
        evidence_snapshot_json=evidence if evidence is not None else {"item_id": "ITEM-001"},
    )
    db_session.add(hold)
    await db_session.flush()
    return hold


def _continue_request(service: RuntimeHoldReleaseService, hold: RuntimeHold) -> ResolveRuntimeHoldRequest:
    return ResolveRuntimeHoldRequest(
        resolution="COMPLETED",
        checks={"line_checked": True},
        operator_note="现场确认继续生产",
        material_disposition="CONTINUE",
        hold_version=hold.version,
        latest_evidence_hash=service.build_latest_evidence_hash(hold),
    )


def _return_to_ng_request(service: RuntimeHoldReleaseService, hold: RuntimeHold) -> ResolveRuntimeHoldRequest:
    return ResolveRuntimeHoldRequest(
        resolution="FAILED",
        checks={"line_clear_checked": True, "late_callback_reviewed": True},
        operator_note="物料转 NG 返修",
        material_disposition="RETURN_TO_NG",
        ng_reason=NgReasonInput(source="PLUGIN", code="SCAN_NG", label="扫码异常"),
        physical_handoff_evidence=PhysicalHandoffEvidenceInput(
            ng_location_code="NG-01",
            ng_location_scan="NG-01",
            material_scan_payload={"item_id": "ITEM-001"},
            line_clear_checked=True,
            late_callback_reviewed=True,
        ),
        hold_version=hold.version,
        latest_evidence_hash=service.build_latest_evidence_hash(hold),
    )


async def _ng_item_count(db_session, hold_id: int) -> int:
    result = await db_session.execute(
        select(NgReturnItem).where(cast("Any", NgReturnItem.created_from_runtime_hold_id) == hold_id)
    )
    return len(list(result.scalars().all()))


async def test_continue_resolves_hold_without_ng_item(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session)
    session = await _create_session(db_session, workline)
    hold = await _create_hold(db_session, workline, session=session)

    request = _continue_request(service, hold).model_copy(
        update={"latest_evidence_hash": service.build_latest_evidence_hash(hold, session=session)}
    )

    result = await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(workline)
    await db_session.refresh(hold)
    await db_session.refresh(session)
    assert result["status"] == RuntimeHoldStatus.RESOLVED.value
    assert hold.material_disposition == MaterialDisposition.CONTINUE
    assert hold.version == 1
    assert await _ng_item_count(db_session, cast("int", hold.id)) == 0
    assert session.status == SessionStatus.COMPLETED
    assert result["workline_runtime_status"] == WorkLineRuntimeStatus.STOPPED.value
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED


async def test_continue_for_command_backed_hold_replays_command_result_instead_of_terminalizing_session(
    db_session,
) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-CONTINUE-REPLAY")
    workline.plugin_key = "rough_sorter"
    workline.contract_version = "rough_sorter.v1"
    device = Device(
        device_code="ARM03-HOLD-CONTINUE-REPLAY",
        device_name="ARM03",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=3,
    )
    db_session.add(device)
    await db_session.flush()
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-CONTINUE-REPLAY",
        context={"item_id": "ITEM-001"},
    )
    session.plugin_key = workline.plugin_key
    session.contract_version = workline.contract_version
    session.reconciliation_state = RuntimeReconciliationState.PENDING
    session.reconciliation_reason = RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED
    command = DeviceCommand(
        command_code="CMD-HOLD-CONTINUE-REPLAY",
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="PICK_AND_PUT",
        params={"item_id": "ITEM-001"},
        status=CommandStatus.FAILED,
        trace_id="trace-hold-continue-replay",
    )
    db_session.add(command)
    await db_session.flush()
    hold = await _create_hold(
        db_session,
        workline,
        session=session,
        key="hold:continue-replay",
    )
    hold.source_reason = "COMMAND_ACK_EXHAUSTED"
    hold.source_command_id = cast("int", command.id)
    hold.source_device_id = cast("int", device.id)
    hold.trace_id = command.trace_id
    request = _continue_request(service, hold).model_copy(
        update={
            "result_payload": {
                "item_id": "ITEM-001",
                "reel_diameter": "178.0",
                "reel_thickness": "15.0",
            }
        }
    )

    result = await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(session)
    await db_session.refresh(command)
    assert result["status"] == RuntimeHoldStatus.RESOLVED.value
    assert isinstance(result["created_inbox_id"], int)
    assert session.status == SessionStatus.WAITING_DEVICE_RESULT
    assert session.ended_at is None
    assert session.awaiting_command_id == command.id
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.reconciliation_state == RuntimeReconciliationState.RESOLVED
    assert session.reconciliation_resolution == RuntimeReconciliationResolution.COMPLETED
    assert command.status == CommandStatus.COMPLETED
    assert command.result == CommandResult.SUCCESS
    assert command.result_data == {
        "item_id": "ITEM-001",
        "reel_diameter": "178.0",
        "reel_thickness": "15.0",
    }

    inbox = await db_session.get(WorklineInbox, result["created_inbox_id"])
    assert inbox is not None
    assert inbox.kind == InboxKind.COMMAND_RESULT
    assert inbox.session_id == session.id
    assert inbox.command_id == command.id
    assert inbox.device_id == device.id
    assert inbox.payload_json == {
        "command_code": command.command_code,
        "device_code": device.device_code,
        "task_type": "PICK_AND_PUT",
        "result": "SUCCESS",
        "runtime_hold_release": True,
        "data": {
            "item_id": "ITEM-001",
            "reel_diameter": "178.0",
            "reel_thickness": "15.0",
        },
    }


async def test_continue_for_command_backed_hold_uses_command_params_when_result_payload_is_empty(
    db_session,
) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-CONTINUE-PARAMS")
    device = Device(
        device_code="PIPELINE-HOLD-CONTINUE-PARAMS",
        device_name="Pipeline",
        work_line_id=workline.id,
        device_role="CONVEYOR",
        role_index=1,
    )
    db_session.add(device)
    await db_session.flush()
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-CONTINUE-PARAMS",
        context={"item_id": "ITEM-001"},
    )
    session.reconciliation_state = RuntimeReconciliationState.PENDING
    session.reconciliation_reason = RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED
    command = DeviceCommand(
        command_code="CMD-HOLD-CONTINUE-PARAMS",
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="MOVE_FORWARD",
        params={"item_id": "ITEM-001"},
        status=CommandStatus.FAILED,
        trace_id="trace-hold-continue-params",
    )
    db_session.add(command)
    await db_session.flush()
    hold = await _create_hold(
        db_session,
        workline,
        session=session,
        key="hold:continue-params",
    )
    hold.source_reason = "COMMAND_ACK_EXHAUSTED"
    hold.source_command_id = cast("int", command.id)
    hold.source_device_id = cast("int", device.id)
    hold.trace_id = command.trace_id

    request = _continue_request(service, hold).model_copy(
        update={"latest_evidence_hash": service.build_latest_evidence_hash(hold, session=session)}
    )

    result = await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(command)
    assert command.status == CommandStatus.COMPLETED
    assert command.result == CommandResult.SUCCESS
    assert command.result_data == {"item_id": "ITEM-001"}

    inbox = await db_session.get(WorklineInbox, result["created_inbox_id"])
    assert inbox is not None
    assert inbox.payload_json == {
        "command_code": command.command_code,
        "device_code": device.device_code,
        "task_type": "MOVE_FORWARD",
        "result": "SUCCESS",
        "runtime_hold_release": True,
        "data": {"item_id": "ITEM-001"},
    }


@pytest.mark.parametrize(
    "result_payload",
    [
        None,
        {"reel_diameter": "NaN", "reel_thickness": "15.0"},
        {"reel_diameter": "178.0", "reel_thickness": "Infinity"},
        {"reel_diameter": "0", "reel_thickness": "15.0"},
        {"reel_diameter": "178.0", "reel_thickness": "-1"},
    ],
)
async def test_continue_for_rough_sorter_pick_and_put_requires_valid_measurement_payload(
    db_session,
    result_payload: dict[str, str] | None,
) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-ROUGH-PICK-NO-MEASUREMENT")
    workline.plugin_key = "rough_sorter"
    workline.contract_version = "rough_sorter.v1"
    device = Device(
        device_code="ARM03-HOLD-ROUGH-PICK-NO-MEASUREMENT",
        device_name="ARM03",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=3,
    )
    db_session.add(device)
    await db_session.flush()
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-ROUGH-PICK-NO-MEASUREMENT",
        context={"item_id": "ITEM-001"},
    )
    session.plugin_key = workline.plugin_key
    session.contract_version = workline.contract_version
    session.reconciliation_state = RuntimeReconciliationState.PENDING
    session.reconciliation_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
    command = DeviceCommand(
        command_code="CMD-HOLD-ROUGH-PICK-NO-MEASUREMENT",
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="PICK_AND_PUT",
        params={"business_key": "ITEM-001"},
        status=CommandStatus.FAILED,
        trace_id="trace-hold-rough-pick-no-measurement",
    )
    db_session.add(command)
    await db_session.flush()
    hold = await _create_hold(
        db_session,
        workline,
        session=session,
        key="hold:rough-pick-no-measurement",
    )
    hold.plugin_key = workline.plugin_key
    hold.contract_version = workline.contract_version
    hold.source_reason = "CALLBACK_DEADLINE_EXPIRED"
    hold.source_command_id = cast("int", command.id)
    hold.source_device_id = cast("int", device.id)
    hold.trace_id = command.trace_id

    request_update: dict[str, Any] = {"latest_evidence_hash": service.build_latest_evidence_hash(hold, session=session)}
    if result_payload is not None:
        request_update["result_payload"] = result_payload
    request = _continue_request(service, hold).model_copy(update=request_update)

    with pytest.raises(RuntimeHoldReleaseError, match="reel_diameter/reel_thickness"):
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(hold)
    await db_session.refresh(command)
    assert hold.status == RuntimeHoldStatus.OPEN
    assert command.status == CommandStatus.FAILED
    assert command.result is None
    assert command.result_data is None


async def test_continue_for_command_hold_uses_late_callback_payload_when_result_payload_is_empty(
    db_session,
) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-CONTINUE-LATE-CALLBACK")
    workline.plugin_key = "rough_sorter"
    workline.contract_version = "rough_sorter.v1"
    device = Device(
        device_code="ARM03-HOLD-CONTINUE-LATE-CALLBACK",
        device_name="ARM03",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=3,
    )
    db_session.add(device)
    await db_session.flush()
    command_code = "CMD-HOLD-CONTINUE-LATE-CALLBACK"
    measurement_payload = {"reel_diameter": "178.0", "reel_thickness": "15.0", "measurement_result": "OK"}
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-CONTINUE-LATE-CALLBACK",
        context={
            "runtime_reconciliation_late_callback_evidence": [
                {
                    "evidence_key": "late-callback-1",
                    "command_code": command_code,
                    "payload": {
                        "command_code": command_code,
                        "result": "SUCCESS",
                        "data": measurement_payload,
                    },
                }
            ]
        },
    )
    session.plugin_key = workline.plugin_key
    session.contract_version = workline.contract_version
    session.reconciliation_state = RuntimeReconciliationState.PENDING
    session.reconciliation_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
    session.reconciliation_late_evidence_received = True
    command = DeviceCommand(
        command_code=command_code,
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="PICK_AND_PUT",
        params={"business_key": "ITEM-001"},
        status=CommandStatus.FAILED,
        trace_id="trace-hold-continue-late-callback",
    )
    db_session.add(command)
    await db_session.flush()
    hold = await _create_hold(
        db_session,
        workline,
        session=session,
        key="hold:continue-late-callback",
    )
    hold.source_reason = "CALLBACK_DEADLINE_EXPIRED"
    hold.source_command_id = cast("int", command.id)
    hold.source_device_id = cast("int", device.id)
    hold.trace_id = command.trace_id

    request = _continue_request(service, hold).model_copy(
        update={"latest_evidence_hash": service.build_latest_evidence_hash(hold, session=session)}
    )
    result = await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(command)
    assert command.result_data == measurement_payload
    inbox = await db_session.get(WorklineInbox, result["created_inbox_id"])
    assert inbox is not None
    assert inbox.payload_json["data"] == measurement_payload


async def test_return_to_ng_requires_handoff_evidence_and_does_not_release_workline(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-NO-HANDOFF")
    session = await _create_session(db_session, workline, code="S-HOLD-NO-HANDOFF")
    hold = await _create_hold(db_session, workline, session=session, key="hold:no-handoff")
    request = _return_to_ng_request(service, hold).model_copy(update={"physical_handoff_evidence": None})

    with pytest.raises(ValueError, match="physical_handoff_evidence"):
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    await db_session.refresh(workline)
    await db_session.refresh(hold)
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert hold.status == RuntimeHoldStatus.OPEN


async def test_failed_resolution_preserves_session_failure_reason(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-FAILED-REASON")
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-FAILED-REASON",
        context={"item_id": "ITEM-001"},
    )
    session.failure_domain = "BLOCK"
    session.failure_code = "SCAN_NG"
    session.failure_message = "原始阻塞原因"
    hold = await _create_hold(db_session, workline, session=session, key="hold:failed-reason")

    _ = await service.resolve_hold(db_session, cast("int", hold.id), _return_to_ng_request(service, hold), 42)

    await db_session.refresh(session)
    assert session.status == SessionStatus.FAILED
    assert session.failure_domain == "BLOCK"
    assert session.failure_code == "SCAN_NG"
    assert session.failure_message == "原始阻塞原因"


async def test_failed_resolution_resolves_active_hold_for_already_failed_session(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-FAILED-IDEMPOTENT")
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-FAILED-IDEMPOTENT",
        context={"item_id": "ITEM-001"},
    )
    hold = await _create_hold(db_session, workline, session=session, key="hold:failed-idempotent")
    session.status = SessionStatus.FAILED
    session.failure_domain = "SAFETY"
    session.failure_code = "WORKLINE_ESTOPPED"
    session.failure_message = "WorkLine 急停冻结"
    await db_session.flush()

    result = await service.resolve_hold(db_session, cast("int", hold.id), _return_to_ng_request(service, hold), 42)

    await db_session.refresh(workline)
    await db_session.refresh(hold)
    await db_session.refresh(session)
    assert result["status"] == RuntimeHoldStatus.RESOLVED.value
    assert hold.status == RuntimeHoldStatus.RESOLVED
    assert result["workline_runtime_status"] == WorkLineRuntimeStatus.STOPPED.value
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert session.status == SessionStatus.FAILED
    assert session.failure_domain == "SAFETY"
    assert session.failure_code == "WORKLINE_ESTOPPED"
    assert session.failure_message == "WorkLine 急停冻结"


async def test_return_to_ng_requires_ng_reason(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-NO-REASON")
    session = await _create_session(db_session, workline, code="S-HOLD-NO-REASON")
    hold = await _create_hold(db_session, workline, session=session, key="hold:no-reason")
    request = _return_to_ng_request(service, hold).model_copy(update={"ng_reason": None})

    with pytest.raises(ValueError, match="ng_reason"):
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)


async def test_return_to_ng_rejects_unmapped_ng_reason(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-BAD-REASON")
    session = await _create_session(db_session, workline, code="S-HOLD-BAD-REASON")
    hold = await _create_hold(db_session, workline, session=session, key="hold:bad-reason")
    payload = _return_to_ng_request(service, hold).model_dump(mode="json")
    payload["ng_reason"] = {"source": "PLUGIN", "code": "FREE_TEXT_REASON", "label": "随便填的原因"}
    request = ResolveRuntimeHoldRequest.model_validate(payload)

    with pytest.raises(RuntimeHoldReleaseError) as exc_info:
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    assert exc_info.value.error_code == "RUNTIME_HOLD_REASON_UNMAPPED"


async def test_return_to_ng_rejects_unmapped_ng_location(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-BAD-LOCATION")
    session = await _create_session(db_session, workline, code="S-HOLD-BAD-LOCATION")
    hold = await _create_hold(db_session, workline, session=session, key="hold:bad-location")
    payload = _return_to_ng_request(service, hold).model_dump(mode="json")
    payload["physical_handoff_evidence"]["ng_location_code"] = "FREE-TEXT"
    payload["physical_handoff_evidence"]["ng_location_scan"] = "FREE-TEXT"
    request = ResolveRuntimeHoldRequest.model_validate(payload)

    with pytest.raises(RuntimeHoldReleaseError) as exc_info:
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    assert exc_info.value.error_code == "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED"


async def test_return_to_ng_rejects_active_ng_item_for_same_material_identity(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-MATERIAL-CONFLICT")
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-MATERIAL-CONFLICT",
        context={"item_id": "ITEM-001"},
    )
    hold = await _create_hold(db_session, workline, session=session, key="hold:material-conflict")
    other_hold = await _create_hold(db_session, workline, session=session, key="hold:material-conflict:other")
    existing_item = NgReturnItem(
        source_workline_id=cast("int", workline.id),
        source_session_id=cast("int", session.id),
        material_identity_key="test-material:ITEM-001",
        material_identity_json={"idempotency_key": "test-material:ITEM-001"},
        physical_handoff_evidence_json={"ng_location_code": "NG-01"},
        disposition=MaterialDisposition.RETURN_TO_NG,
        ng_reason_source=NgReasonSource.PLUGIN,
        ng_reason_code="SCAN_NG",
        ng_reason_label="扫码异常",
        created_from_runtime_hold_id=cast("int", other_hold.id),
        status=NgReturnItemStatus.WAITING_REWORK,
    )
    db_session.add(existing_item)
    await db_session.flush()

    with pytest.raises(RuntimeHoldReleaseError) as exc_info:
        await service.resolve_hold(db_session, cast("int", hold.id), _return_to_ng_request(service, hold), 42)

    assert exc_info.value.error_code == "RUNTIME_HOLD_MATERIAL_CONFLICT"
    assert exc_info.value.data == {
        "material_identity_key": "test-material:ITEM-001",
        "existing_ng_return_item_id": existing_item.id,
        "existing_runtime_hold_id": other_hold.id,
        "existing_status": NgReturnItemStatus.WAITING_REWORK.value,
    }


async def test_return_to_ng_material_conflict_is_enforced_by_database_when_precheck_races(
    db_session,
    monkeypatch,
) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-MATERIAL-RACE")
    session = await _create_session(
        db_session,
        workline,
        code="S-HOLD-MATERIAL-RACE",
        context={"item_id": "ITEM-001"},
    )
    hold = await _create_hold(db_session, workline, session=session, key="hold:material-race")
    other_hold = await _create_hold(db_session, workline, session=session, key="hold:material-race:other")
    existing_item = NgReturnItem(
        source_workline_id=cast("int", workline.id),
        source_session_id=cast("int", session.id),
        material_identity_key="test-material:ITEM-001",
        material_identity_json={"idempotency_key": "test-material:ITEM-001"},
        physical_handoff_evidence_json={"ng_location_code": "NG-01"},
        disposition=MaterialDisposition.RETURN_TO_NG,
        ng_reason_source=NgReasonSource.PLUGIN,
        ng_reason_code="SCAN_NG",
        ng_reason_label="扫码异常",
        created_from_runtime_hold_id=cast("int", other_hold.id),
        status=NgReturnItemStatus.WAITING_REWORK,
    )
    db_session.add(existing_item)
    await db_session.flush()

    original_get_active_item = service.runtime_hold_repo.get_active_ng_return_item_by_material_identity

    async def stale_precheck(*args, **kwargs):
        if kwargs.get("exclude_runtime_hold_id") == hold.id:
            return None
        return await original_get_active_item(*args, **kwargs)

    monkeypatch.setattr(
        service.runtime_hold_repo,
        "get_active_ng_return_item_by_material_identity",
        stale_precheck,
    )

    with pytest.raises(RuntimeHoldReleaseError) as exc_info:
        await service.resolve_hold(db_session, cast("int", hold.id), _return_to_ng_request(service, hold), 42)

    assert exc_info.value.error_code == "RUNTIME_HOLD_MATERIAL_CONFLICT"
    assert exc_info.value.data == {
        "material_identity_key": "test-material:ITEM-001",
        "existing_ng_return_item_id": existing_item.id,
        "existing_runtime_hold_id": other_hold.id,
        "existing_status": NgReturnItemStatus.WAITING_REWORK.value,
    }


async def test_material_identity_missing_or_ambiguous_rejects(db_session) -> None:
    service = RuntimeHoldReleaseService()
    missing_workline = await _create_workline(db_session, code="WL-HOLD-MISSING")
    missing_session = await _create_session(db_session, missing_workline, code="S-HOLD-MISSING")
    missing_hold = await _create_hold(
        db_session,
        missing_workline,
        session=missing_session,
        key="hold:missing",
        evidence={},
    )
    missing_payload = _return_to_ng_request(service, missing_hold).model_dump(mode="json")
    missing_payload["physical_handoff_evidence"] = {
        "ng_location_code": "NG-01",
        "ng_location_scan": "NG-01",
        "material_scan_payload": {},
        "line_clear_checked": True,
        "late_callback_reviewed": True,
    }
    missing_request = ResolveRuntimeHoldRequest.model_validate(missing_payload)
    with pytest.raises(ValueError, match="material identity unresolved: MISSING"):
        await service.resolve_hold(db_session, cast("int", missing_hold.id), missing_request, 42)

    ambiguous_workline = await _create_workline(db_session, code="WL-HOLD-AMBIG")
    ambiguous_session = await _create_session(db_session, ambiguous_workline, code="S-HOLD-AMBIG")
    ambiguous_hold = await _create_hold(
        db_session,
        ambiguous_workline,
        session=ambiguous_session,
        key="hold:ambiguous",
        evidence={"inbox_payload": {"data": {"item_id": "ITEM-001"}}},
    )
    ambiguous_payload = _return_to_ng_request(service, ambiguous_hold).model_dump(mode="json")
    ambiguous_payload["physical_handoff_evidence"] = {
        "ng_location_code": "NG-01",
        "ng_location_scan": "NG-01",
        "material_scan_payload": {"item_id": "ITEM-002"},
        "line_clear_checked": True,
        "late_callback_reviewed": True,
    }
    ambiguous_request = ResolveRuntimeHoldRequest.model_validate(ambiguous_payload)

    with pytest.raises(ValueError, match="material identity unresolved: AMBIGUOUS"):
        await service.resolve_hold(db_session, cast("int", ambiguous_hold.id), ambiguous_request, 42)


async def test_stale_hold_version_and_evidence_hash_reject(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-STALE")
    session = await _create_session(db_session, workline, code="S-HOLD-STALE")
    hold = await _create_hold(db_session, workline, session=session, key="hold:stale")

    stale_version = _continue_request(service, hold).model_copy(update={"hold_version": hold.version + 1})
    with pytest.raises(ValueError, match="version conflict"):
        await service.resolve_hold(db_session, cast("int", hold.id), stale_version, 42)

    stale_hash = _continue_request(service, hold).model_copy(update={"latest_evidence_hash": "sha256:stale"})
    with pytest.raises(ValueError, match="evidence changed"):
        await service.resolve_hold(db_session, cast("int", hold.id), stale_hash, 42)


async def test_late_callback_evidence_changes_release_hash(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-LATE-EVIDENCE")
    session = await _create_session(db_session, workline, code="S-HOLD-LATE-EVIDENCE")
    hold = await _create_hold(db_session, workline, session=session, key="hold:late-evidence")
    request = _continue_request(service, hold)

    session.context_json = {
        "runtime_reconciliation_late_callback_evidence": [
            {"evidence_key": "event_id:late-001", "payload": {"result": "SUCCESS"}}
        ]
    }
    await db_session.flush()

    with pytest.raises(ValueError, match="evidence changed"):
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)


async def test_resolving_one_hold_keeps_workline_not_ready_when_another_blocking_hold_exists(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-MULTI")
    session = await _create_session(db_session, workline, code="S-HOLD-MULTI")
    first = await _create_hold(db_session, workline, session=session, key="hold:multi:1")
    second = await _create_hold(db_session, workline, key="hold:multi:2", hold_type=RuntimeHoldType.MANUAL_HOLD)

    result = await service.resolve_hold(db_session, cast("int", first.id), _continue_request(service, first), 42)

    await db_session.refresh(workline)
    await db_session.refresh(second)
    assert result["remaining_active_blocking_holds"] == 1
    assert second.status == RuntimeHoldStatus.OPEN
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING


async def test_safety_estop_hold_requires_clear_estop_entrypoint(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-SAFETY")
    workline.runtime_status = WorkLineRuntimeStatus.ESTOPPED
    hold = await _create_hold(
        db_session,
        workline,
        key="hold:safety-estop",
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
    )
    hold.source_kind = "SAFETY_ESTOP"
    hold.source_reason = "ESTOP_PRESSED"
    await db_session.flush()

    with pytest.raises(RuntimeHoldReleaseError, match="clear-estop"):
        await service.resolve_hold(db_session, cast("int", hold.id), _continue_request(service, hold), 42)


async def test_last_blocking_hold_resolved_releases_blocked_outbox(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-OUTBOX")
    session = await _create_session(db_session, workline, code="S-HOLD-OUTBOX")
    hold = await _create_hold(db_session, workline, session=session, key="hold:outbox")
    outbox = SystemOutbox(
        session_id=cast("int", session.id),
        workline_id=cast("int", workline.id),
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:HOLD-OUTBOX",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ARM01",
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_by_runtime_hold_id=cast("int", hold.id),
        blocked_by_reconciliation_session_id=cast("int", session.id),
        blocked_workline_id=cast("int", workline.id),
        blocked_reason="RUNTIME_HOLD",
        last_error="RUNTIME_HOLD",
    )
    db_session.add(outbox)
    await db_session.flush()

    result = await service.resolve_hold(db_session, cast("int", hold.id), _continue_request(service, hold), 42)

    await db_session.refresh(workline)
    await db_session.refresh(outbox)
    assert result["released_outbox_count"] == 1
    assert result["workline_runtime_status"] == WorkLineRuntimeStatus.STOPPED.value
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.blocked_by_runtime_hold_id is None
    assert outbox.blocked_by_reconciliation_session_id is None
    assert outbox.blocked_workline_id is None
    assert outbox.blocked_reason is None


async def test_repeated_resolved_hold_rejects_without_second_ng_item(db_session) -> None:
    service = RuntimeHoldReleaseService()
    workline = await _create_workline(db_session, code="WL-HOLD-REPEAT")
    session = await _create_session(db_session, workline, code="S-HOLD-REPEAT")
    hold = await _create_hold(db_session, workline, session=session, key="hold:repeat")
    request = _return_to_ng_request(service, hold)

    await service.resolve_hold(db_session, cast("int", hold.id), request, 42)
    with pytest.raises(ValueError, match="已解除"):
        await service.resolve_hold(db_session, cast("int", hold.id), request, 42)

    assert await _ng_item_count(db_session, cast("int", hold.id)) == 1
