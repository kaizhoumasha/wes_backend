"""
SMT 分类插件集成测试

验证 SmtClassifierPlugin 功能正确性。
"""

from unittest.mock import MagicMock

import pytest

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.workline_plugins.smt_classifier import SmtClassifierPlugin, smt_classifier_plugin
from src.workline_runtime.contracts import SixInOne


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
        """测试扫码 OK 流程会进入测量等待态。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.failure is None
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].action == "MEASUREMENT_REEL"
        assert result.commands[0].target_device_id == 123
        assert result.commands[0].parameters["pkg_id"] == "SVYU00125TP4LCR02_2"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MEASUREMENT_REEL-")
        assert result.wait.deadline_seconds == 300
        assert result.context_patch["device_code"] == "SCANNER01"
        assert result.context_patch["location"] == "LOC01"
        assert result.context_patch["step_code"] == "WAITING_MEASUREMENT"
        assert len(result.context_patch["barcodes"]) == 6

    @pytest.mark.asyncio
    async def test_scan_completed_incomplete_barcodes_routes_to_scan_ng(self, plugin, mock_context):
        """测试条码不完整时会进入 scan_ng，并继续等待 NG 分流结果。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_scan_completed_ng_flow(self, plugin, mock_context):
        """测试扫码 NG 流程 - 命中业务 NG 规则。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "LOTSIZENG_001",
                "location": "LOC01",
            },
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
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_scan_completed_requires_data(self, plugin, mock_context):
        """测试扫码事件缺少 data 时返回 MISSING_SCAN_DATA。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
        }

        inbox = MagicMock()
        inbox.id = 11
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "MISSING_SCAN_DATA"
        assert not result.commands

    @pytest.mark.asyncio
    async def test_scan_invalid_barcode(self, plugin, mock_context):
        """测试无效条码时会进入 scan_ng，并继续等待 NG 分流结果。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "X",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

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

    @pytest.mark.asyncio
    async def test_scan_completed_accepts_canonical_event_type(self, plugin, mock_context):
        """测试粗分机插件可按标准化 canonical_event_type 路由扫码事件。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "VENDOR_SCAN_DONE",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 101
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}
        mock_context.normalized_input = MagicMock(canonical_event_type="SCAN_COMPLETED")

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MEASUREMENT_REEL"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MEASUREMENT_REEL-")

    @pytest.mark.asyncio
    async def test_estop_event_returns_hardware_failure(self, plugin, mock_context):
        """测试急停事件会直接落到硬件失败。"""
        payload = {
            "device_code": "ARM01",
            "event_type": "ESTOP_PRESSED",
            "data": None,
        }

        inbox = MagicMock()
        inbox.id = 102
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "WAITING_PICK_PLACE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ESTOP"
        assert result.failure.message == "急停触发: ARM01"

    # ========== 命令结果测试 ==========

    @pytest.mark.asyncio
    async def test_measurement_reel_success(self, plugin, mock_context):
        """测试测量成功会推进到流水线传输。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_MEASUREMENT",
        }

        payload = {
            "command_code": "CMD-MEASURE-001",
            "command_type": "MEASUREMENT_REEL",
            "result": "SUCCESS",
            "device_code": "ARM01",
            "data": {
                "pkg_id": "SVYU00125TP4LCR02_2",
                "reel_diameter": 178.5,
                "reel_thickness": 12.3,
            },
        }

        inbox = MagicMock()
        inbox.id = 2
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "pick_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MOVE_FORWARD"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MOVE_FORWARD-")
        assert result.commands[0].parameters["pkg_id"] == "SVYU00125TP4LCR02_2"
        assert result.context_patch["pkg_id"] == "SVYU00125TP4LCR02_2"
        assert result.context_patch["reel_diameter"] == 178.5
        assert result.context_patch["reel_thickness"] == 12.3
        assert result.context_patch["step_code"] == "WAITING_CONVEYOR"

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_data(self, plugin, mock_context):
        """测试测量成功但缺少 data 时会进入 measurement_ng。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_MEASUREMENT",
        }

        payload = {
            "command_code": "CMD-MEASURE-002",
            "command_type": "MEASUREMENT_REEL",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 22
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "measurement_ng"
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "MEASUREMENT_DATA_MISSING"

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_pkg_id(self, plugin, mock_context):
        """测试测量成功但缺少 PkgID/pkg_id 时必须显式失败，不能继续下发 MOVE_FORWARD。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_MEASUREMENT",
        }

        payload = {
            "command_code": "CMD-MEASURE-002A",
            "command_type": "MEASUREMENT_REEL",
            "result": "SUCCESS",
            "device_code": "ARM01",
            "data": {
                "reel_diameter": 178.5,
                "reel_thickness": 12.3,
            },
        }

        inbox = MagicMock()
        inbox.id = 222
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"
        assert result.failure.message == "测量成功回调缺少 PkgID/pkg_id"
        assert result.transition is None
        assert result.commands == []

    @pytest.mark.asyncio
    async def test_measurement_reel_result_requires_standard_envelope(self, plugin, mock_context):
        """测试测量结果缺少标准包络时返回 PAYLOAD_INVALID。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_MEASUREMENT",
        }

        payload = {
            "command_code": "CMD-MEASURE-003",
            "command_type": "MEASUREMENT_REEL",
            "result": "SUCCESS",
        }

        inbox = MagicMock()
        inbox.id = 23
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"

    @pytest.mark.asyncio
    async def test_pick_success(self, plugin, mock_context):
        """测试抓取成功。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",  # 正确的初始状态
            "barcode": "LOTABC123",
        }

        payload = {
            "command_code": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM01",
            "data": {
                "reel_diameter": "178.5",
                "reel_thickness": "12.3",
            },
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "pick_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MOVE_FORWARD"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MOVE_FORWARD-")
        assert result.context_patch["reel_diameter"] == "178.5"
        assert result.context_patch["reel_thickness"] == "12.3"
        assert result.context_patch["step_code"] == "WAITING_CONVEYOR"

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
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "ARM_ERROR",
                "error_message": "机械臂错误",
            },
        }

        inbox = MagicMock()
        inbox.id = 3
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ARM_ERROR"

    @pytest.mark.asyncio
    async def test_pick_failed_dimension_error_routes_to_inspection_ng(self, plugin, mock_context):
        """测试尺寸检测异常会进入 inspection_ng 并回送 NG 平台。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
            "barcode": "LOTABC123",
        }

        payload = {
            "command_code": "CMD-001A",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "INSPECTION_SIZE_NG",
                "error_message": "料盘尺寸检测异常",
            },
        }

        inbox = MagicMock()
        inbox.id = 31
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "inspection_ng"
        assert result.failure is None
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.commands[0].parameters["barcode"] == "LOTABC123"
        assert result.commands[0].parameters["source_type"] == "PIPELINE_PLATFORM"
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.context_patch["inspection_error"] == "INSPECTION_SIZE_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_pick_failed_standard_error_routes_to_manual_hold(self, plugin, mock_context):
        """测试标准设备错误码会把会话切到 MANUAL_HOLD，而不是直接终态失败。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
        }

        payload = {
            "command_code": "CMD-001B",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "PICK_AND_PUT_FAILED",
                "error_message": "机械臂搬运失败",
            },
        }

        inbox = MagicMock()
        inbox.id = 32
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "manual_hold"
        assert result.failure is None
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["manual_hold_reason_code"] == "PICK_AND_PUT_FAILED"
        assert result.context_patch["manual_hold_reason_message"] == "机械臂搬运失败"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_pick_failed_scan_failed_routes_to_manual_hold(self, plugin, mock_context):
        """测试 SCAN_FAILED 当前会直接进入 MANUAL_HOLD。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
        }

        payload = {
            "command_code": "CMD-001C",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "SCAN_FAILED",
                "error_message": "扫码执行失败",
            },
        }

        inbox = MagicMock()
        inbox.id = 33
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "manual_hold"
        assert result.failure is None
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["manual_hold_reason_code"] == "SCAN_FAILED"
        assert result.context_patch["manual_hold_reason_message"] == "扫码执行失败"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_pick_failed_accepts_normalized_failure_alias(self, plugin, mock_context):
        """测试粗分机插件可按标准化失败语义兼容 vendor ERROR 结果。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_PICK_PLACE",
        }
        mock_context.normalized_input = MagicMock(
            command_type="PICK_AND_PUT",
            source_result="ERROR",
            normalized_result="TERMINAL_FAILURE",
        )

        payload = {
            "command_code": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "ERROR",
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "ARM_ERROR",
                "error_message": "机械臂错误",
            },
        }

        inbox = MagicMock()
        inbox.id = 103
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ARM_ERROR"

    @pytest.mark.asyncio
    async def test_pick_success_rejects_unexpected_state(self, plugin, mock_context):
        """测试抓取成功在非法状态下返回 STATE_MISMATCH。"""
        mock_context.session.context_json = {
            "step_code": "IDLE",
        }

        payload = {
            "command_code": "CMD-010",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        inbox = MagicMock()
        inbox.id = 104
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "SOFTWARE"
        assert result.failure.code == "STATE_MISMATCH"

    @pytest.mark.asyncio
    async def test_pick_failed_rejects_unexpected_state(self, plugin, mock_context):
        """测试抓取失败在非法状态下返回 STATE_MISMATCH。"""
        mock_context.session.context_json = {
            "step_code": "IDLE",
        }

        payload = {
            "command_code": "CMD-011",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM01",
            "error_detail": {
                "error_code": "ARM_ERROR",
                "error_message": "机械臂错误",
            },
        }

        inbox = MagicMock()
        inbox.id = 105
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "SOFTWARE"
        assert result.failure.code == "STATE_MISMATCH"

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
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["step_code"] == "WAITING_OUTPUT"
        assert "bin_location" in result.context_patch

    @pytest.mark.asyncio
    async def test_conveyor_failed(self, plugin, mock_context):
        """测试流水线传输失败。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_CONVEYOR",
        }

        payload = {
            "command_code": "CMD-003",
            "command_type": "MOVE_FORWARD",
            "result": "FAILED",
            "device_code": "CONVEYOR01",
            "error_detail": {
                "error_code": "CONVEYOR_ERROR",
                "error_message": "流水线卡住",
            },
        }

        inbox = MagicMock()
        inbox.id = 5
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "CONVEYOR_ERROR"
        assert result.failure.message == "流水线卡住"

    @pytest.mark.asyncio
    async def test_output_success(self, plugin, mock_context):
        """测试出料机械臂成功后会完成会话。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_OUTPUT",
        }

        payload = {
            "command_code": "CMD-004",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_code": "ARM02",
        }

        inbox = MagicMock()
        inbox.id = 6
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "output_ok"
        assert result.complete is True
        assert result.context_patch["step_code"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_output_failed(self, plugin, mock_context):
        """测试出料机械臂失败会按标准化错误信息返回。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_OUTPUT",
        }

        payload = {
            "command_code": "CMD-005",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM02",
            "error_detail": {
                "error_code": "OUTPUT_ERROR",
                "error_message": "出料异常",
            },
        }

        inbox = MagicMock()
        inbox.id = 7
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "OUTPUT_ERROR"
        assert result.failure.message == "出料异常"

    @pytest.mark.asyncio
    async def test_output_failed_standard_error_routes_to_manual_hold(self, plugin, mock_context):
        """测试出料阶段的标准设备错误码也应进入 MANUAL_HOLD。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_OUTPUT",
        }

        payload = {
            "command_code": "CMD-005A",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM02",
            "error_detail": {
                "error_code": "PICK_AND_PUT_FAILED",
                "error_message": "出料机械臂搬运失败",
            },
        }

        inbox = MagicMock()
        inbox.id = 70
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "manual_hold"
        assert result.failure is None
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["manual_hold_reason_code"] == "PICK_AND_PUT_FAILED"
        assert result.context_patch["manual_hold_reason_message"] == "出料机械臂搬运失败"
        assert result.context_patch["step_code"] == "WAITING_OUTPUT"

    @pytest.mark.asyncio
    async def test_output_failed_bin_full_routes_to_manual_hold(self, plugin, mock_context):
        """测试 BIN_FULL 不再直接终态失败，而是进入 MANUAL_HOLD。"""
        mock_context.session.context_json = {
            "step_code": "WAITING_OUTPUT",
        }

        payload = {
            "command_code": "CMD-005B",
            "command_type": "PICK_AND_PUT",
            "result": "FAILED",
            "device_code": "ARM02",
            "error_detail": {
                "error_code": "BIN_FULL",
                "error_message": "料箱已满",
            },
        }

        inbox = MagicMock()
        inbox.id = 71
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "manual_hold"
        assert result.failure is None
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["manual_hold_reason_code"] == "BIN_FULL"
        assert result.context_patch["manual_hold_reason_message"] == "料箱已满"
        assert result.context_patch["step_code"] == "WAITING_OUTPUT"

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
    async def test_idle_to_waiting_measurement(self, plugin, mock_context):
        """测试 IDLE → WAITING_MEASUREMENT 迁移。"""
        mock_context.session.context_json = {"step_code": "IDLE"}

        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch.get("step_code") == "WAITING_MEASUREMENT"


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
        """测试完整六合一码判定为 OK。"""
        result = barcode_decision_service.evaluate(
            SixInOne(
                HHPN="620100L00-011-G",
                MfrPN="CC0402JRNPO9BN220",
                Qty="7387",
                DateCode="122625",
                LotCode="8904936031",
                PkgID="SVYU00125TP4LCR02_2",
            )
        )

        assert result.decision == BarcodeDecisionType.OK
        assert result.business_key
        assert result.pkg_id == "SVYU00125TP4LCR02_2"
        assert len(result.barcodes) == 6

    def test_decide_invalid_barcode(self):
        """测试无效 PkgID 判定为 INVALID。"""
        result = barcode_decision_service.evaluate(
            SixInOne(
                HHPN="620100L00-011-G",
                MfrPN="CC0402JRNPO9BN220",
                Qty="7387",
                DateCode="122625",
                LotCode="8904936031",
                PkgID="AB",
            )
        )

        assert result.decision == BarcodeDecisionType.INVALID
        assert result.reason_code == "BARCODE_INVALID"

    def test_decide_business_ng_barcode(self):
        """测试命中业务规则的 PkgID 判定为 NG。"""
        result = barcode_decision_service.evaluate(
            SixInOne(
                HHPN="620100L00-011-G",
                MfrPN="CC0402JRNPO9BN220",
                Qty="7387",
                DateCode="122625",
                LotCode="8904936031",
                PkgID="LOTSIZENG_001",
            )
        )

        assert result.decision == BarcodeDecisionType.NG
        assert result.reason_code == "SCAN_NG_BY_RULE"
