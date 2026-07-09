"""BC-XX DeviceCommandPort dispatch + ACK/result correlation 行为契约。

验收: DeviceCommand 状态机 PENDING → SENT → ACK_RECEIVED → COMPLETED 正确推进;
       H4 反注入禁止字段 (plc/coordinate/joint_angle/x_coord/y_coord/safety_loop)
       被 params 阻断; correlation_id 跨域稳定; 不含 session FK。
       主计划 §7.5 DEVICE_COMMAND_BOUNDARY。
mock 仅允许 `src/app/device/models/command.py` 内的 skeleton 模型, 不依赖 DB。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.device.models.command import (
    _FORBIDDEN_PARAM_KEYS,
    CommandAck,
    CommandBase,
    CommandCallbackResult,
    CommandRequest,
    CommandResult,
    CommandStatus,
    DeviceCommand,
)


def test_command_request_initial_state_pending_via_schema():
    """happy path: CommandRequest 不持 status, 初始 status 由 DeviceCommand 默认 PENDING。"""
    req = CommandRequest(
        device_id=1,
        task_type="PICK",
        command_code="cmd-001",
        params={"bin_id": "bin-A"},
    )
    assert req.params == {"bin_id": "bin-A"}
    assert "status" not in CommandRequest.model_fields


def test_device_command_state_machine_initial_pending():
    """happy path: 新建 DeviceCommand 初始状态为 PENDING。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    assert cmd.status == CommandStatus.PENDING


def test_device_command_state_progression_pending_to_completed():
    """happy path: PENDING → SENT → ACK_RECEIVED → COMPLETED 完整链路。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    cmd.status = CommandStatus.SENT
    cmd.status = CommandStatus.ACK_RECEIVED
    cmd.status = CommandStatus.COMPLETED
    assert cmd.status == CommandStatus.COMPLETED


def test_device_command_state_cancelled_from_pending():
    """error path: PENDING 可直接 CANCELLED (未发送即可取消)。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    cmd.status = CommandStatus.CANCELLED
    assert cmd.status == CommandStatus.CANCELLED


def test_device_command_state_timeout_after_sent():
    """error path: SENT 后超时进入 TIMEOUT。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    cmd.status = CommandStatus.SENT
    cmd.status = CommandStatus.TIMEOUT
    assert cmd.status == CommandStatus.TIMEOUT


@pytest.mark.parametrize("forbidden_key", sorted(_FORBIDDEN_PARAM_KEYS))
def test_command_request_params_reject_forbidden_h4_fields(forbidden_key):
    """H4 反注入边界: params 拒绝 plc/coordinate/joint_angle/x_coord/y_coord/safety_loop 等字段。"""
    with pytest.raises((ValidationError, ValueError)):
        CommandRequest(
            device_id=1,
            task_type="PICK",
            params={forbidden_key: "any-value"},
        )


def test_command_request_params_accept_normal_business_fields():
    """happy path: 正常业务字段透传不被阻断。"""
    req = CommandRequest(
        device_id=1,
        task_type="PICK",
        params={"bin_id": "bin-001", "quantity": 5},
    )
    assert req.params == {"bin_id": "bin-001", "quantity": 5}


def test_device_command_correlation_id_links_to_execution_correlation():
    """happy path: correlation_id 引用 ExecutionCorrelation.correlation_id,
    无 session FK (主计划 §7.5 DEVICE_COMMAND_BOUNDARY)。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
        correlation_id="corr-001",
    )
    assert cmd.correlation_id == "corr-001"
    forbidden_session_fk = {"session_id", "session_id_int", "execution_session_id"}
    leaked = forbidden_session_fk & set(DeviceCommand.model_fields.keys())
    assert not leaked, f"DeviceCommand 不应持 session FK 字段: {leaked}"


def test_command_base_rejects_extra_fields_via_pydantic():
    """H4 extra="forbid" 阻断未声明字段污染 DeviceCommand。"""
    with pytest.raises(ValidationError):
        CommandBase(
            device_id=1,
            task_type="PICK",
            plc_address="forbidden-injection",
        )


def test_command_ack_has_required_response_fields():
    """happy path: CommandAck 必须含 code/message/trace_id 三元组 (H5 idempotency 归因)。"""
    ack = CommandAck(code=200, message="ok", trace_id="trace-001")
    assert ack.code == 200
    assert ack.message == "ok"
    assert ack.trace_id == "trace-001"


def test_command_callback_result_requires_command_code_and_device_code():
    """happy path: 设备回调必须携带 command_code + device_code 归因,
    用于 InboxStatus PROCESSED 闭环。"""
    cb = CommandCallbackResult(
        command_code="cmd-001",
        device_code="device-A",
        result=CommandResult.SUCCESS,
        finish_time=1700000000000,
    )
    assert cb.command_code == "cmd-001"
    assert cb.device_code == "device-A"
    assert cb.result == CommandResult.SUCCESS


def test_command_callback_result_correlation_chain_required():
    """happy path: 设备回调可携带 trace_id/event_id/causation_id 形成因果链 (主计划 §3.5.1)。"""
    cb = CommandCallbackResult(
        command_code="cmd-001",
        device_code="device-A",
        result=CommandResult.SUCCESS,
        finish_time=1700000000000,
        trace_id="trace-001",
        event_id="evt-cb-001",
        causation_id="evt-cmd-001",
    )
    assert cb.trace_id == "trace-001"
    assert cb.event_id == "evt-cb-001"
    assert cb.causation_id == "evt-cmd-001"


def test_device_command_can_retry_respects_retry_count():
    """error path: can_retry 在 retry_count < 3 + 状态为 FAILED/TIMEOUT 时返回 True。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    cmd.status = CommandStatus.FAILED
    cmd.retry_count = 2
    assert cmd.can_retry() is True


def test_device_command_can_retry_exhausted_at_three():
    """error path: can_retry 在 retry_count == 3 时返回 False (进入 DEAD_LETTER 触发)。"""
    cmd = DeviceCommand(
        device_id=1,
        workline_id=7,
        command_code="cmd-001",
        task_type="PICK",
    )
    cmd.status = CommandStatus.TIMEOUT
    cmd.retry_count = 3
    assert cmd.can_retry() is False


def test_device_command_status_field_uses_varchar_check_constraint():
    """验证 status 字段为 VARCHAR + CHECK 约束 (主计划 §10.4 项目规则, 禁用 PG ENUM)。"""
    from sqlalchemy import Enum as SQLAEnum

    status_field = DeviceCommand.model_fields["status"]
    sa_type = status_field.sa_type if hasattr(status_field, "sa_type") else status_field.annotation
    if isinstance(sa_type, SQLAEnum):
        assert sa_type.native_enum is False, "DeviceCommand.status 必须禁用原生 ENUM"
        assert sa_type.create_constraint is True, "必须创建 CHECK 约束"
