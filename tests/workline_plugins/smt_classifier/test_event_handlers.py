"""
SMT 粗分机事件处理器测试

测试事件处理器的核心功能：
- 扫码完成事件处理
- 急停事件处理
- 命令结果处理
- 命令生成函数
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.workline_plugins.smt_classifier.contract import SmtClassifierStepCode
from src.workline_plugins.smt_classifier.event_handlers import (
    CommandResult,
    CommandResultData,
    ErrorDetail,
    EventType,
    LocationInfo,
    LocationType,
    SmtClassifierEventHandler,
    TaskType,
    determine_scan_result,
    generate_move_forward_command,
    generate_pick_and_put_command,
    smt_classifier_event_handler,
    validate_barcode,
)
from src.workline_runtime import CommandIntent, FailureDomain, WaitIntent

# ==================== Fixtures ====================


@pytest.fixture
def event_handler() -> SmtClassifierEventHandler:
    """创建事件处理器实例"""
    return SmtClassifierEventHandler()


@pytest.fixture
def mock_context() -> MagicMock:
    """创建模拟插件上下文"""
    ctx = MagicMock()
    ctx.logger = logging.getLogger("test")
    ctx.session = MagicMock()
    ctx.session.context_json = {
        "barcode": "TEST001",
        "scan_result": "OK",
        "current_location": "STATION_PIPELINE1_INPUT1",
        "retry_count": 0,
    }
    ctx.session.id = 1

    # 模拟设备获取
    def get_device_by_role(role: str, index: int = 0) -> MagicMock | None:
        device = MagicMock()
        device.id = {
            "INPUT_ARM": 101,
            "OUTPUT_ARM": 102,
            "PIPELINE": 103,
            "CONVEYOR": 103,
        }.get(role)
        return device if device.id else None

    ctx.get_device_by_role = get_device_by_role
    ctx.clock = datetime.now

    return ctx


@pytest.fixture
def mock_inbox() -> MagicMock:
    """创建模拟 Inbox 实体"""
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = {}
    return inbox


# ==================== 命令生成函数测试 ====================


class TestCommandGeneration:
    """命令生成函数测试"""

    def test_generate_pick_and_put_command_basic(self) -> None:
        """测试基本抓取放置命令生成"""
        command = generate_pick_and_put_command(
            source="STATION_INPUT1",
            target="STATION_PIPELINE1_INPUT1",
            device_id=101,
        )

        assert isinstance(command, CommandIntent)
        assert command.target_device_id == 101
        assert command.action == TaskType.PICK_AND_PUT.value
        assert command.parameters["task_type"] == TaskType.PICK_AND_PUT.value
        assert command.parameters["params"]["source"]["location_id"] == "STATION_INPUT1"
        assert command.parameters["params"]["target"]["location_id"] == "STATION_PIPELINE1_INPUT1"

    def test_generate_pick_and_put_command_with_bin_info(self) -> None:
        """测试带料箱信息的抓取放置命令生成"""
        target_info = {
            "rack_id": "RACK_001",
            "bin_id": "BIN_001",
            "bin_type": "三格箱",
            "bin_cell_location": "1",
        }

        command = generate_pick_and_put_command(
            source="STATION_PIPELINE1_OUTPUT1",
            target="STATION_OUTPUT1",
            device_id=102,
            source_type=LocationType.PIPELINE_PLATFORM,
            target_type=LocationType.BIN,
            target_info=target_info,
        )

        assert command.parameters["params"]["target"]["rack_id"] == "RACK_001"
        assert command.parameters["params"]["target"]["bin_id"] == "BIN_001"
        assert command.parameters["params"]["target"]["bin_type"] == "三格箱"

    def test_generate_move_forward_command(self) -> None:
        """测试流水线前进命令生成"""
        command = generate_move_forward_command(
            source="STATION_PIPELINE1_INPUT1",
            target="STATION_PIPELINE1_OUTPUT1",
            device_id=103,
        )

        assert isinstance(command, CommandIntent)
        assert command.target_device_id == 103
        assert command.action == TaskType.MOVE_FORWARD.value
        assert command.parameters["task_type"] == TaskType.MOVE_FORWARD.value
        assert command.parameters["source"]["location_id"] == "STATION_PIPELINE1_INPUT1"
        assert command.parameters["target"]["location_id"] == "STATION_PIPELINE1_OUTPUT1"

    def test_generate_command_with_custom_timeout(self) -> None:
        """测试自定义超时命令生成"""
        command = generate_pick_and_put_command(
            source="STATION_INPUT1",
            target="STATION_NG_PLATFORM1",
            device_id=101,
            timeout=60000,
            priority=5,
        )

        assert command.parameters["timeout"] == 60000
        assert command.parameters["priority"] == 5


# ==================== 条码验证测试 ====================


class TestBarcodeValidation:
    """条码验证测试"""

    def test_validate_barcode_valid(self) -> None:
        """测试有效条码验证"""
        is_valid, reason = validate_barcode("PKG12345678")
        assert is_valid is True
        assert reason == "OK"

    def test_validate_barcode_empty(self) -> None:
        """测试空条码验证"""
        is_valid, reason = validate_barcode("")
        assert is_valid is False
        assert "空" in reason

    def test_validate_barcode_too_short(self) -> None:
        """测试过短条码验证"""
        is_valid, reason = validate_barcode("AB")
        assert is_valid is False
        assert "长度不足" in reason

    def test_validate_barcode_too_long(self) -> None:
        """测试过长条码验证"""
        is_valid, reason = validate_barcode("A" * 101)
        assert is_valid is False
        assert "长度超限" in reason

    def test_determine_scan_result_ok(self) -> None:
        """测试扫码结果判定 OK"""
        is_ok, reason = determine_scan_result(["PKG001", "PKG002"])
        assert is_ok is True
        assert reason == "OK"

    def test_determine_scan_result_ng_empty(self) -> None:
        """测试扫码结果判定 NG（空条码列表）"""
        is_ok, reason = determine_scan_result([])
        assert is_ok is False
        assert "无有效条码" in reason

    def test_determine_scan_result_ng_invalid(self) -> None:
        """测试扫码结果判定 NG（无效条码）"""
        is_ok, _reason = determine_scan_result(["AB"])  # 过短条码
        assert is_ok is False


# ==================== 数据模型测试 ====================


class TestDataModels:
    """数据模型测试"""

    def test_scan_event_data_get_barcodes(self) -> None:
        """测试扫码事件数据获取条码列表"""
        from src.workline_plugins.smt_classifier.event_handlers import ScanEventData

        scan_data = ScanEventData(
            location="STATION_INPUT1",
            barcode1="PKG001",
            barcode2="PKG002",
            barcode3=None,
            barcode4="PKG003",
        )

        barcodes = scan_data.get_barcodes()
        assert barcodes == ["PKG001", "PKG002", "PKG003"]

    def test_scan_event_data_get_primary_barcode(self) -> None:
        """测试获取主条码"""
        from src.workline_plugins.smt_classifier.event_handlers import ScanEventData

        scan_data = ScanEventData(
            location="STATION_INPUT1",
            barcode1="PKG001",
            barcode2="PKG002",
        )

        assert scan_data.get_primary_barcode() == "PKG001"

    def test_command_result_data(self) -> None:
        """测试命令结果数据模型"""
        result_data = CommandResultData(
            actual_qty=1,
            location="STATION_PIPELINE1_INPUT1",
            barcode1="PKG001",
            reel_diameter="15inch",
            reel_thickness="20",
            pick_and_put_result="PUT_FINISHED",
        )

        assert result_data.actual_qty == 1
        assert result_data.reel_diameter == "15inch"

    def test_location_info(self) -> None:
        """测试位置信息模型"""
        location = LocationInfo(
            location_id="STATION_OUTPUT1",
            location_type=LocationType.BIN,
            rack_id="RACK_001",
            bin_id="BIN_001",
            bin_type="三格箱",
        )

        assert location.location_id == "STATION_OUTPUT1"
        assert location.location_type == LocationType.BIN


# ==================== 扫码完成事件处理测试 ====================


class TestScanCompletedHandling:
    """扫码完成事件处理测试"""

    @pytest.mark.asyncio
    async def test_handle_scan_completed_ok(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试扫码 OK 事件处理"""
        mock_inbox.payload_json = {
            "event_type": EventType.SCAN_COMPLETED.value,
            "device_id": "ARM01",
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "PKG001",
                "barcode2": "PKG002",
            },
        }

        result = await event_handler.on_device_event(mock_context, mock_inbox)

        assert result.transition == "scan_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == TaskType.PICK_AND_PUT.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.INPUT_PICK_PLACE.value
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"

    @pytest.mark.asyncio
    async def test_handle_scan_completed_ng(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试扫码 NG 事件处理"""
        mock_inbox.payload_json = {
            "event_type": EventType.SCAN_COMPLETED.value,
            "device_id": "ARM01",
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "",  # 空条码导致 NG
            },
        }

        result = await event_handler.on_device_event(mock_context, mock_inbox)

        assert result.transition == "scan_ng"
        assert result.context_patch["step_code"] == SmtClassifierStepCode.NG_PICK_PLACE.value
        assert result.failure is not None
        assert result.failure.domain == FailureDomain.DATA.value

    @pytest.mark.asyncio
    async def test_handle_scan_completed_no_device(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试设备未找到的情况"""
        mock_context.get_device_by_role = lambda role, index=0: None
        mock_inbox.payload_json = {
            "event_type": EventType.SCAN_COMPLETED.value,
            "device_id": "ARM01",
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "PKG001",
            },
        }

        result = await event_handler.on_device_event(mock_context, mock_inbox)

        assert result.failure is not None
        assert result.failure.code == "DEVICE_NOT_FOUND"


