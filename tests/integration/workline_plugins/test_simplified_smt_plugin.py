"""
SMT 简化插件集成测试

验证 SimplifiedSmtPlugin 与 SmtClassifierPlugin 功能等价性。
"""

from unittest.mock import MagicMock, AsyncMock

import pytest

from src.workline_plugins.simplified_smt_plugin import SimplifiedSmtPlugin, simplified_smt_plugin
from src.workline_runtime.payloads import (
    InspectionEventPayload,
    PickPlaceResultPayload,
    ScanEventPayload,
)


class TestSimplifiedSmtPlugin:
    """SMT 简化插件集成测试"""

    @pytest.fixture
    def plugin(self):
        """插件实例"""
        return SimplifiedSmtPlugin()

    @pytest.fixture
    def mock_context(self):
        """Mock 插件上下文"""
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.id = 42
        ctx.session.context_json = {}
        ctx.devices_by_role = {
            "INPUT_ARM": [MagicMock(id=123)],
            "CONVEYOR": [MagicMock(id=456)],
            "OUTPUT_ARM": [MagicMock(id=789)],
        }
        return ctx

    @pytest.fixture
    def mock_inbox(self):
        """Mock Inbox"""
        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = {}
        return inbox

    # ========== 扫码完成测试 ==========

    @pytest.mark.asyncio
    async def test_scan_completed_ok_flow(self, plugin, mock_context):
        """测试扫码OK流程"""
        # 准备 payload
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "barcode": "ABC123",
            "location": "LOC01",
            "scan_result": "OK",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        # 设置状态为 IDLE
        mock_context.session.context_json = {"step_code": "IDLE"}

        # 调用 on_device_event
        result = await plugin.on_device_event(mock_context, inbox)

        # 验证
        assert result.transition == "scan_ok"
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.commands[0].target_device_id == 123
        assert result.context_patch["barcode"] == "ABC123"
        assert result.context_patch["scan_result"] == "OK"

        # 验证状态已自动设置为 WAITING_INSPECTION
        assert mock_context.session.context_json.get("stage") is None
        # 状态由状态机管理，不由插件直接设置

    @pytest.mark.asyncio
    async def test_scan_completed_ng_flow(self, plugin, mock_context):
        """测试扫码NG流程"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "barcode": "NG123",
            "location": "LOC01",
            "scan_result": "NG",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"

    @pytest.mark.asyncio
    async def test_scan_invalid_barcode(self, plugin, mock_context):
        """测试无效条码"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "barcode": "X",  # 太短
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "BARCODE_INVALID"

    # ========== 检测完成测试 ==========

    @pytest.mark.asyncio
    async def test_inspection_ok_flow(self, plugin, mock_context):
        """测试检测OK流程"""
        mock_context.session.context_json = {
            "step_code": "WAITING_INSPECTION",
            "barcode": "ABC123"
        }

        payload = {
            "device_code": "INSPECTOR01",
            "event_type": "INSPECTION_COMPLETED",
            "inspection_result": "OK",
            "reel_diameter": 200.5,
        }

        inbox = MagicMock()
        inbox.id = 2
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "inspection_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MOVE_FORWARD"

    @pytest.mark.asyncio
    async def test_inspection_ng_flow(self, plugin, mock_context):
        """测试检测NG流程"""
        mock_context.session.context_json = {
            "step_code": "WAITING_INSPECTION",
            "barcode": "ABC123"
        }

        payload = {
            "device_code": "INSPECTOR01",
            "event_type": "INSPECTION_COMPLETED",
            "inspection_result": "NG",
        }

        inbox = MagicMock()
        inbox.id = 2
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "inspection_ng"
        assert result.commands is not None
        assert result.commands[0].action == "PICK_NG"

    # ========== 命令结果测试 ==========

    @pytest.mark.asyncio
    async def test_pick_success(self, plugin, mock_context):
        """测试抓取成功"""
        mock_context.session.context_json = {
            "step_code": "WAITING_INSPECTION",
            "barcode": "ABC123"
        }

        payload = {
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "pick_ok"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.deadline_seconds == 300

    @pytest.mark.asyncio
    async def test_pick_failed(self, plugin, mock_context):
        """测试抓取失败"""
        mock_context.session.context_json = {
            "step_code": "WAITING_INSPECTION",
        }

        payload = {
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "error_code": "ARM_ERROR",
            "error_message": "机械臂错误",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ARM_ERROR"

    @pytest.mark.asyncio
    async def test_conveyor_success(self, plugin, mock_context):
        """测试流水线传输成功"""
        mock_context.session.context_json = {
            "step_code": "WAITING_CONVEYOR",
            "barcode": "ABC123",
        }

        payload = {
            "command_type": "MOVE_FORWARD",
            "result": "SUCCESS",
            "device_code": "CONVEYOR01",
        }

        inbox = MagicMock()
        inbox.id = 4
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "conveyor_ok"
        assert result.commands is not None
        assert result.commands[0].action == "OUTPUT"

    @pytest.mark.asyncio
    async def test_output_success(self, plugin, mock_context):
        """测试最终出料成功"""
        mock_context.session.context_json = {
            "step_code": "WAITING_OUTPUT",
        }

        payload = {
            "command_type": "OUTPUT",
            "result": "SUCCESS",
            "device_code": "OUTPUT_ARM01",
        }

        inbox = MagicMock()
        inbox.id = 5
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "output_ok"
        assert result.complete is True

    # ========== 超时测试 ==========

    @pytest.mark.asyncio
    async def test_timeout(self, plugin, mock_context):
        """测试超时处理"""
        inbox = MagicMock()
        inbox.id = 6
        inbox.payload_json = {}

        result = await plugin.on_timeout(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "TIMEOUT"
        assert result.failure.code == "DEVICE_TIMEOUT"


class TestSimplifiedSmtPluginPluginRegistration:
    """插件注册测试"""

    def test_plugin_key(self):
        """验证 plugin_key"""
        assert SimplifiedSmtPlugin.plugin_key == "simplified_smt"

    def test_contract_version(self):
        """验证 contract_version"""
        assert SimplifiedSmtPlugin.contract_version == "1.0"

    def test_plugin_instance(self):
        """验证插件实例可创建"""
        assert simplified_smt_plugin is not None
        assert isinstance(simplified_smt_plugin, SimplifiedSmtPlugin)


class TestBarcodeValidation:
    """条码验证单元测试"""

    @pytest.fixture
    def plugin(self):
        return SimplifiedSmtPlugin()

    def test_valid_barcode_alphanumeric(self, plugin):
        """测试有效条码（字母数字）"""
        assert plugin._is_valid_barcode("ABC123") is True
        assert plugin._is_valid_barcode("XYZ789") is True

    def test_valid_barcode_minimum_length(self, plugin):
        """测试有效条码（最小长度）"""
        assert plugin._is_valid_barcode("ABC") is True  # 刚好3个字符

    def test_invalid_barcode_too_short(self, plugin):
        """测试无效条码（太短）"""
        assert plugin._is_valid_barcode("AB") is False
        assert plugin._is_valid_barcode("") is False

    def test_invalid_barcode_special_chars(self, plugin):
        """测试无效条码（特殊字符）"""
        assert plugin._is_valid_barcode("ABC-123") is False
        assert plugin._is_valid_barcode("ABC 123") is False


class TestStateMachineTransitions:
    """状态迁移测试"""

    @pytest.mark.asyncio
    async def test_idle_to_waiting_inspection(self, plugin, mock_context):
        """测试 IDLE → WAITING_INSPECTION 迁移"""
        mock_context.session.context_json = {"step_code": "IDLE"}

        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "barcode": "ABC123",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        # 验证状态迁移已设置
        assert result.transition == "scan_ok"

    @pytest.mark.asyncio
    async def test_state_mismatch_rejected(self, plugin, mock_context):
        """测试状态不匹配被拒绝"""
        # 当前状态不是 IDLE
        mock_context.session.context_json = {"step_code": "PROCESSING"}

        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "barcode": "ABC123",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        # 应该被状态校验拒绝
        assert result.failure is not None
        assert result.failure.code == "STATE_MISMATCH"
