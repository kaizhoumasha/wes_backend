import pytest

from src.app.device.models import Device, DeviceCommand
from src.app.device.models.command import CommandStatus
from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.runtime_hold import RuntimeHoldStatus, RuntimeHoldType
from src.app.workline.models.workline import LineType, WorkLine, WorkLineRunMode
from src.app.workline.repositories.runtime_hold_repository import RuntimeHoldRepository
from src.app.workline.services.runtime_query_service import RuntimeQueryService


async def _create_workline_with_devices(db_session) -> tuple[WorkLine, Device, Device]:
    workline = WorkLine(
        line_code="WL-RUNTIME-TOPOLOGY",
        line_name="运行态拓扑测试线",
        line_type=LineType.AUTO,
        run_mode=WorkLineRunMode.SIMULATION,
        plugin_key="smt_classifier",
        contract_version="1.0.0",
    )
    db_session.add(workline)
    await db_session.flush()

    arm01 = Device(
        device_code="ARM01-RUNTIME-TOPOLOGY",
        device_name="ARM01",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=1,
    )
    arm03 = Device(
        device_code="ARM03-RUNTIME-TOPOLOGY",
        device_name="ARM03",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=3,
    )
    db_session.add(arm01)
    db_session.add(arm03)
    await db_session.flush()
    return workline, arm01, arm03


def _command(
    *,
    device: Device,
    workline: WorkLine,
    command_code: str,
    status: CommandStatus,
) -> DeviceCommand:
    return DeviceCommand(
        device_id=device.id,
        workline_id=workline.id,
        command_code=command_code,
        task_type="MEASUREMENT_REEL",
        status=status,
        session_id="SES-RUNTIME-TOPOLOGY",
    )


@pytest.mark.asyncio
async def test_workline_detail_projects_open_blocked_and_hold_counts_by_device(db_session) -> None:
    workline, arm01, arm03 = await _create_workline_with_devices(db_session)
    db_session.add_all(
        [
            _command(device=arm01, workline=workline, command_code="CMD-OPEN-PENDING", status=CommandStatus.PENDING),
            _command(device=arm01, workline=workline, command_code="CMD-OPEN-SENT", status=CommandStatus.SENT),
            _command(
                device=arm01,
                workline=workline,
                command_code="CMD-OPEN-ACKED",
                status=CommandStatus.ACK_RECEIVED,
            ),
            _command(device=arm01, workline=workline, command_code="CMD-DONE", status=CommandStatus.COMPLETED),
            _command(device=arm01, workline=workline, command_code="CMD-FAILED", status=CommandStatus.FAILED),
            _command(device=arm01, workline=workline, command_code="CMD-CANCELLED", status=CommandStatus.CANCELLED),
            _command(device=arm01, workline=workline, command_code="CMD-TIMEOUT", status=CommandStatus.TIMEOUT),
            _command(device=arm03, workline=workline, command_code="CMD-BLOCKED", status=CommandStatus.PENDING),
        ]
    )
    blocked_outbox = WorklineOutbox(
        workline_id=workline.id,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:CMD-BLOCKED",
        target_type=TargetType.DEVICE,
        target_code=arm03.device_code,
        status=OutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=arm03.id,
        blocked_workline_id=workline.id,
        blocked_reason="DEVICE_BUSY",
        payload_json={"command_code": "CMD-BLOCKED"},
    )
    db_session.add(blocked_outbox)
    await db_session.flush()

    repo = RuntimeHoldRepository()
    active_hold = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=workline.id,
        source_kind="DISPATCH_ACK_EXHAUSTED",
        source_reason="COMMAND_ACK_EXHAUSTED",
        source_idempotency_key="runtime-topology:active",
        source_device_id=arm03.id,
        source_outbox_id=blocked_outbox.id,
    )
    resolved_hold = await repo.create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=workline.id,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="runtime-topology:resolved",
        source_device_id=arm03.id,
    )
    resolved_hold.status = RuntimeHoldStatus.RESOLVED
    await db_session.flush()

    detail = await RuntimeQueryService().get_workline_detail(db_session, workline.id)

    assert detail is not None
    devices = {item.device_code: item for item in detail.devices}
    assert devices[arm01.device_code].open_command_count == 3
    assert devices[arm01.device_code].pending_command_count == 3
    assert devices[arm01.device_code].blocked_outbox_count == 0
    assert devices[arm01.device_code].open_issue_count == 0

    assert devices[arm03.device_code].open_command_count == 0
    assert devices[arm03.device_code].pending_command_count == 0
    assert devices[arm03.device_code].blocked_outbox_count == 1
    assert devices[arm03.device_code].open_issue_count == 1
    assert devices[arm03.device_code].active_runtime_hold_ids == [active_hold.id]