# ==================== 急停事件处理测试 ====================


class TestEstopHandling:
    """急停事件处理测试"""

    @pytest.mark.asyncio
    async def test_handle_estop_pressed(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试急停事件处理"""
        mock_inbox.payload_json = {
            "event_type": EventType.ESTOP_PRESSED.value,
            "device_id": "ARM01",
            "timestamp": 1702627300000,
        }

        result = await event_handler.on_device_event(mock_context, mock_inbox)

        assert result.transition == "estop"
        assert "estop_device" in result.context_patch
        assert result.context_patch["estop_device"] == "ARM01"


# ==================== 命令结果处理测试 ====================


class TestCommandResultHandling:
    """命令结果处理测试"""

    @pytest.mark.asyncio
    async def test_handle_command_success_pipeline_input(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试流水线进料位置命令成功后的处理"""
        mock_context.session.context_json = {
            "barcode": "PKG001",
            "scan_result": "OK",
            "current_location": "STATION_PIPELINE1_INPUT1",
            "step_code": SmtClassifierStepCode.INPUT_PICK_PLACE.value,
            "retry_count": 0,
        }
        mock_inbox.payload_json = {
            "result": CommandResult.SUCCESS.value,
            "device_id": "ARM01",
            "data": {
                "actual_qty": 1,
                "location": "STATION_PIPELINE1_INPUT1",
                "reel_diameter": "15inch",
                "reel_thickness": "20",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        assert result.transition == "move_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == TaskType.MOVE_FORWARD.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.PIPELINE_MOVE_FORWARD.value

    @pytest.mark.asyncio
    async def test_handle_command_success_pipeline_output(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试流水线出料位置命令成功后的处理"""
        mock_context.session.context_json = {
            "barcode": "PKG001",
            "scan_result": "OK",
            "current_location": "STATION_PIPELINE1_OUTPUT1",
            "diameter": "15inch",
            "thickness": "20",
            "retry_count": 0,
        }
        mock_inbox.payload_json = {
            "result": CommandResult.SUCCESS.value,
            "device_id": "PIPELINE01",
            "data": {
                "actual_qty": 1,
                "location": "STATION_PIPELINE1_OUTPUT1",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        assert result.transition == "put_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == TaskType.PICK_AND_PUT.value
        assert result.context_patch["step_code"] == SmtClassifierStepCode.OUTPUT_PICK_PLACE.value

    @pytest.mark.asyncio
    async def test_handle_command_success_output(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试出料完成后的处理"""
        mock_context.session.context_json = {
            "barcode": "PKG001",
            "scan_result": "OK",
            "current_location": "STATION_OUTPUT1",
            "retry_count": 0,
        }
        mock_inbox.payload_json = {
            "result": CommandResult.SUCCESS.value,
            "device_id": "ARM02",
            "data": {
                "actual_qty": 1,
                "location": "STATION_OUTPUT1",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        assert result.transition == "complete"
        assert result.complete is True
        assert result.context_patch["step_code"] == SmtClassifierStepCode.COMPLETED.value

    @pytest.mark.asyncio
    async def test_handle_command_success_step_code_precedence(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """step_code 应优先于位置字符串启发式。"""
        mock_context.session.context_json = {
            "barcode": "PKG001",
            "scan_result": "OK",
            "current_location": "STATION_OUTPUT1",
            "step_code": SmtClassifierStepCode.INPUT_PICK_PLACE.value,
            "retry_count": 0,
        }
        mock_inbox.payload_json = {
            "result": CommandResult.SUCCESS.value,
            "device_id": "ARM01",
            "data": {
                "actual_qty": 1,
                "location": "STATION_OUTPUT1",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        assert result.transition == "move_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == TaskType.MOVE_FORWARD.value

    @pytest.mark.asyncio
    async def test_handle_command_failed(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试命令失败处理（不可重试错误）"""
        mock_inbox.payload_json = {
            "result": CommandResult.FAILED.value,
            "device_id": "ARM01",
            "error_detail": {
                "error_code": "9999",  # 不可重试错误
                "error_message": "未知错误",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        assert result.failure is not None
        assert result.failure.code == "9999"

    @pytest.mark.asyncio
    async def test_handle_command_failed_retryable(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试可重试错误处理"""
        mock_inbox.payload_json = {
            "result": CommandResult.FAILED.value,
            "device_id": "ARM01",
            "error_detail": {
                "error_code": "2002",  # 可重试错误
                "error_message": "搬运失败",
            },
        }

        result = await event_handler.on_command_result(mock_context, mock_inbox)

        # 错误码 2002 是可重试的，所以会尝试重试
        assert result.transition == "retry"
        assert result.context_patch["retry_count"] == 1


# ==================== 超时处理测试 ====================


class TestTimeoutHandling:
    """超时处理测试"""

    @pytest.mark.asyncio
    async def test_handle_timeout(
        self,
        event_handler: SmtClassifierEventHandler,
        mock_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试超时处理"""
        result = await event_handler.on_timeout(mock_context, mock_inbox)

        assert result.failure is not None
        assert result.failure.domain == FailureDomain.TIMEOUT.value
        assert result.failure.code == "DEVICE_TIMEOUT"


# ==================== 单例测试 ====================


class TestSingleton:
    """单例测试"""

    def test_smt_classifier_event_handler_singleton(self) -> None:
        """测试事件处理器单例"""
        from src.workline_plugins.smt_classifier.event_handlers import (
            smt_classifier_event_handler as handler1,
        )
        from src.workline_plugins.smt_classifier.event_handlers import (
            smt_classifier_event_handler as handler2,
        )

        assert handler1 is handler2


# ==================== 错误归因测试 ====================


class TestFailureAttribution:
    """错误归因测试"""

    def test_determine_failure_domain_hardware(self) -> None:
        """测试硬件错误归因"""
        event_handler = SmtClassifierEventHandler()

        assert event_handler._determine_failure_domain("1001") == FailureDomain.HARDWARE.value
        assert event_handler._determine_failure_domain("1002") == FailureDomain.HARDWARE.value
        assert event_handler._determine_failure_domain("2001") == FailureDomain.HARDWARE.value
        assert event_handler._determine_failure_domain("2002") == FailureDomain.HARDWARE.value

    def test_determine_failure_domain_algorithm(self) -> None:
        """测试算法错误归因"""
        event_handler = SmtClassifierEventHandler()

        assert event_handler._determine_failure_domain("2003") == FailureDomain.ALGORITHM.value

    def test_determine_failure_domain_software(self) -> None:
        """测试软件错误归因"""
        event_handler = SmtClassifierEventHandler()

        assert event_handler._determine_failure_domain("9999") == FailureDomain.SOFTWARE.value
        assert event_handler._determine_failure_domain("5000") == FailureDomain.SOFTWARE.value

    def test_is_retryable_error(self) -> None:
        """测试可重试错误判断"""
        event_handler = SmtClassifierEventHandler()

        # 可重试错误
        assert event_handler._is_retryable_error("503") is True
        assert event_handler._is_retryable_error("1001") is True
        assert event_handler._is_retryable_error("2002") is True

        # 不可重试错误
        assert event_handler._is_retryable_error("9999") is False
        assert event_handler._is_retryable_error("2003") is False


# ==================== 枚举测试 ====================


class TestEnums:
    """枚举测试"""

    def test_event_type_values(self) -> None:
        """测试事件类型枚举值"""
        assert EventType.SCAN_COMPLETED.value == "SCAN_COMPLETED"
        assert EventType.ESTOP_PRESSED.value == "ESTOP_PRESSED"

    def test_task_type_values(self) -> None:
        """测试任务类型枚举值"""
        assert TaskType.PICK_AND_PUT.value == "PICK_AND_PUT"
        assert TaskType.MOVE_FORWARD.value == "MOVE_FORWARD"

    def test_location_type_values(self) -> None:
        """测试位置类型枚举值"""
        assert LocationType.INPUT_PLATFORM.value == "INPUT_PLATFORM"
        assert LocationType.NG_PLATFORM.value == "NG_PLATFORM"
        assert LocationType.PIPELINE_PLATFORM.value == "PIPELINE_PLATFORM"
        assert LocationType.BIN.value == "BIN"

    def test_command_result_values(self) -> None:
        """测试命令结果枚举值"""
        assert CommandResult.SUCCESS.value == "SUCCESS"
        assert CommandResult.FAILED.value == "FAILED"
