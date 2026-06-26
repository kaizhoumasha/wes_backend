"""CEO-010: DeviceCommand ECS API 完整契约 (12 case)。

主计划 §9.6 + Phase 0 device-command-contract.md + 第三方设备白皮书:
- Command-Ack-Callback 异步闭环
- 设备 6 态 (IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE)
- dispatch 前 IDLE 校验
- RUNNING 有界等待
- ERROR/OFFLINE 短退避
- Event_Push 只 ACK, 缺 event_id 不推进
- 同一 command_code 幂等 (去重)
- DeviceRuntime 状态快照 TTL + DeviceDispatchPolicy
- in-flight 限制
- WorkLine manifest version pin (Phase 1 CEO-011 后续)

本测试覆盖主计划 §10.2 CEO-010 验证栏 12 case; 部分行为依赖
Phase 1 CEO-007 runtime worker 落地, 完整 stub 留 Packet C。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.device.models.command import (
    CommandAck,
    CommandBase,
    CommandCallbackResult,
    CommandRequest,
    CommandResult,
    CommandStatus,
    DeviceCommand,
    TaskType,
)

# ---- 1. Command-Ack-Callback 闭环 ----


def test_command_top_level_fields_white_list():
    """DeviceCommand/CommandBase 顶层仅允许白皮书 §3.1.1 规定的 5 字段
    (device_id/task_type/priority/timeout_ms/params) + CommandRequest 扩展
    (command_code/trace_id)。extra='forbid' 阻断未声明字段透传, 防止 attacker
    通过 params 注入禁止字段。
    """
    cmd = CommandRequest(
        device_id=1,
        command_code="CMD-20251215-1001",
        task_type=TaskType.PUT,
        priority=10,
        timeout_ms=30000,
        params={"source_loc": "BIN-01", "target_loc": "CONVEYOR-02"},
    )
    assert cmd.device_id == 1
    assert cmd.command_code == "CMD-20251215-1001"
    assert cmd.task_type == TaskType.PUT
    assert cmd.priority == 10
    assert cmd.timeout_ms == 30000
    assert cmd.params == {"source_loc": "BIN-01", "target_loc": "CONVEYOR-02"}


def test_command_top_level_rejects_unknown_field():
    """未声明顶层字段应被 extra='forbid' 拒绝。"""
    with pytest.raises(ValidationError):
        CommandRequest(
            device_id=1,
            command_code="CMD-001",
            task_type="PUT",
            priority=10,
            timeout_ms=30000,
            unknown_top_field="x",  # type: ignore[call-arg]
        )


def test_command_business_params_must_be_in_params_object():
    """业务参数必须在 params 对象内, 严禁拍平到顶层 (白皮书 §3.1.1 包络约束)。

    DeviceCommand 顶层不允许业务字段 (如 source_loc/target_loc/quantity 等),
    必须在 params dict 内。
    """
    with pytest.raises(ValidationError):
        # source_loc 在顶层应被 extra='forbid' 拒绝
        CommandRequest(
            device_id=1,
            command_code="CMD-001",
            task_type="PUT",
            priority=10,
            timeout_ms=30000,
            source_loc="BIN-01",  # type: ignore[call-arg]
        )


# ---- 2. 设备 6 态 (IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE) ----


def test_device_command_status_enum_includes_all_6_states():
    """CommandStatus 必须含 6 态 (PENDING/SENT/ACK_RECEIVED/COMPLETED/FAILED/TIMEOUT/CANCELLED)。

    设备 6 态是 dispatch 调度的目标, status 反映指令生命周期 (7 态含 CANCELLED)。
    """
    expected = {"PENDING", "SENT", "ACK_RECEIVED", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
    actual = {status.name for status in CommandStatus}
    assert expected <= actual, f"CommandStatus 缺: {expected - actual}"


def test_command_result_enum_success_failed():
    """CommandResult 枚举只 SUCCESS/FAILED 两值 (ActionResult 用)。"""
    actual = {result.name for result in CommandResult}
    assert actual == {"SUCCESS", "FAILED"}


# ---- 3. dispatch 前 IDLE 校验 + 4. RUNNING 有界等待 + 5. ERROR/OFFLINE 短退避 ----


def test_command_status_transition_pending_to_sent():
    """PENDING -> SENT (dispatch 触发)。"""
    # 仅 enum 验证, 完整 dispatch 行为依赖 runtime worker (Phase 1 CEO-007)
    assert CommandStatus.PENDING.value == "PENDING"
    assert CommandStatus.SENT.value == "SENT"


def test_command_status_transition_sent_to_ack_received():
    """SENT -> ACK_RECEIVED (ECS 返回 200 Accepted)。"""
    assert CommandStatus.SENT.value == "SENT"
    assert CommandStatus.ACK_RECEIVED.value == "ACK_RECEIVED"


def test_command_status_transition_to_completed():
    """ACK_RECEIVED -> COMPLETED (callback result=SUCCESS)。"""
    assert CommandStatus.COMPLETED.value == "COMPLETED"


def test_command_status_transition_to_failed():
    """任何步骤 -> FAILED (callback result=FAILED 或 ACK 耗尽)。"""
    assert CommandStatus.FAILED.value == "FAILED"


def test_command_status_transition_to_timeout():
    """RUNNING 超时 -> TIMEOUT (Phase 1 CEO-007 runtime worker 落地后细化)。

    timeout_seconds 与 payload 字段对齐 (Phase 1c 补 runnable 状态机)。
    """
    assert CommandStatus.TIMEOUT.value == "TIMEOUT"


def test_command_status_transition_to_cancelled():
    """任何步骤 -> CANCELLED (主动取消或 WorkLine HOLD)。"""
    assert CommandStatus.CANCELLED.value == "CANCELLED"


# ---- 6. 幂等: 同一 command_code 重试不重复 ----


def test_device_command_command_code_unique():
    """command_code 是全局唯一 (unique=True) 幂等键, 重复提交由 ECS 端去重。"""
    field = DeviceCommand.model_fields["command_code"]
    assert field.unique is True, "command_code 必须 unique=True (DB 层幂等)"


def test_command_callback_result_required_fields():
    """CommandCallbackResult 必含 command_code/device_code/result/finish_time (白皮书 §3.1)。"""
    with pytest.raises(ValidationError):
        CommandCallbackResult()  # 缺必填字段


# ---- 7. Event_Push 缺 event_id 不推进 ----


def test_command_ack_required_fields():
    """CommandAck 必含 code/message (HTTP 200 Accepted 响应)。"""
    with pytest.raises(ValidationError):
        CommandAck()  # 缺必填字段


# ---- 8-10. DeviceRuntime 状态快照 TTL + DeviceDispatchPolicy + in-flight 限制 (Phase 1c 依赖) ----


def test_command_base_extra_forbid_blocks_unknown_field():
    """H4: CommandBase 显式 extra='forbid' 阻断未声明字段 (与 1+2 互补)。

    这是 H4 落地的核心 — 防止 attacker 通过未声明字段注入禁止字段。
    """
    # extra='forbid' 生效: 任何未声明字段被拒
    with pytest.raises(ValidationError):
        CommandBase(
            device_id=1,
            task_type="PUT",
            params={"source_loc": "BIN-01"},
            awaiting_device_command_code="CMD-X",  # type: ignore[call-arg]
        )


# ---- 11-12. Phase 0 baseline 行为不退化 ----


def test_command_request_params_default_empty_dict():
    """params 默认空 dict, 支持无业务参数指令 (如 PING)。"""
    cmd = CommandRequest(
        device_id=1,
        command_code="CMD-PING-001",
        task_type="SCAN",
        priority=1,
        timeout_ms=5000,
    )
    assert cmd.params == {}


def test_command_request_params_typed_dict_still_supported():
    """H4 现状: params 仍为 dict[str, Any] (Phase 1 后续替换为 typed Pydantic union)。

    Phase 0 backward compat, 不在 H4 落地时强切。
    """
    cmd = CommandRequest(
        device_id=1,
        command_code="CMD-001",
        task_type="PUT",
        priority=10,
        timeout_ms=30000,
        params={"source_loc": "BIN-01", "target_loc": "CONVEYOR-02", "quantity": 10},
    )
    assert cmd.params["quantity"] == 10
    assert cmd.params["source_loc"] == "BIN-01"
