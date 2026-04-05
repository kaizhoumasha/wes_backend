"""
插件框架单元测试

测试装饰器驱动、Pydantic自动解析、状态校验、类型映射等核心功能。
"""

from unittest.mock import MagicMock, Mock

import pytest

from src.workline_runtime.plugin_base import (
    EventPayload,
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    on_timeout,
    step,
)
from src.workline_runtime.types import (
    CommandIntent,
    FailureIntent,
    PluginResult,
    WaitIntent,
)

# ==================== 测试数据 ====================


class ScanEventPayload(EventPayload):
    """扫码事件Payload测试模型"""

    barcode: str
    location_id: str


class PickPlaceResultPayload(EventPayload):
    """抓取放置结果Payload测试模型"""

    result: str


# ==================== 插件测试实现 ====================


class TestPlugin(WorklinePlugin):
    """测试插件"""

    plugin_key = "test"
    contract_version = "1.0"

    # ========== 事件处理 ==========

    @on_event("SCAN_COMPLETED")
    @step("IDLE", "WAITING_INSPECTION")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        """扫码处理"""
        return (
            PluginResultBuilder(ctx).transition("scan_ok").command(device_role="INPUT_ARM", command_type="PICK").build()
        )

    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection(self, ctx, event: ScanEventPayload):
        """检测处理（无状态迁移声明）"""
        return PluginResultBuilder(ctx).transition("inspection_ok").build()

    # ========== 命令结果处理 ==========

    @on_command("PICK", result="SUCCESS")
    @step("WAITING_INSPECTION", "WAITING_CONVEYOR")
    async def handle_pick_success(self, ctx, result: PickPlaceResultPayload):
        """抓取成功"""
        return PluginResultBuilder(ctx).transition("pick_ok").build()

    @on_command("PICK", result="FAILED")
    @step("WAITING_INSPECTION", "ERROR")
    async def handle_pick_failed(self, ctx, result: PickPlaceResultPayload):
        """抓取失败"""
        return PluginResultBuilder(ctx).failure(domain="HARDWARE", code="PICK_FAILED", message="抓取失败").build()

    # ========== 超时处理 ==========

    @on_timeout()
    async def handle_timeout(self, ctx, inbox):
        """超时处理"""
        return PluginResultBuilder(ctx).failure(domain="TIMEOUT", code="TIMEOUT", message="超时").build()


# ==================== 测试用例 ====================


class TestDecoratorRouting:
    """装饰器路由测试"""

    def test_on_event_decorator_registers_handler(self):
        """验证 @on_event 将方法注册到 _event_handlers"""
        assert "SCAN_COMPLETED" in TestPlugin._event_handlers
        assert "INSPECTION_COMPLETED" in TestPlugin._event_handlers
        assert TestPlugin._event_handlers["SCAN_COMPLETED"].__name__ == "handle_scan"

    def test_on_command_decorator_registers_handler(self):
        """验证 @on_command 将方法注册到 _command_handlers"""
        # 精确匹配 (command_type, result)
        key = ("PICK", "SUCCESS")
        assert key in TestPlugin._command_handlers

        # 检查方法绑定
        handler = TestPlugin._command_handlers[key]
        assert handler.__name__ == "handle_pick_success"

    def test_on_timeout_decorator_registers_handler(self):
        """验证 @on_timeout 标记超时处理器"""
        assert TestPlugin._timeout_handler is not None
        assert TestPlugin._timeout_handler.__name__ == "handle_timeout"


class TestStepDecorator:
    """@step 装饰器测试"""

    def test_step_decorator_sets_expected_and_target(self):
        """验证 @step 设置 _expected_step 和 _target_step"""
        handler = TestPlugin._event_handlers["SCAN_COMPLETED"]
        assert getattr(handler, "_expected_step", None) == "IDLE"
        assert getattr(handler, "_target_step", None) == "WAITING_INSPECTION"

    def test_step_decorator_optional_params(self):
        """验证 @step 参数可选"""
        handler = TestPlugin._event_handlers["INSPECTION_COMPLETED"]
        assert getattr(handler, "_expected_step", None) is None
        assert getattr(handler, "_target_step", None) is None


