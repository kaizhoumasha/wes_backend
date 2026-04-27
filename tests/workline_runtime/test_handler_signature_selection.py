"""Handler 签名选择测试。

验证 `_resolve_handler_model_arg()` 根据 handler 签名自动选择注入参数的逻辑：
- `async def handler(self, ctx, inbox)` → 注入原始 inbox
- `async def handler(self, ctx, result: NormalizedCommandResult)` → 注入 normalized_input
"""

from unittest.mock import MagicMock

from src.workline_runtime.plugin_base import _resolve_handler_model_arg
from src.workline_runtime.plugin_sdk import NormalizedCommandResult


class MockInbox:
    """模拟 Inbox 实体。"""

    def __init__(
        self,
        id: int = 1,
        kind: str = "COMMAND_RESULT",
        payload_json: dict | None = None,
    ):
        self.id = id
        self.kind = kind
        self.payload_json = payload_json or {}


class MockContext:
    """模拟 PluginContext。"""

    def __init__(
        self,
        trace_id: str = "test-trace",
        normalized_input: NormalizedCommandResult | None = None,
    ):
        self.trace_id = trace_id
        self.normalized_input = normalized_input


class TestHandlerSignatureSelection:
    """测试 Handler 签名选择规则。"""

    def test_normalized_input_present_returns_directly(self):
        """ctx.normalized_input 存在且类型匹配时，直接返回。"""
        normalized = NormalizedCommandResult(
            command_code="CMD-001",
            source_result="SUCCESS",
            normalized_result="SUCCESS",
        )
        ctx = MockContext(normalized_input=normalized)
        inbox = MockInbox()
        payload = {"command_code": "CMD-001", "result": "SUCCESS"}

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert result == normalized
        assert result.command_code == "CMD-001"
        assert result.normalized_result == "SUCCESS"

    def test_normalized_input_absent_calls_normalize_inbox(self):
        """ctx.normalized_input 不存在时，调用 normalize_inbox_input()。"""
        ctx = MockContext(normalized_input=None)
        inbox = MockInbox(
            payload_json={
                "command_code": "CMD-002",
                "result": "FAILED",
                "error_detail": {"error_code": "DEVICE_ERROR"},
            }
        )
        payload = inbox.payload_json

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert isinstance(result, NormalizedCommandResult)
        assert result.command_code == "CMD-002"
        # classify_result 将 FAILED 转换为 TERMINAL_FAILURE
        assert result.normalized_result == "TERMINAL_FAILURE"
        # error_detail 从嵌套字段提取
        assert result.error_detail.get("error_code") == "DEVICE_ERROR"

    def test_normalize_fails_falls_back_to_payload_validation(self):
        """normalize_inbox_input 失败时，回退到原始 payload 解析。"""
        ctx = MockContext(normalized_input=None)
        # payload 包含必需字段
        inbox = MockInbox(
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-003",
                "result": "SUCCESS",
            },
        )
        payload = inbox.payload_json

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert isinstance(result, NormalizedCommandResult)
        assert result.command_code == "CMD-003"
        assert result.normalized_result == "SUCCESS"

    def test_type_mismatch_falls_back_to_validation(self):
        """ctx.normalized_input 类型不匹配时，回退到 payload 解析。"""

        # 使用 MagicMock 避免类型验证错误
        ctx = MagicMock()
        ctx.normalized_input = MagicMock()  # 不是 NormalizedCommandResult 类型
        ctx.trace_id = "test-trace"

        inbox = MockInbox(payload_json={"command_code": "CMD-004", "result": "SUCCESS"})
        payload = inbox.payload_json

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert isinstance(result, NormalizedCommandResult)
        assert result.command_code == "CMD-004"

    def test_error_detail_from_nested_field(self):
        """error_detail 从嵌套 error_detail 字段提取。"""
        ctx = MockContext(normalized_input=None)
        inbox = MockInbox(
            payload_json={
                "command_code": "CMD-005",
                "result": "FAILED",
                "error_detail": {
                    "code": "DEVICE_ERROR",  # vendor alias in nested
                    "message": "Device failure",
                },
            }
        )
        payload = inbox.payload_json

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert isinstance(result, NormalizedCommandResult)
        # error_detail 包含标准化后的 error_code
        assert result.error_detail.get("error_code") == "DEVICE_ERROR"

    def test_success_result_normalized(self):
        """SUCCESS 结果保持为 SUCCESS。"""
        ctx = MockContext(normalized_input=None)
        inbox = MockInbox(
            payload_json={
                "command_code": "CMD-006",
                "result": "SUCCESS",
            }
        )
        payload = inbox.payload_json

        result = _resolve_handler_model_arg(ctx, inbox, payload, NormalizedCommandResult)

        assert result.normalized_result == "SUCCESS"


class TestHandlerSignatureIntegration:
    """集成测试：验证 handler 调用时参数注入。"""

    def test_command_handlers_tuple_key(self):
        """验证 _command_handlers 使用 tuple key (command_type, result)。"""

        from src.workline_runtime.plugin_base import WorklinePlugin, on_command

        class TestPlugin(WorklinePlugin):
            @on_command("TEST_CMD", result="SUCCESS")
            async def handle_test(self, ctx, inbox):
                return inbox

        plugin = TestPlugin()

        # 获取注册的 handler，key 是 tuple (command_type, result)
        handlers = plugin._command_handlers
        assert ("TEST_CMD", "SUCCESS") in handlers

    def test_multiple_result_handlers_registered(self):
        """同一 command_type 不同 result 注册多个 handler。"""

        from src.workline_runtime.plugin_base import WorklinePlugin, on_command

        class TestPlugin(WorklinePlugin):
            @on_command("TEST_CMD", result="SUCCESS")
            async def handle_success(self, ctx, result: NormalizedCommandResult):
                return result

            @on_command("TEST_CMD", result="FAILED")
            async def handle_failed(self, ctx, result: NormalizedCommandResult):
                return result

        plugin = TestPlugin()

        handlers = plugin._command_handlers
        assert ("TEST_CMD", "SUCCESS") in handlers
        assert ("TEST_CMD", "FAILED") in handlers
