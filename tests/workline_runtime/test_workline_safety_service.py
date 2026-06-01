import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.app.device.models import Device, DeviceStatus
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.safety import WorkLineRuntimeStatus, WorklineSafetyIncidentStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services.safety_service import (
    WorkLineSafetyBlocked,
    WorkLineSafetyService,
    workline_safety_service,
)

pytestmark = pytest.mark.asyncio

SAFETY_TRIGGER_PAYLOAD_MAX_BYTES = 16 * 1024


async def test_handle_estop_freezes_workline_and_drains_open_work(db_session) -> None:
    workline = WorkLine(line_code="WL-ESTOP-001", line_name="急停线", line_type=LineType.AUTO)
    db_session.add(workline)
    await db_session.flush()

    device = Device(
        device_code="ESTOP-ARM-01",
        device_name="急停机械臂",
        work_line_id=workline.id,
        device_role="INPUT_ARM",
        device_status=DeviceStatus.RUNNING,
    )
    session = WorklineSession(
        session_code="S-ESTOP-001",
        workline_id=cast("int", workline.id),
        plugin_key="test_workline_plugin",
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add_all([device, session])
    await db_session.flush()

    outbox = SystemOutbox(
        session_id=cast("int", session.id),
        workline_id=cast("int", workline.id),
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:ESTOP-1",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        status=SystemOutboxStatus.SENT,
    )
    command = DeviceCommand(
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id_int=cast("int", session.id),
        command_code="CMD-ESTOP-1",
        task_type="PICK_AND_PUT",
        status=CommandStatus.ACK_RECEIVED,
    )
    db_session.add_all([outbox, command])
    await db_session.flush()
    runtime_hold_creation_service = SimpleNamespace(
        create_for_safety_estop=AsyncMock(return_value=SimpleNamespace(id=1))
    )
    service = WorkLineSafetyService(runtime_hold_creation_service=runtime_hold_creation_service)

    incident = await service.handle_estop(
        db_session,
        workline_id=cast("int", workline.id),
        source_inbox_id=100,
        source_device_id=device.id,
        trigger_payload={"event_type": "ESTOP_PRESSED", "device_code": device.device_code},
    )

    await db_session.refresh(workline)
    await db_session.refresh(session)
    await db_session.refresh(outbox)
    await db_session.refresh(command)
    await db_session.refresh(device)

    assert incident.status == WorklineSafetyIncidentStatus.ACTIVE
    assert incident.drain_status == "COMPLETED"
    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.active_safety_incident_id == incident.id
    assert session.status == SessionStatus.FAILED
    assert session.failure_code == "WORKLINE_ESTOPPED"
    assert outbox.status == SystemOutboxStatus.CANCELLED
    assert outbox.last_error == "CANCELLED_BY_ESTOP"
    assert command.status == CommandStatus.CANCELLED
    assert command.error_detail["error_code"] == "CANCELLED_BY_ESTOP"  # type: ignore[reportOptionalMemberAccess]
    assert device.device_status == DeviceStatus.ERROR
    runtime_hold_creation_service.create_for_safety_estop.assert_awaited_once_with(db_session, incident=incident)


class _FailingOutboxRepository:
    async def cancel_active_by_workline(self, *_args, **_kwargs) -> int:
        raise RuntimeError("drain boom")


async def test_handle_estop_preserves_freeze_when_drain_fails(db_session) -> None:
    workline = WorkLine(line_code="WL-ESTOP-DRAIN-FAIL", line_name="排空失败线", line_type=LineType.AUTO)
    db_session.add(workline)
    await db_session.flush()

    service = WorkLineSafetyService(system_outbox_repository=_FailingOutboxRepository())  # type: ignore[arg-type]

    incident = await service.handle_estop(
        db_session,
        workline_id=cast("int", workline.id),
        source_inbox_id=102,
        trigger_payload={"event_type": "ESTOP_PRESSED"},
    )

    await db_session.refresh(workline)
    await db_session.refresh(incident)

    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.active_safety_incident_id == incident.id
    assert incident.status == WorklineSafetyIncidentStatus.ACTIVE
    assert incident.drain_status == "FAILED"
    assert "drain boom" in incident.drain_error_json["message"]


async def test_handle_estop_redacts_and_limits_trigger_payload(db_session) -> None:
    workline = WorkLine(line_code="WL-ESTOP-REDACT", line_name="脱敏线", line_type=LineType.AUTO)
    db_session.add(workline)
    await db_session.flush()

    incident = await workline_safety_service.handle_estop(
        db_session,
        workline_id=cast("int", workline.id),
        trigger_payload={
            "event_type": "ESTOP_PRESSED",
            "authorization": "Bearer secret-token",
            "callback_url": "https://device.example/callback?token=query-secret&trace_id=ok",
            "nested": {"password": "secret-password"},
            "blob": "x" * (SAFETY_TRIGGER_PAYLOAD_MAX_BYTES + 1024),
        },
    )

    encoded = json.dumps(incident.trigger_payload_json, ensure_ascii=False)

    assert len(encoded.encode("utf-8")) <= SAFETY_TRIGGER_PAYLOAD_MAX_BYTES
    assert "secret-token" not in encoded
    assert "query-secret" not in encoded
    assert "secret-password" not in encoded
    assert incident.trigger_payload_json["_truncated"] is True


async def test_assert_accepting_work_blocks_estopped_workline(db_session) -> None:
    workline = WorkLine(
        line_code="WL-ESTOP-002",
        line_name="已冻结线",
        line_type=LineType.AUTO,
        runtime_status=WorkLineRuntimeStatus.ESTOPPED,
    )
    db_session.add(workline)
    await db_session.flush()

    with pytest.raises(WorkLineSafetyBlocked, match="WORKLINE_ESTOPPED"):
        await workline_safety_service.assert_accepting_work(db_session, workline_id=cast("int", workline.id))


async def test_clear_estop_requires_checklist_and_returns_stopped(db_session) -> None:
    workline = WorkLine(
        line_code="WL-ESTOP-003",
        line_name="恢复线",
        line_type=LineType.AUTO,
    )
    device = Device(
        device_code="DEV-ESTOP-003",
        device_name="恢复设备",
        device_role="INPUT_ARM",
        work_line_id=None,
        device_status=DeviceStatus.RUNNING,
    )
    db_session.add_all([workline, device])
    await db_session.flush()
    device.work_line_id = cast("int", workline.id)
    await db_session.flush()

    incident = await workline_safety_service.handle_estop(
        db_session,
        workline_id=cast("int", workline.id),
        source_inbox_id=101,
        trigger_payload={"event_type": "ESTOP_PRESSED"},
    )

    with pytest.raises(ValueError, match="checklist"):
        await workline_safety_service.clear_estop(
            db_session,
            workline_id=cast("int", workline.id),
            checks={"estop_button_reset": True, "area_safe": False},
        )

    cleared = await workline_safety_service.clear_estop(
        db_session,
        workline_id=cast("int", workline.id),
        checks={"estop_button_reset": True, "area_safe": True},
        reason="按钮复位，现场确认安全",
        operator_id=42,
    )

    await db_session.refresh(workline)
    await db_session.refresh(incident)
    await db_session.refresh(device)

    assert cleared.id == incident.id
    assert incident.status == WorklineSafetyIncidentStatus.CLEARED
    assert incident.recovery_check_json == {"estop_button_reset": True, "area_safe": True}
    assert incident.release_evidence_json == {
        "released_device_count": 1,
        "released_device_error_code": "WORKLINE_ESTOPPED",
    }
    assert incident.cleared_by == 42
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert workline.active_safety_incident_id is None
    assert device.device_status == DeviceStatus.IDLE
    assert device.error_code is None


async def test_simulate_estop_uses_sandbox_source_and_reason(db_session) -> None:
    workline = WorkLine(
        line_code="WL-ESTOP-004",
        line_name="模拟急停线",
        line_type=LineType.AUTO,
    )
    db_session.add(workline)
    await db_session.flush()

    incident = await workline_safety_service.simulate_estop(
        db_session,
        workline_id=cast("int", workline.id),
        reason="沙箱验证软件急停 UI",
        payload={"operator": "qa"},
    )

    await db_session.refresh(workline)
    await db_session.refresh(incident)

    assert workline.runtime_status == WorkLineRuntimeStatus.ESTOPPED
    assert workline.active_safety_incident_id == incident.id
    assert incident.reason == "沙箱验证软件急停 UI"
    assert incident.trigger_payload_json["event_type"] == "ESTOP_PRESSED"
    assert incident.trigger_payload_json["source"] == "sandbox"
    assert incident.trigger_payload_json["operator"] == "qa"
