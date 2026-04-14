"""
SMT 分类插件集成测试

验证 SmtClassifierPlugin 功能正确性。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.workline_plugins.smt_classifier import SmtClassifierPlugin, smt_classifier_plugin


class TestSmtClassifierPlugin:
    """SMT 分类插件集成测试"""

    @pytest.fixture
    def plugin(self):
        """插件实例"""
        return SmtClassifierPlugin()

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

    # ========== 扫码完成测试 ==========

    @pytest.mark.asyncio
    async def test_scan_completed_ok_flow(self, plugin, mock_context):
        """测试扫码OK流程 - 使用完整的 SixInOne 字段"""
        # 准备 payload - 使用完整的 SixInOne 字段（无连字符等特殊字符）
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTABC123",  # SixInOne: 批次码
            "DateCode": "20260409",  # SixInOne: 日期码
            "Qty": "100",  # SixInOne: 数量
            "ProductNo": "PN001",  # SixInOne: 产品PN码
            "MfrPN": "MFR002",  # SixInOne: 制造商PN码
            "PONumber": "PO2026040901",  # SixInOne: 订单码
            "location": "LOC01",
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
        assert result.commands[0].parameters["source_type"] == "INPUT_PLATFORM"
        assert result.commands[0].parameters["target_type"] == "PIPELINE_PLATFORM"
        assert result.commands[0].parameters["source_loc"] == "LOC01"
        assert result.commands[0].parameters["target_loc"] == "STATION_PIPELINE1_INPUT1"
        assert result.context_patch["barcode"] == "LOTABC123"  # first_barcode 优先使用 LotCode
        assert "barcodes" in result.context_patch  # 包含所有条码列表
        assert len(result.context_patch["barcodes"]) == 6  # 完整的 6 个条码

    @pytest.mark.asyncio
    async def test_scan_completed_ok_flow_multiple_barcodes(self, plugin, mock_context):
        """测试扫码OK流程 - 使用多个 SixInOne 字段"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTABC123",
            "DateCode": "20260409",
            "ProductNo": "PN001",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch["barcode"] == "LOTABC123"
        assert len(result.context_patch["barcodes"]) == 3  # LotCode, DateCode, ProductNo

    @pytest.mark.asyncio
    async def test_scan_completed_ng_flow(self, plugin, mock_context):
        """测试扫码NG流程 - 命中业务 NG 规则"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTSIZENG",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"

    @pytest.mark.asyncio
    async def test_scan_invalid_barcode(self, plugin, mock_context):
        """测试无效条码"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "X",  # 太短
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

    @pytest.mark.asyncio
    async def test_pick_success_completes_scan_ng_flow(self, plugin, mock_context):
        """测试扫码 NG 分流命令成功后直接完成。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
            "barcode": "LOTSIZENG",
            "pick_place_reason": "SCAN_NG",
        }

        payload = {
            "command_code": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "pick_ng"
        assert result.complete is True
        assert result.context_patch["step_code"] == "COMPLETED"
        assert result.context_patch["ng_handled"] is True

    @pytest.mark.asyncio
    async def test_scan_completed_rejects_legacy_device_id(self, plugin, mock_context):
        """测试扫码事件不再接受 legacy device_id"""
        payload = {
            "device_id": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTABC123",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"

    # ========== 命令结果测试 ==========

    @pytest.mark.asyncio
    async def test_pick_success(self, plugin, mock_context):
        """测试抓取成功"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",  # 正确的初始状态
            "barcode": "LOTABC123",
        }

        payload = {
            "command_code": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "pick_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MOVE_FORWARD"

    @pytest.mark.asyncio
    async def test_pick_failed(self, plugin, mock_context):
        """测试抓取失败"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",  # 正确的初始状态
        }

        payload = {
            "command_code": "CMD-001",
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
            "barcode": "LOTABC123",
        }

        payload = {
            "command_code": "CMD-002",  # 添加必需字段
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
        assert result.commands[0].action == "PICK_AND_PUT"  # 输出机械臂

    @pytest.mark.asyncio
    async def test_pick_result_rejects_legacy_command_id_and_device_id(self, plugin, mock_context):
        """测试命令结果不再接受 legacy command_id / device_id"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
            "barcode": "LOTABC123",
        }

        payload = {
            "command_id": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_id": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"

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

    # ========== 状态迁移测试 ==========

    @pytest.mark.asyncio
    async def test_idle_to_waiting_pick_place(self, plugin, mock_context):
        """测试 IDLE → WAITING_PICK_PLACE 迁移"""
        mock_context.session.context_json = {"step_code": "IDLE"}

        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTABC123",  # SixInOne 字段
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        # 验证状态迁移已设置
        assert result.transition == "scan_ok"
        assert result.context_patch.get("step_code") == "WAITING_PICK_PLACE"


class TestSmtClassifierPluginPluginRegistration:
    """插件注册测试"""

    def test_plugin_key(self):
        """验证 plugin_key"""
        assert SmtClassifierPlugin.plugin_key == "smt_classifier"

    def test_contract_version(self):
        """验证 contract_version"""
        assert SmtClassifierPlugin.contract_version == "1.0"

    def test_plugin_instance(self):
        """验证插件实例可创建"""
        assert smt_classifier_plugin is not None
        assert isinstance(smt_classifier_plugin, SmtClassifierPlugin)


class TestBarcodeDecisionService:
    """条码业务判定服务测试"""

    def test_decide_ok_barcode(self):
        """测试有效条码判定为 OK。"""
        result = barcode_decision_service.evaluate_scan(
            lot_code="LOTABC123",
            date_code="20260409",
            product_no="PN001",
        )

        assert result.decision == BarcodeDecisionType.OK
        assert result.barcode == "LOTABC123"
        assert result.barcodes == ["LOTABC123", "20260409", "PN001"]

    def test_decide_invalid_barcode(self):
        """测试无效条码判定为 INVALID。"""
        result = barcode_decision_service.evaluate_scan(lot_code="AB")

        assert result.decision == BarcodeDecisionType.INVALID
        assert result.reason_code == "BARCODE_INVALID"

    def test_decide_business_ng_barcode(self):
        """测试命中业务规则的条码判定为 NG。"""
        result = barcode_decision_service.evaluate_scan(
            lot_code="LOTSIZENG",
            date_code="20260409",
        )

        assert result.decision == BarcodeDecisionType.NG
        assert result.reason_code == "SCAN_NG_BY_RULE"