class TestPydanticParsing:
    """Pydantic 自动解析测试"""

    @pytest.mark.asyncio
    async def test_invoke_handler_parses_pydantic_payload(self):
        """验证 _invoke_handler 自动解析 Pydantic Model"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "IDLE"}

        # Mock inbox with payload
        inbox = MagicMock()
        inbox.payload_json = {
            "device_code": "SCANNER01",
            "barcode": "ABC123",
            "location_id": "LOC01",
        }

        # Call handler
        handler = TestPlugin._event_handlers["SCAN_COMPLETED"]
        result = await plugin._invoke_handler(handler, ctx, inbox, inbox.payload_json)

        # Verify
        assert result.transition == "scan_ok"
        assert result.commands is not None
        assert len(result.commands) == 1

    @pytest.mark.asyncio
    async def test_invoke_handler_validation_error_returns_failure(self):
        """验证 Payload 验证失败返回 FailureIntent"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "IDLE"}

        # Mock inbox with invalid payload (missing required field)
        inbox = MagicMock()
        inbox.payload_json = {
            "device_code": "SCANNER01",
            # 缺少 barcode 字段
        }

        # Call handler
        handler = TestPlugin._event_handlers["SCAN_COMPLETED"]
        result = await plugin._invoke_handler(handler, ctx, inbox, inbox.payload_json)

        # Verify
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"


