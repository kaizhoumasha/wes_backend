"""
SmtClassifierPlugin 单元测试

测试 SMT 粗分机工作线插件的核心功能：
- on_device_event: 设备事件处理（扫码完成、急停等）
- on_command_result: 命令结果处理
- on_timeout: 超时处理
- on_external_http: 外部 HTTP 回调
- on_manual_operation: 人工操作处理

设计参考:
- 设计文档: phase2-orchestrator design doc
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workline_plugins.smt_classifier.event_handlers import LocationType
from src.workline_plugins.smt_classifier.plugin import (
    SmtClassifierCommandType,
    SmtClassifierDeviceRole,
    SmtClassifierEventType,
    SmtClassifierPlugin,
    SmtClassifierStage,
    SmtClassifierStepCode,
)
from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)


class TestSmtClassifierPluginEnums:
    """测试插件枚举定义"""

    def test_device_roles_values(self):
        """测试设备角色枚举值"""
        assert SmtClassifierDeviceRole.INPUT_ARM.value == "INPUT_ARM"
        assert SmtClassifierDeviceRole.OUTPUT_ARM.value == "OUTPUT_ARM"
        assert SmtClassifierDeviceRole.CONVEYOR.value == "CONVEYOR"

    def test_stage_values(self):
        """测试阶段枚举值"""
        assert SmtClassifierStage.IDLE.value == "IDLE"
        assert SmtClassifierStage.WAITING_SCAN.value == "WAITING_SCAN"
        assert SmtClassifierStage.SCAN_RESULT_RECEIVED.value == "SCAN_RESULT_RECEIVED"
        assert SmtClassifierStage.WAITING_INSPECTION.value == "WAITING_INSPECTION"
        assert SmtClassifierStage.COMPLETED.value == "COMPLETED"
        assert SmtClassifierStage.ERROR.value == "ERROR"


class TestSmtClassifierPluginBase:
    """SmtClassifierPlugin 基础测试"""

    @pytest.fixture
    def plugin(self):
        """创建 SmtClassifierPlugin 实例"""
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context(self):
        """创建模拟的插件上下文"""
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}
        context.devices_by_role = {}

        # 模拟 get_device_by_role 方法
        def mock_get_device(role, index=0):
            devices = context.devices_by_role.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device
        return context

    @pytest.fixture
    def mock_inbox(self):
        """创建模拟的 Inbox"""
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_plugin_key(self, plugin):
        """测试插件标识"""
        assert plugin.plugin_key == "smt_classifier"

    @pytest.mark.asyncio
    async def test_on_device_event_unknown_type(self, plugin, mock_context, mock_inbox):
        """测试未知事件类型"""
        mock_inbox.payload_json = {"event_type": "UNKNOWN_EVENT"}

        result = await plugin.on_device_event(mock_context, mock_inbox)

        assert isinstance(result, PluginResult)
        assert result.transition is None
        mock_context.logger.warning.assert_called_once()


class TestSmtClassifierPluginScanEvents:
    """测试扫码事件处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context_with_devices(self):
        """创建带有模拟设备的上下文"""
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}
        context.devices_by_role = {}

        # 模拟设备
        input_arm = MagicMock()
        input_arm.id = 10
        output_arm = MagicMock()
        output_arm.id = 20
        conveyor = MagicMock()
        conveyor.id = 30

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
                SmtClassifierDeviceRole.OUTPUT_ARM.value: [output_arm],
                SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_scan_ok_flow(self, plugin, mock_context_with_devices, mock_inbox):
        """测试扫码 OK 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC001",
            "scan_result": "OK",
            "source_type": LocationType.INPUT_PLATFORM.value,
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch["scan_result"] == "OK"
        assert result.context_patch["last_barcode"] == "BC001"
        assert result.context_patch["stage"] == SmtClassifierStage.WAITING_INSPECTION.value
        assert result.context_patch["source_type"] == LocationType.INPUT_PLATFORM.value
        assert result.context_patch["target_type"] == LocationType.PIPELINE_PLATFORM.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.INPUT_PICK_PLACE.value

    @pytest.mark.asyncio
    async def test_scan_ng_flow(self, plugin, mock_context_with_devices, mock_inbox):
        """测试扫码 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC002",
            "scan_result": "NG",
            "source_type": LocationType.INPUT_PLATFORM.value,
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ng"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.commands[0].parameters["params"]["source_type"] == LocationType.INPUT_PLATFORM.value
        assert result.commands[0].parameters["params"]["target_type"] == LocationType.NG_PLATFORM.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.NG_PICK_PLACE.value
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"

    @pytest.mark.asyncio
    async def test_scan_ng_right_station(self, plugin, mock_context_with_devices, mock_inbox):
        """测试右侧作业线扫码 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC003",
            "scan_result": "NG",
            "source_type": LocationType.INPUT_PLATFORM.value,
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ng"
        assert result.commands[0].parameters["params"]["source_type"] == LocationType.INPUT_PLATFORM.value
        assert result.commands[0].parameters["params"]["target_type"] == LocationType.NG_PLATFORM.value


class TestSmtClassifierPluginEstop:
    """测试急停事件处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}
        context.get_device_by_role = MagicMock(return_value=None)
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 789
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_estop_handling(self, plugin, mock_context, mock_inbox):
        """测试急停处理"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.ESTOP_PRESSED.value,
            "timestamp": "2026-03-24T12:00:00Z",
        }

        result = await plugin.on_device_event(mock_context, mock_inbox)

        assert result.transition == "estop"
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ESTOP_PRESSED"
        assert result.context_patch["estop_pressed"] is True


class TestSmtClassifierPluginInspectionEvents:
    """测试检测事件处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context_with_devices(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}
        context.devices_by_role = {}

        input_arm = MagicMock()
        input_arm.id = 10
        conveyor = MagicMock()
        conveyor.id = 30

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
                SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_inspection_ok_start_conveyor(self, plugin, mock_context_with_devices, mock_inbox):
        """测试检测 OK 启动流水线"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "OK",
            "source_type": LocationType.PIPELINE_PLATFORM.value,
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.MOVE_FORWARD.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.PIPELINE_MOVE_FORWARD.value
        assert result.wait is not None

    @pytest.mark.asyncio
    async def test_inspection_ng_flow(self, plugin, mock_context_with_devices, mock_inbox):
        """测试检测 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "NG",
            "source_type": LocationType.PIPELINE_PLATFORM.value,
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ng"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.commands[0].parameters["reason"] == "INSPECTION_NG"


