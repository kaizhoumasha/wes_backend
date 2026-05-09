"""SMT 分类插件命令结果测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.workline_runtime.services import WorklineRuntimeServices


class TestSmtClassifierPluginCommandResults:
    """SMT 分类插件命令结果测试。"""

    @pytest.mark.asyncio
    async def test_pick_success_completes_scan_ng_flow(self, plugin, mock_context):
        """测试扫码 NG 分流命令成功后直接完成。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {
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
        assert result.context_patch["ng_handled"] is True

    @pytest.mark.asyncio
    async def test_measurement_reel_success(self, plugin, mock_context):
        """测试测量成功会推进到流水线传输。"""
        mock_context.plugin_state = "WAITING_MEASUREMENT"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_data(self, plugin, mock_context):
        """测试测量成功但缺少 data 时会进入 measurement_ng。"""
        mock_context.plugin_state = "WAITING_MEASUREMENT"
        mock_context.session.context_json = {}

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
    async def test_measurement_reel_success_rejects_flattened_business_fields(self, plugin, mock_context):
        """测试测量成功回调业务字段必须放在 data 中，不能拍平到顶层。"""
        mock_context.plugin_state = "WAITING_MEASUREMENT"
        mock_context.session.context_json = {}

        payload = {
            "command_code": "CMD-MEASURE-002B",
            "command_type": "MEASUREMENT_REEL",
            "result": "SUCCESS",
            "device_code": "ARM01",
            "pkg_id": "SVYU00125TP4LCR02_2",
            "reel_diameter": 178.5,
            "reel_thickness": 12.3,
        }

        inbox = MagicMock()
        inbox.id = 223
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "measurement_ng"
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "MEASUREMENT_DATA_MISSING"
        assert result.commands == []

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_pkg_id(self, plugin, mock_context):
        """测试测量成功但缺少 PkgID/pkg_id 时必须显式失败。"""
        mock_context.plugin_state = "WAITING_MEASUREMENT"
        mock_context.session.context_json = {}

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
        mock_context.plugin_state = "WAITING_MEASUREMENT"
        mock_context.session.context_json = {}

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
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {
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

    @pytest.mark.asyncio
    async def test_pick_failed(self, plugin, mock_context):
        """测试抓取失败。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {}

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
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {
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
        assert len(result.business_decisions) == 1
        assert result.business_decisions[0].classification == "business_decision"
        assert result.business_decisions[0].reason_code == "INSPECTION_SIZE_NG"
        assert result.business_decisions[0].business_key == "LOTABC123"
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.commands[0].parameters["barcode"] == "LOTABC123"
        assert result.commands[0].parameters["source_type"] == "PIPELINE_PLATFORM"
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.context_patch["inspection_error"] == "INSPECTION_SIZE_NG"

    @pytest.mark.asyncio
    async def test_pick_failed_standard_error_routes_to_manual_hold(self, plugin, mock_context):
        """测试标准设备错误码会把会话切到 MANUAL_HOLD。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_pick_failed_scan_failed_routes_to_manual_hold(self, plugin, mock_context):
        """测试 SCAN_FAILED 当前会直接进入 MANUAL_HOLD。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_pick_failed_accepts_normalized_failure_alias(self, plugin, mock_context):
        """测试粗分机插件可按标准化失败语义兼容 vendor ERROR 结果。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {}
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
        mock_context.plugin_state = "IDLE"
        mock_context.session.context_json = {}

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
        mock_context.plugin_state = "IDLE"
        mock_context.session.context_json = {}

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
        """测试流水线传输成功。"""
        mock_context.plugin_state = "WAITING_CONVEYOR"
        mock_context.session.context_json = {
            "barcode": "LEGACY-BARCODE",
            "pkg_id": "CTX-PKG-001",
        }

        payload = {
            "command_code": "CMD-002",
            "command_type": "MOVE_FORWARD",
            "result": "SUCCESS",
            "device_code": "CONVEYOR01",
            "data": {
                "pkg_id": "CALLBACK-PKG-001",
            },
        }

        inbox = MagicMock()
        inbox.id = 4
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "conveyor_ok"
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.commands[0].parameters["barcode"] == "CALLBACK-PKG-001"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["pkg_id"] == "CALLBACK-PKG-001"
        assert "bin_location" in result.context_patch

    @pytest.mark.asyncio
    async def test_conveyor_success_uses_bin_allocator_service(self, plugin, mock_context):
        """测试料箱分配优先走 ctx.services 内部领域服务。"""

        class BinAllocator:
            def allocate(self, barcode: str) -> dict:
                assert barcode == "CALLBACK-PKG-002"
                return {
                    "bin_id": "BIN-SVC-001",
                    "bin_type": "九格箱",
                    "bin_cell_location": "6",
                }

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())
        mock_context.plugin_state = "WAITING_CONVEYOR"
        mock_context.session.context_json = {
            "pkg_id": "CTX-PKG-002",
        }
        inbox = MagicMock()
        inbox.id = 40
        inbox.payload_json = {
            "command_code": "CMD-002B",
            "command_type": "MOVE_FORWARD",
            "result": "SUCCESS",
            "device_code": "CONVEYOR01",
            "data": {
                "pkg_id": "CALLBACK-PKG-002",
            },
        }

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.transition == "conveyor_ok"
        assert result.commands[0].parameters["target_loc"] == "BIN-SVC-001"
        assert result.commands[0].parameters["bin_type"] == "九格箱"
        assert result.context_patch["bin_location"]["bin_id"] == "BIN-SVC-001"

    @pytest.mark.asyncio
    async def test_conveyor_success_requires_callback_pkg_id(self, plugin, mock_context):
        """测试流水线成功回调缺少 data.pkg_id 时必须显式失败。"""
        mock_context.plugin_state = "WAITING_CONVEYOR"
        mock_context.session.context_json = {
            "barcode": "LEGACY-BARCODE",
            "pkg_id": "CTX-PKG-001",
        }

        payload = {
            "command_code": "CMD-002A",
            "command_type": "MOVE_FORWARD",
            "result": "SUCCESS",
            "device_code": "CONVEYOR01",
            "data": {},
        }

        inbox = MagicMock()
        inbox.id = 41
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"
        assert result.failure.message == "MOVE_FORWARD 成功回调缺少 pkg_id"
        assert result.commands == []

    @pytest.mark.asyncio
    async def test_conveyor_failed(self, plugin, mock_context):
        """测试流水线传输失败。"""
        mock_context.plugin_state = "WAITING_CONVEYOR"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_conveyor_failed_requires_nested_error_detail(self, plugin, mock_context):
        """测试流水线失败回调不再接受拍平顶层错误字段。"""
        mock_context.plugin_state = "WAITING_CONVEYOR"
        mock_context.session.context_json = {}

        payload = {
            "command_code": "CMD-003A",
            "command_type": "MOVE_FORWARD",
            "result": "FAILED",
            "device_code": "CONVEYOR01",
            "error_code": "CONVEYOR_ERROR",
            "error_message": "流水线卡住",
        }

        inbox = MagicMock()
        inbox.id = 42
        inbox.payload_json = payload

        result = await plugin.on_command_result(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"
        assert result.failure.message == "MOVE_FORWARD 失败回调缺少 error_detail 字段"

    @pytest.mark.asyncio
    async def test_output_success(self, plugin, mock_context):
        """测试出料机械臂成功后会完成会话。"""
        mock_context.plugin_state = "WAITING_OUTPUT"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_output_failed(self, plugin, mock_context):
        """测试出料机械臂失败会按标准化错误信息返回。"""
        mock_context.plugin_state = "WAITING_OUTPUT"
        mock_context.session.context_json = {}

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
        mock_context.plugin_state = "WAITING_OUTPUT"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_output_failed_bin_full_routes_to_manual_hold(self, plugin, mock_context):
        """测试 BIN_FULL 不再直接终态失败，而是进入 MANUAL_HOLD。"""
        mock_context.plugin_state = "WAITING_OUTPUT"
        mock_context.session.context_json = {}

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

    @pytest.mark.asyncio
    async def test_pick_result_rejects_legacy_command_id_and_device_id(self, plugin, mock_context):
        """测试命令结果不再接受 legacy command_id / device_id。"""
        mock_context.plugin_state = "WAITING_PICK_PLACE"
        mock_context.session.context_json = {
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

    @pytest.mark.asyncio
    async def test_timeout(self, plugin, mock_context):
        """测试插件不再处理系统 timeout。"""
        assert not hasattr(plugin, "on" + "_timeout")
