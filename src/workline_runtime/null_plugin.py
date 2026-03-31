"""
NullPlugin - Phase 2 默认插件

空实现插件，用于测试编排流程和验证基础设施。
所有方法返回空的 PluginResult，不修改 Session。

设计参考: 设计文档 phase2-orchestrator
"""

from typing import TYPE_CHECKING

from src.workline_runtime.types import PluginResult

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


class NullPlugin:
    """Null 插件 - 空实现

    用于测试编排流程，验证基础设施。
    所有方法返回空的 PluginResult，不修改 Session。

    Attributes:
        plugin_key: 插件标识符
    """

    plugin_key = "null"

    async def on_device_event(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """设备事件处理 - 记录日志，不修改 Session

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            空的 PluginResult
        """
        ctx.logger.info(f"NullPlugin received device event: {inbox.id}")
        return PluginResult()

    async def on_command_result(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """命令结果处理 - 记录日志

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            空的 PluginResult
        """
        ctx.logger.info(f"NullPlugin received command result: {inbox.id}")
        return PluginResult()

    async def on_timeout(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """超时处理 - 记录日志

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            空的 PluginResult
        """
        ctx.logger.warning(f"NullPlugin received timeout: {inbox.id}")
        return PluginResult()

    async def on_external_http(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """外部 HTTP 回调处理 - 记录日志

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            空的 PluginResult
        """
        ctx.logger.info(f"NullPlugin received external HTTP: {inbox.id}")
        return PluginResult()

    async def on_manual_operation(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """人工操作处理 - 记录日志

        Args:
            ctx: 插件上下文
            inbox: Inbox 实体

        Returns:
            空的 PluginResult
        """
        ctx.logger.info(f"NullPlugin received manual operation: {inbox.id}")
        return PluginResult()


# 单例导出
null_plugin = NullPlugin()

__all__ = ["NullPlugin", "null_plugin"]
