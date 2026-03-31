"""
SMT 粗分机工作线集成测试脚本

测试 SmtClassifierPlugin 与 Mock 服务的交互。

运行方式:
    PYTHONPATH=. uv run python tests/integration/test_smt_classifier_integration.py
"""

from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier.plugin import SmtClassifierPlugin
from src.workline_plugins.smt_classifier.state_machine import (
    SmtClassifierStageStatus,
    get_valid_stage_transitions,
)


class TestSmtClassifierIntegration:
    """SMT 粗分机插件集成测试"""

    @pytest.fixture
    def plugin(self):
        """创建插件实例"""
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context(self):
        """创建模拟的插件上下文"""
        ctx = MagicMock()
        ctx.session = MagicMock()
        ctx.session.id = 1001
        ctx.session.workline_id = 2001
        ctx.session.status = "NEW"
        ctx.session.context_json = {}
        ctx.session.barcode = None
        ctx.workline = MagicMock()
        ctx.workline.config = {}
        ctx.devices_by_role = {}
        ctx.correlation_id = "corr-test-123"
        ctx.logger = MagicMock()
        ctx.get_device_by_role = MagicMock(return_value=None)
        return ctx

    @pytest.fixture
    def mock_inbox(self):
        """创建模拟的 Inbox"""
        inbox = MagicMock()
        inbox.id = 5001
        inbox.kind = "DEVICE_EVENT"
        inbox.device_id = 1
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_full_ok_flow_integration(self, plugin, mock_context, mock_inbox):
        """测试完整的 OK 流程集成"""
        mock_input_arm = MagicMock()
        mock_input_arm.id = 1
        mock_context.get_device_by_role = MagicMock(return_value=mock_input_arm)

        # Step 1: 扫码 OK 事件
        mock_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": "SCAN_COMPLETED",
            "location_id": "LEFT_STATION_INPUT",  # 使用 location_id
            "barcode": "BARCODE001",
            "scan_result": "OK",
        }

        result = await plugin.on_device_event(mock_context, mock_inbox)

        # 验证结果 - 插件返回业务事件类型 scan_ok
        assert result is not None
        assert result.transition == "scan_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.commands[0].target_device_id == mock_input_arm.id
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.context_patch.get("stage") == "WAITING_INSPECTION"

        # Step 2: 验证状态机迁移
        valid_transitions = get_valid_stage_transitions("IDLE")
        assert "scan_ok" in valid_transitions
        assert "scan_ng" in valid_transitions

    @pytest.mark.asyncio
    async def test_scan_ng_flow_integration(self, plugin, mock_context, mock_inbox):
        """测试扫码 NG 流程集成"""
        mock_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": "SCAN_COMPLETED",
            "location_id": "LEFT_STATION_INPUT",  # 使用 location_id 而不是 location
            "barcode": None,
            "scan_result": "NG",  # 设置 scan_result 为 NG
        }

        # 设置 mock 设备 - INPUT_ARM
        mock_input_arm = MagicMock()
        mock_input_arm.id = 1
        mock_context.get_device_by_role = MagicMock(return_value=mock_input_arm)

        result = await plugin.on_device_event(mock_context, mock_inbox)

        # 验证 NG 处理 - 插件返回 scan_ng
        assert result is not None
        assert result.transition == "scan_ng"
        assert result.commands  # 应该生成抓取放置命令
        assert result.wait is not None  # 应该设置等待

    @pytest.mark.asyncio
    async def test_estop_handling_integration(self, plugin, mock_context, mock_inbox):
        """测试急停处理集成"""
        mock_context.session.status = "RUNNING"
        mock_inbox.payload_json = {
            "event_type": "ESTOP_PRESSED",
        }

        result = await plugin.on_device_event(mock_context, mock_inbox)

        # 验证急停处理 - 插件返回 estop
        assert result is not None
        assert result.transition == "estop"

        # 验证状态机允许 estop 从任意状态触发
        for status in SmtClassifierStageStatus:
            valid = get_valid_stage_transitions(status.value)
            assert "estop" in valid, f"estop should be valid from {status.value}"

    @pytest.mark.asyncio
    async def test_manual_hold_resume_integration(self, plugin, mock_context, mock_inbox):
        """测试人工暂停恢复集成"""
        # 测试人工暂停 - 插件从 payload_json 读取 operation_type
        mock_context.session.status = "RUNNING"
        mock_inbox.kind = "MANUAL_HOLD"
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_HOLD",
            "reason": "manual test",
        }

        result = await plugin.on_manual_operation(mock_context, mock_inbox)
        assert result.transition == "manual_hold"

        # 测试人工恢复
        mock_context.session.status = "MANUAL_HOLD"
        mock_inbox.kind = "MANUAL_RESUME"
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_RESUME",
        }

        result = await plugin.on_manual_operation(mock_context, mock_inbox)
        assert result.transition == "manual_resume"

    @pytest.mark.asyncio
    async def test_timeout_handling_integration(self, plugin, mock_context, mock_inbox):
        """测试超时处理集成"""
        mock_context.session.status = "WAITING_DEVICE_RESULT"
        mock_context.session.context_json = {
            "stage": "WAITING_CONVEYOR",
            "current_wait_type": "COMMAND_RESULT",
            "retry_count": 0,
        }
        mock_inbox.kind = "TIMER_TIMEOUT"

        result = await plugin.on_timeout(mock_context, mock_inbox)

        # 验证超时处理
        assert result is not None
        assert result.transition == "timeout"


class TestStateMachineIntegration:
    """状态机集成测试"""

    def test_all_states_have_valid_transitions(self):
        """验证所有状态都有有效的迁移"""
        for status in SmtClassifierStageStatus:
            transitions = get_valid_stage_transitions(status.value)
            # 终态可能没有迁移，但其他状态应该有
            if status not in [SmtClassifierStageStatus.COMPLETED]:
                assert len(transitions) > 0, f"Status {status.value} has no valid transitions"

    def test_wildcard_transitions_from_all_states(self):
        """验证通配符迁移可以从任意状态触发"""
        wildcard_transitions = ["estop", "timeout", "command_failed"]

        for status in SmtClassifierStageStatus:
            valid = get_valid_stage_transitions(status.value)
            for transition in wildcard_transitions:
                assert transition in valid, f"{transition} should be valid from {status.value}"

    def test_happy_path_workflow(self):
        """测试完整正常流程的状态迁移"""
        workflow = [
            ("IDLE", "scan_ok", "WAITING_INSPECTION"),
            ("WAITING_INSPECTION", "inspection_ok", "WAITING_CONVEYOR"),
            ("WAITING_CONVEYOR", "conveyor_complete", "WAITING_OUTPUT"),
            ("WAITING_OUTPUT", "output_handled", "COMPLETED"),
        ]

        for from_state, transition, _expected_to_state in workflow:
            valid = get_valid_stage_transitions(from_state)
            assert transition in valid, f"{transition} should be valid from {from_state}"

    def test_scan_ng_workflow(self):
        """测试扫码 NG 流程的状态迁移"""
        workflow = [
            ("IDLE", "scan_ng", "WAITING_PICK_PLACE"),
            ("WAITING_PICK_PLACE", "ng_handled", "COMPLETED"),
        ]

        for from_state, transition, _expected_to_state in workflow:
            valid = get_valid_stage_transitions(from_state)
            assert transition in valid, f"{transition} should be valid from {from_state}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