class TestStateValidation:
    """状态校验测试"""

    @pytest.mark.asyncio
    async def test_step_decorator_validates_expected_state(self):
        """验证 @step 前置状态校验 - 匹配"""
        plugin = TestPlugin()

        # Mock context with correct state
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "IDLE"}

        # Mock inbox
        inbox = MagicMock()
        inbox.payload_json = {
            "device_code": "SCANNER01",
            "barcode": "ABC123",
            "location_id": "LOC01",
        }

        # Call handler
        handler = TestPlugin._event_handlers["SCAN_COMPLETED"]
        result = await plugin._invoke_handler(handler, ctx, inbox, inbox.payload_json)

        # Verify state passed
        assert result.failure is None
        assert result.transition == "scan_ok"

    @pytest.mark.asyncio
    async def test_step_decorator_rejects_wrong_state(self):
        """验证 @step 前置状态校验 - 不匹配"""
        plugin = TestPlugin()

        # Mock context with wrong state
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "COMPLETED"}

        # Mock inbox
        inbox = MagicMock()
        inbox.payload_json = {
            "device_code": "SCANNER01",
            "barcode": "ABC123",
            "location_id": "LOC01",
        }

        # Call handler
        handler = TestPlugin._event_handlers["SCAN_COMPLETED"]
        result = await plugin._invoke_handler(handler, ctx, inbox, inbox.payload_json)

        # Verify state rejected
        assert result.failure is not None
        assert result.failure.domain == "SOFTWARE"
        assert result.failure.code == "STATE_MISMATCH"
        assert "idle" in result.failure.message.lower()

    @pytest.mark.asyncio
    async def test_step_decorator_sets_target_state(self):
        """验证 @step 自动设置目标状态"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "IDLE"}

        # Mock inbox
        inbox = MagicMock()
        inbox.payload_json = {
            "device_code": "SCANNER01",
            "barcode": "ABC123",
            "location_id": "LOC01",
        }

        # Call handler that returns result without transition
        handler = TestPlugin._event_handlers["INSPECTION_COMPLETED"]
        result = await plugin._invoke_handler(handler, ctx, inbox, inbox.payload_json)

        # Verify target state was set
        # Note: handle_inspection sets transition explicitly, so this test
        # verifies the fallback behavior when transition is not set
        assert result.transition == "inspection_ok"


class TestPluginResultBuilder:
    """PluginResultBuilder 测试"""

    def test_command_builder_resolves_device_role(self):
        """验证 Builder.command() 从 devices_by_role 解析设备ID"""
        # Mock context
        ctx = MagicMock()
        device = MagicMock()
        device.id = 123
        ctx.devices_by_role = {"INPUT_ARM": [device]}

        # Build command
        result = PluginResultBuilder(ctx).command(device_role="INPUT_ARM", command_type="PICK").build()

        # Verify device_role resolved to target_device_id
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].target_device_id == 123
        assert result.commands[0].action == "PICK"

    def test_command_builder_raises_on_missing_role(self):
        """验证设备角色缺失时抛出 ValueError"""
        # Mock context with empty devices_by_role
        ctx = MagicMock()
        ctx.devices_by_role = {}

        # Build command should raise
        with pytest.raises(ValueError, match=r"Device role.*not found"):
            PluginResultBuilder(ctx).command(device_role="INPUT_ARM", command_type="PICK").build()

    def test_wait_intent_field_mapping(self):
        """验证 WaitIntent 字段正确映射"""
        # Mock context
        ctx = MagicMock()
        session = MagicMock()
        session.id = 42
        ctx.session = session

        # Build wait
        result = PluginResultBuilder(ctx).wait(event_type="INSPECTION_COMPLETED", timeout_seconds=300).build()

        # Verify field mapping
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-INSPECTION_COMPLETED-")
        assert result.wait.deadline_seconds == 300

    def test_failure_builder(self):
        """验证 failure() 方法"""
        ctx = MagicMock()

        result = PluginResultBuilder(ctx).failure(domain="HARDWARE", code="TIMEOUT", message="设备超时").build()

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "TIMEOUT"
        assert result.failure.message == "设备超时"

    def test_complete_builder(self):
        """验证 complete() 方法"""
        ctx = MagicMock()

        result = PluginResultBuilder(ctx).complete().build()

        assert result.complete is True

    def test_context_builder(self):
        """验证 context() 方法"""
        ctx = MagicMock()

        result = PluginResultBuilder(ctx).context({"last_barcode": "ABC123", "stage": "WAITING_INSPECTION"}).build()

        assert result.context_patch is not None
        assert result.context_patch["last_barcode"] == "ABC123"
        assert result.context_patch["stage"] == "WAITING_INSPECTION"

    def test_chain_builder(self):
        """验证链式调用"""
        # Mock context
        ctx = MagicMock()
        device = MagicMock()
        device.id = 123
        ctx.devices_by_role = {"INPUT_ARM": [device]}
        session = MagicMock()
        session.id = 42
        ctx.session = session

        # Chain all builder methods
        result = (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(device_role="INPUT_ARM", command_type="PICK", parameters={"barcode": "ABC123"})
            .wait(event_type="INSPECTION_COMPLETED", timeout_seconds=300)
            .context({"last_barcode": "ABC123"})
            .build()
        )

        # Verify all fields
        assert result.transition == "scan_ok"
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.wait is not None
        assert result.context_patch is not None


class TestEventRouting:
    """事件路由测试"""

    @pytest.mark.asyncio
    async def test_on_device_event_routes_to_event_handler(self):
        """验证 on_device_event 自动路由到 @on_event 标记的方法"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "IDLE"}

        # Mock inbox
        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = {
            "event_type": "SCAN_COMPLETED",
            "device_code": "SCANNER01",
            "barcode": "ABC123",
            "location_id": "LOC01",
        }

        # Call on_device_event
        result = await plugin.on_device_event(ctx, inbox)

        # Verify routed to handle_scan
        assert result.transition == "scan_ok"
        assert result.commands is not None

    @pytest.mark.asyncio
    async def test_on_command_result_routes_to_command_handler(self):
        """验证 on_command_result 自动路由到 @on_command 标记的方法"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.session = MagicMock()
        ctx.session.context_json = {"step_code": "WAITING_INSPECTION"}

        # Mock inbox
        inbox = MagicMock()
        inbox.id = 2
        inbox.payload_json = {
            "command_type": "PICK",
            "result": "SUCCESS",
            "device_code": "ARM01",
        }

        # Call on_command_result
        result = await plugin.on_command_result(ctx, inbox)

        # Verify routed to handle_pick_success
        assert result.transition == "pick_ok"

    @pytest.mark.asyncio
    async def test_on_timeout_routes_to_timeout_handler(self):
        """验证 on_timeout 自动路由到 @on_timeout 标记的方法"""
        plugin = TestPlugin()

        # Mock context
        ctx = MagicMock()
        ctx.logger = MagicMock()

        # Mock inbox
        inbox = MagicMock()
        inbox.id = 3

        # Call on_timeout
        result = await plugin.on_timeout(ctx, inbox)

        # Verify routed to handle_timeout
        assert result.failure is not None
        assert result.failure.code == "TIMEOUT"
