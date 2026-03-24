"""
NullPlugin 单元测试

测试 Phase 2 默认插件的空实现：
- on_device_event: 设备事件处理
- on_command_result: 命令结果处理
- on_timeout: 超时处理
- on_manual_operation: 人工操作处理

设计参考:
- 设计文档: phase2-orchestrator design doc
"""

from unittest.mock import MagicMock

import pytest

from src.workline_runtime.null_plugin import NullPlugin
from src.workline_runtime.types import PluginResult


class TestNullPlugin:
    """NullPlugin 测试"""

    @pytest.fixture
    def plugin(self):
        """创建 NullPlugin 实例"""
        return NullPlugin()

    @pytest.fixture
    def mock_context(self):
        """创建模拟的插件上下文"""
        context = MagicMock()
        context.logger = MagicMock()
        return context

    @pytest.fixture
    def mock_inbox(self):
        """创建模拟的 Inbox"""
        inbox = MagicMock()
        inbox.id = 123
        return inbox

    @pytest.mark.asyncio
    async def test_plugin_key(self, plugin):
        """测试插件标识"""
        assert plugin.plugin_key == "null"

    @pytest.mark.asyncio
    async def test_on_device_event_returns_empty_result(self, plugin, mock_context, mock_inbox):
        """测试设备事件处理返回空结果"""
        result = await plugin.on_device_event(mock_context, mock_inbox)
        assert isinstance(result, PluginResult)
        assert result.transition is None
        assert result.context_patch == {}

    @pytest.mark.asyncio
    async def test_on_device_event_logs_event(self, plugin, mock_context, mock_inbox):
        """测试设备事件处理记录日志"""
        await plugin.on_device_event(mock_context, mock_inbox)
        mock_context.logger.info.assert_called_once()
        # 验证日志包含 inbox_id
        call_args = mock_context.logger.info.call_args[0][0]
        assert "123" in call_args

    @pytest.mark.asyncio
    async def test_on_command_result_returns_empty_result(self, plugin, mock_context, mock_inbox):
        """测试命令结果处理返回空结果"""
        result = await plugin.on_command_result(mock_context, mock_inbox)
        assert isinstance(result, PluginResult)
        assert result.transition is None

    @pytest.mark.asyncio
    async def test_on_command_result_logs_event(self, plugin, mock_context, mock_inbox):
        """测试命令结果处理记录日志"""
        await plugin.on_command_result(mock_context, mock_inbox)
        mock_context.logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_timeout_returns_empty_result(self, plugin, mock_context, mock_inbox):
        """测试超时处理返回空结果"""
        result = await plugin.on_timeout(mock_context, mock_inbox)
        assert isinstance(result, PluginResult)
        assert result.transition is None

    @pytest.mark.asyncio
    async def test_on_timeout_logs_warning(self, plugin, mock_context, mock_inbox):
        """测试超时处理记录警告日志"""
        await plugin.on_timeout(mock_context, mock_inbox)
        mock_context.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_external_http_returns_empty_result(self, plugin, mock_context, mock_inbox):
        """测试外部 HTTP 回调处理返回空结果"""
        result = await plugin.on_external_http(mock_context, mock_inbox)
        assert isinstance(result, PluginResult)
        assert result.transition is None

    @pytest.mark.asyncio
    async def test_on_external_http_logs_event(self, plugin, mock_context, mock_inbox):
        """测试外部 HTTP 回调处理记录日志"""
        await plugin.on_external_http(mock_context, mock_inbox)
        mock_context.logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_manual_operation_returns_empty_result(self, plugin, mock_context, mock_inbox):
        """测试人工操作处理返回空结果"""
        result = await plugin.on_manual_operation(mock_context, mock_inbox)
        assert isinstance(result, PluginResult)
        assert result.transition is None

    @pytest.mark.asyncio
    async def test_on_manual_operation_logs_event(self, plugin, mock_context, mock_inbox):
        """测试人工操作处理记录日志"""
        await plugin.on_manual_operation(mock_context, mock_inbox)
        mock_context.logger.info.assert_called_once()


class TestNullPluginIntegration:
    """NullPlugin 集成测试"""

    @pytest.mark.asyncio
    async def test_all_methods_return_empty_result(self):
        """测试所有方法返回空结果（不修改 Session）"""
        plugin = NullPlugin()
        context = MagicMock()
        context.logger = MagicMock()
        inbox = MagicMock(id=1)

        # 所有方法应返回空的 PluginResult
        results = [
            await plugin.on_device_event(context, inbox),
            await plugin.on_command_result(context, inbox),
            await plugin.on_timeout(context, inbox),
            await plugin.on_external_http(context, inbox),
            await plugin.on_manual_operation(context, inbox),
        ]

        for result in results:
            assert result.transition is None
            assert result.context_patch == {}
            assert result.decisions == []
            assert result.commands == []
            assert result.wait is None
            assert result.failure is None
            assert result.complete is False

    @pytest.mark.asyncio
    async def test_all_methods_log_correctly(self):
        """测试所有方法正确记录日志"""
        plugin = NullPlugin()
        inbox = MagicMock(id=42)

        # 测试 on_device_event 记录 info 日志
        context = MagicMock()
        context.logger = MagicMock()
        await plugin.on_device_event(context, inbox)
        context.logger.info.assert_called_once()
        assert "42" in context.logger.info.call_args[0][0]

        # 测试 on_command_result 记录 info 日志
        context = MagicMock()
        context.logger = MagicMock()
        await plugin.on_command_result(context, inbox)
        context.logger.info.assert_called_once()
        assert "42" in context.logger.info.call_args[0][0]

        # 测试 on_timeout 记录 warning 日志
        context = MagicMock()
        context.logger = MagicMock()
        await plugin.on_timeout(context, inbox)
        context.logger.warning.assert_called_once()
        assert "42" in context.logger.warning.call_args[0][0]

        # 测试 on_external_http 记录 info 日志
        context = MagicMock()
        context.logger = MagicMock()
        await plugin.on_external_http(context, inbox)
        context.logger.info.assert_called_once()
        assert "42" in context.logger.info.call_args[0][0]

        # 测试 on_manual_operation 记录 info 日志
        context = MagicMock()
        context.logger = MagicMock()
        await plugin.on_manual_operation(context, inbox)
        context.logger.info.assert_called_once()
        assert "42" in context.logger.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_null_plugin_singleton(self):
        """测试 null_plugin 单例导出"""
        from src.workline_runtime.null_plugin import null_plugin

        assert isinstance(null_plugin, NullPlugin)
        assert null_plugin.plugin_key == "null"
