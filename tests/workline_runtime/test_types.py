"""
PluginResult & 相关意图类型单元测试

测试插件返回结果的构建和验证：
- WaitIntent: 等待意图
- CommandIntent: 设备命令意图
- FailureIntent: 失败归因意图
- PluginResult: 插件返回结果

设计参考:
- 设计文档: phase2-orchestrator design doc
"""

import pytest
from pydantic import ValidationError

from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)


class TestWaitIntent:
    """等待意图测试"""

    def test_create_wait_intent(self):
        """测试创建等待意图"""
        intent = WaitIntent(
            wait_type="COMMAND_RESULT",
            wait_token="cmd-123",
            deadline_seconds=30,
        )
        assert intent.wait_type == "COMMAND_RESULT"
        assert intent.wait_token == "cmd-123"
        assert intent.deadline_seconds == 30

    def test_wait_intent_required_fields(self):
        """测试必填字段验证"""
        with pytest.raises(ValidationError):
            WaitIntent()  # 缺少所有必填字段

    def test_wait_intent_invalid_deadline(self):
        """测试无效的 deadline_seconds"""
        with pytest.raises(ValidationError):
            WaitIntent(
                wait_type="COMMAND_RESULT",
                wait_token="cmd-123",
                deadline_seconds="not_a_number",  # type: ignore[arg-type]
            )


class TestCommandIntent:
    """设备命令意图测试"""

    def test_create_command_intent_with_parameters(self):
        """测试创建带参数的命令意图"""
        intent = CommandIntent(
            target_device_id=1,
            action="PICK_AND_PUT",
            parameters={"target_position": "A1", "speed": "fast"},
        )
        assert intent.target_device_id == 1
        assert intent.action == "PICK_AND_PUT"
        assert intent.parameters["target_position"] == "A1"

    def test_create_command_intent_without_parameters(self):
        """测试创建无参数的命令意图"""
        intent = CommandIntent(
            target_device_id=2,
            action="MOVE_FORWARD",
        )
        assert intent.target_device_id == 2
        assert intent.parameters == {}

    def test_command_intent_required_fields(self):
        """测试必填字段验证"""
        with pytest.raises(ValidationError):
            CommandIntent()  # 缺少 target_device_id 和 action


class TestFailureIntent:
    """失败归因意图测试"""

    def test_create_failure_intent(self):
        """测试创建失败意图"""
        intent = FailureIntent(
            domain="HARDWARE",
            code="DEVICE_OFFLINE",
            message="Device is not responding",
        )
        assert intent.domain == "HARDWARE"
        assert intent.code == "DEVICE_OFFLINE"
        assert intent.message == "Device is not responding"

    def test_failure_intent_required_fields(self):
        """测试必填字段验证"""
        with pytest.raises(ValidationError):
            FailureIntent(domain="HARDWARE")  # 缺少 code 和 message


class TestPluginResult:
    """插件返回结果测试"""

    def test_create_empty_result(self):
        """测试创建空结果（NullPlugin 返回）"""
        result = PluginResult()
        assert result.transition is None
        assert result.context_patch == {}
        assert result.decisions == []
        assert result.commands == []
        assert result.wait is None
        assert result.failure is None
        assert result.complete is False

    def test_create_result_with_transition(self):
        """测试创建带状态迁移的结果"""
        result = PluginResult(transition="scan_ok")
        assert result.transition == "scan_ok"

    def test_create_result_with_context_patch(self):
        """测试创建带上下文更新的结果"""
        result = PluginResult(context_patch={"scan_result": "OK", "barcode": "ABC123"})
        assert result.context_patch["scan_result"] == "OK"
        assert result.context_patch["barcode"] == "ABC123"

    def test_create_result_with_decision(self):
        """测试创建带决策的结果"""
        result = PluginResult(
            decisions=[
                {
                    "decision_type": "ROUTE_DECISION",
                    "target_location": "CONVEYOR_IN",
                    "reason": "Scan OK",
                }
            ]
        )
        assert len(result.decisions) == 1
        assert result.decisions[0]["decision_type"] == "ROUTE_DECISION"

    def test_create_result_with_command(self):
        """测试创建带设备命令的结果"""
        result = PluginResult(
            commands=[
                CommandIntent(
                    target_device_id=1,
                    action="PICK_AND_PUT",
                    parameters={"target": "A1"},
                )
            ]
        )
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"

    def test_create_result_with_wait(self):
        """测试创建带等待条件的结果"""
        result = PluginResult(
            wait=WaitIntent(
                wait_type="COMMAND_RESULT",
                wait_token="cmd-123",
                deadline_seconds=60,
            )
        )
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"

    def test_create_result_with_failure(self):
        """测试创建带失败归因的结果"""
        result = PluginResult(
            failure=FailureIntent(
                domain="HARDWARE",
                code="DEVICE_OFFLINE",
                message="Device is not responding",
            )
        )
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"

    def test_create_result_with_complete(self):
        """测试创建完成标记的结果"""
        result = PluginResult(complete=True)
        assert result.complete is True

    def test_create_full_result(self):
        """测试创建完整结果"""
        result = PluginResult(
            transition="detect_ok",
            context_patch={"detect_value": 0.95},
            decisions=[{"decision_type": "QUALITY_CHECK", "result": "PASS"}],
            commands=[CommandIntent(target_device_id=1, action="MOVE_FORWARD")],
            complete=True,
        )
        assert result.transition == "detect_ok"
        assert result.context_patch["detect_value"] == 0.95
        assert len(result.decisions) == 1
        assert len(result.commands) == 1
        assert result.complete is True

    def test_result_is_pydantic_model(self):
        """测试结果是 Pydantic 模型，支持序列化"""
        result = PluginResult(
            transition="test",
            context_patch={"key": "value"},
        )
        # 测试 model_dump
        data = result.model_dump()
        assert data["transition"] == "test"
        assert data["context_patch"]["key"] == "value"

        # 测试 model_json
        json_str = result.model_dump_json()
        assert '"transition":"test"' in json_str