class TestSmtClassifierPluginCommandResult:
    """测试命令结果处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context_with_devices(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {
            "stage": SmtClassifierStage.WAITING_PICK_PLACE.value,
            "source_type": LocationType.PIPELINE_PLATFORM.value,
        }
        context.workline = MagicMock()
        context.workline.line_code = "WL-TEST-01"
        context.config = {}
        context.correlation_id = "corr-test-001"
        context.devices_by_role = {}

        output_arm = MagicMock()
        output_arm.id = 20

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.OUTPUT_ARM.value: [output_arm],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_pick_and_put_ng_completed(self, plugin, mock_context_with_devices, mock_inbox):
        """测试 NG 抓取放置完成"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_PICK_PLACE.value,
            "ng_reason": "SCAN_NG",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "ng_handled"
        assert result.complete is True
        assert result.context_patch["stage"] == SmtClassifierStage.COMPLETED.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.COMPLETED.value

    @pytest.mark.asyncio
    async def test_conveyor_completed(self, plugin, mock_context_with_devices, mock_inbox):
        """测试流水线传输完成"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_CONVEYOR.value,
            "source_type": LocationType.PIPELINE_PLATFORM.value,
            "target_type": LocationType.PIPELINE_PLATFORM.value,
            "barcode": "PKG-001",
            "inspection_result": "OK",
            "reel_diameter": "15inch",
            "thickness": "20",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
        }

        with patch.object(
            plugin,
            "_request_bin_allocation",
            new=AsyncMock(
                return_value=(
                    {
                        "message": "ALLOCATED",
                        "data": {
                            "allocation_status": "ALLOCATED",
                            "target_bin": {
                                "station_location_id": "STATION_OUTPUT1",
                                "rack_id": "RACK_001",
                                "bin_id": "BIN_001",
                                "bin_type": "三格箱",
                                "bin_cell_location": "A1",
                                "reel_layer": "15",
                                "reel_thickness": "20",
                                "reel_diameter": "15inch",
                                "reel_totalthickness": "300",
                            },
                        },
                    },
                    "ALLOC-PKG-001-01",
                    1,
                )
            ),
        ):
            result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "conveyor_complete"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.OUTPUT_PICK_PLACE.value
        assert result.context_patch["target_bin"]["bin_id"] == "BIN_001"

    @pytest.mark.asyncio
    async def test_input_pick_place_result_with_embedded_inspection_ok(self, plugin, mock_context_with_devices, mock_inbox):
        """输入机械臂结果若带检测数据，应直接推进到流水线命令。"""
        input_arm = MagicMock(
            id=10,
            device_role=SmtClassifierDeviceRole.INPUT_ARM.value,
            role_index=1,
            upstream_device_id=None,
        )
        conveyor = MagicMock(
            id=30,
            device_role=SmtClassifierDeviceRole.CONVEYOR.value,
            role_index=1,
            upstream_device_id=10,
        )
        mock_context_with_devices.devices_by_role = {
            SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
            SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
        }
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_INSPECTION.value,
            "step_code": SmtClassifierStepCode.INPUT_PICK_PLACE.value,
        }
        mock_inbox.payload_json = {
            "result": "SUCCESS",
            "data": {
                "location": "STATION_PIPELINE1_INPUT1",
                "reel_diameter": "15inch",
                "reel_thickness": "20",
            },
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.MOVE_FORWARD.value
        assert result.context_patch["inspection_result"] == "OK"
        assert result.context_patch["reel_diameter"] == "15inch"
        assert result.context_patch["thickness"] == "20"
        assert result.context_patch["step_code"] == SmtClassifierStepCode.PIPELINE_MOVE_FORWARD.value

    @pytest.mark.asyncio
    async def test_command_result_step_code_precedence(self, plugin, mock_context_with_devices, mock_inbox):
        """step_code 与 command_type 冲突时优先使用 step_code 推进。"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_OUTPUT.value,
            "step_code": SmtClassifierStepCode.OUTPUT_PICK_PLACE.value,
            "current_location": "STATION_PIPELINE1_OUTPUT1",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "output_handled"
        assert result.complete is True

    @pytest.mark.asyncio
    async def test_command_failure(self, plugin, mock_context_with_devices, mock_inbox):
        """测试命令失败"""
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "FAILED",
            "error_detail": {
                "code": "ARM_ERROR",
                "message": "Arm motion failed",
            },
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "command_failed"
        assert result.failure is not None
        assert result.failure.code == "ARM_ERROR"

    @pytest.mark.asyncio
    async def test_move_forward_uses_upstream_topology(self, plugin, mock_inbox):
        """流水线完成后应按 upstream 拓扑选择当前工作线的出料机械臂。"""

        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {
            "stage": SmtClassifierStage.WAITING_CONVEYOR.value,
            "source_type": LocationType.PIPELINE_PLATFORM.value,
            "target_type": LocationType.PIPELINE_PLATFORM.value,
            "barcode": "PKG-002",
            "inspection_result": "OK",
            "reel_diameter": "15inch",
            "thickness": "20",
        }
        context.workline = MagicMock()
        context.workline.line_code = "WL-TEST-TOPOLOGY"
        context.config = {}
        context.correlation_id = "corr-topology-001"

        input_arm = MagicMock(
            id=31, device_role=SmtClassifierDeviceRole.INPUT_ARM.value, role_index=2, upstream_device_id=None
        )
        conveyor = MagicMock(
            id=32, device_role=SmtClassifierDeviceRole.CONVEYOR.value, role_index=2, upstream_device_id=31
        )
        output_arm = MagicMock(
            id=33, device_role=SmtClassifierDeviceRole.OUTPUT_ARM.value, role_index=2, upstream_device_id=32
        )

        context.devices_by_role = {
            SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
            SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
            SmtClassifierDeviceRole.OUTPUT_ARM.value: [output_arm],
        }
        context.get_device_by_role = MagicMock(return_value=None)

        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
        }

        with patch.object(
            plugin,
            "_request_bin_allocation",
            new=AsyncMock(
                return_value=(
                    {
                        "message": "ALLOCATED",
                        "data": {
                            "allocation_status": "ALLOCATED",
                            "target_bin": {
                                "station_location_id": "STATION_OUTPUT1",
                                "rack_id": "RACK_002",
                                "bin_id": "BIN_002",
                                "bin_type": "三格箱",
                                "bin_cell_location": "B1",
                                "reel_layer": "12",
                                "reel_thickness": "20",
                                "reel_diameter": "15inch",
                                "reel_totalthickness": "300",
                            },
                        },
                    },
                    "ALLOC-PKG-002-01",
                    1,
                )
            ),
        ):
            result = await plugin.on_command_result(context, mock_inbox)

        assert result.transition == "conveyor_complete"
        assert result.commands[0].target_device_id == 33
        assert result.commands[0].parameters["params"]["source_type"] == LocationType.PIPELINE_PLATFORM.value
        assert result.commands[0].parameters["params"]["target_type"] == LocationType.BIN.value

    @pytest.mark.asyncio
    async def test_move_forward_requests_agv_when_bin_unavailable(self, plugin, mock_context_with_devices, mock_inbox):
        """流水线完成后若 allocation 返回 AGV_REQUIRED，应生成 EXTERNAL_HTTP 决策并等待外部回调。"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_CONVEYOR.value,
            "source_type": LocationType.PIPELINE_PLATFORM.value,
            "target_type": LocationType.PIPELINE_PLATFORM.value,
            "barcode": "PKG-003",
            "inspection_result": "OK",
        }
        mock_context_with_devices.config = {
            "agv_dispatch": {"url": "http://agv.mock/api/v1/device/command"},
        }
        mock_context_with_devices.correlation_id = "corr-agv-001"
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
        }

        with patch.object(
            plugin,
            "_request_bin_allocation",
            new=AsyncMock(
                return_value=(
                    {
                        "message": "AGV_REQUIRED",
                        "data": {
                            "allocation_status": "AGV_REQUIRED",
                            "agv_request": {
                                "request_code": "AGV-REQ-001",
                                "from_location": "RACK_BUFFER_A",
                                "to_location": "STATION_OUTPUT1",
                                "rack_type": "SMT_BIN_RACK",
                                "reason": "NO_AVAILABLE_BIN",
                            },
                        },
                    },
                    "ALLOC-PKG-003-01",
                    1,
                )
            ),
        ):
            result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "agv_requested"
        assert result.wait is not None
        assert result.wait.wait_type == "EXTERNAL_HTTP"
        assert result.wait.wait_token == "AGV-REQ-001"
        assert result.decisions[0]["decision_type"] == "EXTERNAL_HTTP_REQUEST"
        assert result.decisions[0]["target_code"] == "http://agv.mock/api/v1/device/command"
        assert result.context_patch["stage"] == SmtClassifierStage.WAITING_AGV_DELIVERY.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.WAITING_AGV_DELIVERY.value
        assert result.commands == []


class TestSmtClassifierPluginTimeout:
    """测试超时处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {
            "stage": SmtClassifierStage.WAITING_SCAN.value,
        }
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_timeout_handling(self, plugin, mock_context, mock_inbox):
        """测试超时处理"""
        result = await plugin.on_timeout(mock_context, mock_inbox)

        assert result.transition == "timeout"
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "TIMEOUT"
        assert result.context_patch["timeout_at_stage"] == SmtClassifierStage.WAITING_SCAN.value


