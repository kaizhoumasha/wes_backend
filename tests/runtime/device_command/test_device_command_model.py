"""DeviceCommand 最终模型与状态机不变量。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.app.device.models.command import CommandStatus, DeviceCommand, InvalidCommandTransitionError


def _command(*, device_code: str = "ARM-01", status: CommandStatus = CommandStatus.PENDING) -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        command_code=f"CMD-{device_code}",
        device_code=device_code,
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=21,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={"source": {"type": "BIN", "code": "BIN-01"}},
        payload_digest="a" * 64,
        deadline_at=now + timedelta(seconds=30),
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_command_status_is_final_closed_set() -> None:
    assert {status.value for status in CommandStatus} == {
        "PENDING",
        "DISPATCHING",
        "ACKNOWLEDGED",
        "RECONCILING",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
    }


@pytest.mark.parametrize(
    "status",
    [
        CommandStatus.PENDING,
        CommandStatus.DISPATCHING,
        CommandStatus.ACKNOWLEDGED,
        CommandStatus.RECONCILING,
    ],
)
def test_all_unclosed_states_keep_device_slot(status: CommandStatus) -> None:
    assert _command(status=status).occupies_device_slot is True


@pytest.mark.parametrize(
    "status",
    [CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.TIMED_OUT],
)
def test_terminal_states_release_device_slot(status: CommandStatus) -> None:
    assert _command(status=status).occupies_device_slot is False


def test_acknowledged_command_cannot_be_marked_timed_out() -> None:
    command = _command(status=CommandStatus.ACKNOWLEDGED)

    with pytest.raises(InvalidCommandTransitionError):
        command.transition_to(CommandStatus.TIMED_OUT)


def test_matching_callback_can_close_reconciling_command() -> None:
    command = _command(status=CommandStatus.RECONCILING)

    command.transition_to(CommandStatus.SUCCEEDED)

    assert command.status == CommandStatus.SUCCEEDED


def test_manual_debug_context_cannot_bind_material_execution() -> None:
    constraint = next(
        item
        for item in DeviceCommand.__table__.constraints
        if item.name and item.name.endswith("device_command_execution_context_complete")
    )

    assert "material_execution_id IS NULL" in str(constraint.sqltext)


def test_final_model_has_no_legacy_priority_cancel_or_wire_override_fields() -> None:
    fields = DeviceCommand.model_fields

    assert "priority" not in fields
    assert "timeout_ms" not in fields
    assert "event_id" not in fields
    assert "causation_id" not in fields
    assert "correlation_id" not in fields
    assert "workline_id" not in fields
    assert "plugin_key" not in fields
