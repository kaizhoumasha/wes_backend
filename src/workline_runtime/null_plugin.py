"""
NullPlugin - Phase 2 默认插件

空实现插件，用于测试编排流程和验证基础设施。
基于装饰器驱动模式实现。

设计参考: 设计文档 phase2-orchestrator
"""

import logging
from typing import TYPE_CHECKING

from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.types import PluginResult

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

logger = logging.getLogger(__name__)


class NullPlugin(WorklinePlugin):
    """Null 插件 - 空实现

    用于测试编排流程，验证基础设施。
    所有处理器返回空的 PluginResult，不修改 Session。
    """

    plugin_key = "null"
    contract_version = "1.0"

    # ========== 事件处理 ==========

    @on_event("SCAN_COMPLETED")
    async def handle_scan_completed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """扫码完成事件"""
        ctx.logger.info(f"NullPlugin received SCAN_COMPLETED: {inbox.id}")
        return PluginResult()

    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection_completed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """检测完成事件"""
        ctx.logger.info(f"NullPlugin received INSPECTION_COMPLETED: {inbox.id}")
        return PluginResult()

    @on_event("MATERIAL_ARRIVED")
    async def handle_material_arrived(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """物料到达事件"""
        ctx.logger.info(f"NullPlugin received MATERIAL_ARRIVED: {inbox.id}")
        return PluginResult()

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    async def handle_pick_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """抓取放置成功"""
        ctx.logger.info(f"NullPlugin received PICK_AND_PUT SUCCESS: {inbox.id}")
        return PluginResult()

    @on_command("PICK_AND_PUT", result="FAILED")
    async def handle_pick_failed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """抓取放置失败"""
        ctx.logger.warning(f"NullPlugin received PICK_AND_PUT FAILED: {inbox.id}")
        return PluginResult()

    @on_command("MOVE_FORWARD", result="SUCCESS")
    async def handle_move_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """流水线移动成功"""
        ctx.logger.info(f"NullPlugin received MOVE_FORWARD SUCCESS: {inbox.id}")
        return PluginResult()

    @on_command("OUTPUT", result="SUCCESS")
    async def handle_output_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> PluginResult:
        """出料成功"""
        ctx.logger.info(f"NullPlugin received OUTPUT SUCCESS: {inbox.id}")
        return PluginResult()


# 单例导出
null_plugin = NullPlugin()

__all__ = ["NullPlugin", "null_plugin"]
