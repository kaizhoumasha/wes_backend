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
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier.plugin import (
    SmtClassifierCommandType,
    SmtClassifierDeviceRole,
    SmtClassifierEventType,
    SmtClassifierLocationId,
    SmtClassifierPlugin,
    SmtClassifierStage,
    build_location_id,
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

    def test_location_id_values(self):
        """测试位置 ID 枚举值"""
        assert SmtClassifierLocationId.INPUT.value == "INPUT"
        assert SmtClassifierLocationId.NG.value == "NG"
        assert SmtClassifierLocationId.PIPELINE_INPUT.value == "PIPELINE_INPUT"
        assert SmtClassifierLocationId.PIPELINE_OUTPUT.value == "PIPELINE_OUTPUT"
        assert SmtClassifierLocationId.OUTPUT.value == "OUTPUT"

        assert build_location_id("LEFT_STATION_INPUT", SmtClassifierLocationId.INPUT) == "LEFT_STATION_INPUT"
        assert (
            build_location_id("RIGHT_STATION_PIPELINE_OUTPUT", SmtClassifierLocationId.OUTPUT) == "RIGHT_STATION_OUTPUT"
        )

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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.INPUT),
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch["scan_result"] == "OK"
        assert result.context_patch["last_barcode"] == "BC001"
        assert result.context_patch["stage"] == SmtClassifierStage.WAITING_INSPECTION.value
        assert result.context_patch["location_id"] == build_location_id("LEFT", SmtClassifierLocationId.INPUT)

    @pytest.mark.asyncio
    async def test_scan_ng_flow(self, plugin, mock_context_with_devices, mock_inbox):
        """测试扫码 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC002",
            "scan_result": "NG",
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.INPUT),
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ng"
        assert result.context_patch["scan_result"] == "NG"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"

    @pytest.mark.asyncio
    async def test_scan_ng_right_station(self, plugin, mock_context_with_devices, mock_inbox):
        """测试右侧作业线扫码 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "BC003",
            "scan_result": "NG",
            "location_id": build_location_id("RIGHT", SmtClassifierLocationId.INPUT),
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "scan_ng"
        assert result.commands[0].parameters["to_location"] == build_location_id("RIGHT", SmtClassifierLocationId.NG)


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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_INPUT),
        }

        result = await plugin.on_device_event(mock_context_with_devices, mock_inbox)

        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.MOVE_FORWARD.value
        assert result.wait is not None

    @pytest.mark.asyncio
    async def test_inspection_ng_flow(self, plugin, mock_context_with_devices, mock_inbox):
        """测试检测 NG 流程"""
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "NG",
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_INPUT),
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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_OUTPUT),
        }
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
            "pick_place_reason": "SCAN_NG",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "ng_handled"
        assert result.complete is True
        assert result.context_patch["stage"] == SmtClassifierStage.COMPLETED.value

    @pytest.mark.asyncio
    async def test_conveyor_completed(self, plugin, mock_context_with_devices, mock_inbox):
        """测试流水线传输完成"""
        mock_context_with_devices.session.context_json = {
            "stage": SmtClassifierStage.WAITING_CONVEYOR.value,
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_INPUT),
            "pending_location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_OUTPUT),
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(mock_context_with_devices, mock_inbox)

        assert result.transition == "conveyor_complete"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value

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
            "location_id": build_location_id("RIGHT", SmtClassifierLocationId.PIPELINE_INPUT),
            "pending_location_id": build_location_id("RIGHT", SmtClassifierLocationId.PIPELINE_OUTPUT),
        }

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

        result = await plugin.on_command_result(context, mock_inbox)

        assert result.transition == "conveyor_complete"
        assert result.commands[0].target_device_id == 33
        assert result.commands[0].parameters["from_location"] == build_location_id(
            "RIGHT", SmtClassifierLocationId.PIPELINE_OUTPUT
        )
        assert result.commands[0].parameters["to_location"] == build_location_id(
            "RIGHT", SmtClassifierLocationId.OUTPUT
        )


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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_INPUT),
        }
        context.devices_by_role = {}

        conveyor = MagicMock()
        conveyor.id = 30

        input_arm = MagicMock()
        input_arm.id = 10

        def mock_get_device(role, index=0):
            devices_map = {
                SmtClassifierDeviceRole.CONVEYOR.value: [conveyor],
                SmtClassifierDeviceRole.INPUT_ARM.value: [input_arm],
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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.INPUT),
        }

        result = await plugin.on_device_event(context, inbox)
        assert result.transition == "scan_ok"

        # 2. 检测 OK
        context.session.context_json = result.context_patch
        inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "OK",
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.PIPELINE_INPUT),
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
            "location_id": build_location_id("LEFT", SmtClassifierLocationId.INPUT),
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