class TestSmtClassifierPluginManualOperation:
    """测试人工操作处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_manual_hold(self, plugin, mock_context, mock_inbox):
        """测试人工暂停"""
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_HOLD",
            "reason": "Equipment check",
        }

        result = await plugin.on_manual_operation(mock_context, mock_inbox)

        assert result.transition == "manual_hold"
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["hold_reason"] == "Equipment check"

    @pytest.mark.asyncio
    async def test_manual_resume(self, plugin, mock_context, mock_inbox):
        """测试人工恢复"""
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_RESUME",
        }

        result = await plugin.on_manual_operation(mock_context, mock_inbox)

        assert result.transition == "manual_resume"
        assert result.context_patch["manual_hold"] is False

    @pytest.mark.asyncio
    async def test_manual_cancel(self, plugin, mock_context, mock_inbox):
        """测试人工取消"""
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_CANCEL",
            "reason": "Quality issue",
        }

        result = await plugin.on_manual_operation(mock_context, mock_inbox)

        assert result.transition == "manual_cancel"
        assert result.complete is True
        assert result.context_patch["cancel_reason"] == "Quality issue"


class TestSmtClassifierPluginExternalHttp:
    """测试外部 HTTP 回调处理"""

    @pytest.fixture
    def plugin(self):
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_context_with_devices(self):
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {
            "source_type": LocationType.PIPELINE_PLATFORM.value,
        }
        context.workline = MagicMock()
        context.workline.line_code = "WL-TEST-EXTERNAL"
        context.config = {}
        context.correlation_id = "corr-external-001"
        context.devices_by_role = {}

        conveyor = MagicMock()
        conveyor.id = 30

        input_arm = MagicMock()
        input_arm.id = 10

        output_arm = MagicMock()
        output_arm.id = 20

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
                SmtClassifierDeviceRole.OUTPUT_ARM.value: [output_arm],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device
        return context

    @pytest.fixture
    def mock_inbox(self):
        inbox = MagicMock()
        inbox.id = 456
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_mes_inspection_ok_callback(self, plugin, mock_context_with_devices, mock_inbox):
        """测试 MES 检测 OK 回调"""
        mock_inbox.payload_json = {
            "callback_type": "MES_INSPECTION_RESULT",
            "barcode": "BC001",
            "inspection_result": "OK",
        }

        result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.MOVE_FORWARD.value

    @pytest.mark.asyncio
    async def test_mes_inspection_ng_callback(self, plugin, mock_context_with_devices, mock_inbox):
        """测试 MES 检测 NG 回调"""
        mock_inbox.payload_json = {
            "callback_type": "MES_INSPECTION_RESULT",
            "barcode": "BC002",
            "inspection_result": "NG",
        }

        result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ng"
        assert len(result.commands) == 1

    @pytest.mark.asyncio
    async def test_wcs_task_completed_callback(self, plugin, mock_context_with_devices, mock_inbox):
        """测试 WCS 任务完成回调"""
        mock_inbox.payload_json = {
            "callback_type": "WCS_TASK_STATUS",
            "task_id": "WCS001",
            "task_status": "COMPLETED",
        }

        result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "wcs_task_complete"
        assert result.context_patch["wcs_task_completed"] is True

    @pytest.mark.asyncio
    async def test_wcs_task_failed_callback(self, plugin, mock_context_with_devices, mock_inbox):
        """测试 WCS 任务失败回调"""
        mock_inbox.payload_json = {
            "callback_type": "WCS_TASK_STATUS",
            "task_id": "WCS002",
            "task_status": "FAILED",
            "error_message": "Connection lost",
        }

        result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "wcs_task_failed"
        assert result.failure is not None
        assert result.failure.code == "WCS_TASK_FAILED"

    @pytest.mark.asyncio
    async def test_agv_task_success_callback_allocates_bin(self, plugin, mock_context_with_devices, mock_inbox):
        """AGV 成功回调后应再次 allocation，并创建 ARM02 出料命令。"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_AGV_DELIVERY.value,
            "step_code": SmtClassifierStepCode.WAITING_AGV_DELIVERY.value,
            "barcode": "PKG-004",
            "inspection_result": "OK",
            "reel_diameter": "15inch",
            "thickness": "20",
            "agv_request_code": "AGV-REQ-002",
        }
        mock_inbox.payload_json = {
            "callback_type": "AGV_TASK_RESULT",
            "correlation_id": "corr-external-001",
            "command_id": "AGV-REQ-002",
            "result": "SUCCESS",
            "data": {"to_location": "STATION_OUTPUT1"},
        }

        with patch.object(
            plugin,
            "_request_bin_allocation",
            new=AsyncMock(
                return_value=(
                    {
                        "message": "ALLOCATED",
                        "data": {
                            "allocation_status": "ALLOCATED",
                            "target_bin": {
                                "station_location_id": "STATION_OUTPUT1",
                                "rack_id": "RACK_003",
                                "bin_id": "BIN_003",
                                "bin_type": "三格箱",
                                "bin_cell_location": "C1",
                                "reel_layer": "15",
                                "reel_thickness": "20",
                                "reel_diameter": "15inch",
                                "reel_totalthickness": "300",
                            },
                        },
                    },
                    "ALLOC-PKG-004-02",
                    2,
                )
            ),
        ):
            result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "agv_completed"
        assert result.commands[0].target_device_id == 20
        assert result.context_patch["target_bin_id"] == "BIN_003"
        assert result.context_patch["allocation_status"] == "ALLOCATED"

    @pytest.mark.asyncio
    async def test_agv_task_failed_callback_marks_session_failed(self, plugin, mock_context_with_devices, mock_inbox):
        """AGV 失败回调应直接进入失败。"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_AGV_DELIVERY.value,
            "step_code": SmtClassifierStepCode.WAITING_AGV_DELIVERY.value,
            "agv_request_code": "AGV-REQ-003",
        }
        mock_inbox.payload_json = {
            "callback_type": "AGV_TASK_RESULT",
            "correlation_id": "corr-external-001",
            "command_id": "AGV-REQ-003",
            "result": "FAILED",
            "data": {"message": "Rack delivery timeout"},
        }

        result = await plugin.on_external_http(mock_context_with_devices, mock_inbox)

        assert result.transition == "command_failed"
        assert result.failure is not None
        assert result.failure.code == "AGV_TASK_FAILED"


class TestSmtClassifierPluginIntegration:
    """SmtClassifierPlugin 集成测试"""

    @pytest.mark.asyncio
    async def test_full_ok_flow(self):
        """测试完整的 OK 流程"""
        plugin = SmtClassifierPlugin()

        # 创建上下文
        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}

        # 模拟设备
        input_arm = MagicMock()
        input_arm.id = 10
        output_arm = MagicMock()
        output_arm.id = 20
        conveyor = MagicMock()
        conveyor.id = 30

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
                SmtClassifierDeviceRole.OUTPUT_ARM.value: [output_arm],
                SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device

        # 1. 扫码 OK
        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC001",
            "scan_result": "OK",
            "source_type": LocationType.INPUT_PLATFORM.value,
        }

        result = await plugin.on_device_event(context, inbox)
        assert result.transition == "scan_ok"

        # 2. 检测 OK
        context.session.context_json = result.context_patch
        inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "OK",
            "source_type": LocationType.PIPELINE_PLATFORM.value,
        }

        result = await plugin.on_device_event(context, inbox)
        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1

    @pytest.mark.asyncio
    async def test_full_scan_ng_flow(self):
        """测试完整的扫码 NG 流程"""
        plugin = SmtClassifierPlugin()

        context = MagicMock()
        context.logger = MagicMock()
        context.clock = datetime.now
        context.session = MagicMock()
        context.session.id = 123
        context.session.context_json = {}

        input_arm = MagicMock()
        input_arm.id = 10

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
            }
            devices = devices_map.get(role, [])
            return devices[index] if index < len(devices) else None

        context.get_device_by_role = mock_get_device

        # 扫码 NG
        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC002",
            "scan_result": "NG",
            "source_type": LocationType.INPUT_PLATFORM.value,
        }

        result = await plugin.on_device_event(context, inbox)
        assert result.transition == "scan_ng"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value

        # 命令完成
        context.session.context_json = result.context_patch
        inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(context, inbox)
        assert result.transition == "ng_handled"
        assert result.complete is True
