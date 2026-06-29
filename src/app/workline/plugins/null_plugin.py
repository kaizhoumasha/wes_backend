# 阶段 2 burn-down C5b 镜像:src.workline_runtime.null_plugin 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

"""
NullPlugin - Phase 2 默认插件

空实现插件，用于测试编排流程和验证基础设施。
基于装饰器驱动模式实现。

设计参考: 设计文档 phase2-orchestrator
"""

from typing import TYPE_CHECKING

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.workline.plugins.plugin_base import WorklinePlugin, on_command, on_event

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.app.workline.plugins.plugin_context import PluginContext


class NullPlugin(WorklinePlugin):
    """Null 插件 - 空实现

    用于测试编排流程，验证基础设施。
    所有处理器返回空 RuntimeIntent 列表，不修改 Session。
    """

    plugin_key = "null"
    contract_version = "1.0"

    # ========== 事件处理 ==========

    @on_event("SCAN_COMPLETED")
    async def handle_scan_completed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """扫码完成事件"""
        ctx.logger.info(f"NullPlugin received SCAN_COMPLETED: {inbox.id}")
        return []

    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection_completed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """检测完成事件"""
        ctx.logger.info(f"NullPlugin received INSPECTION_COMPLETED: {inbox.id}")
        return []

    @on_event("MATERIAL_ARRIVED")
    async def handle_material_arrived(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """物料到达事件"""
        ctx.logger.info(f"NullPlugin received MATERIAL_ARRIVED: {inbox.id}")
        return []

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    async def handle_pick_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """抓取放置成功"""
        ctx.logger.info(f"NullPlugin received PICK_AND_PUT SUCCESS: {inbox.id}")
        return []

    @on_command("PICK_AND_PUT", result="FAILED")
    async def handle_pick_failed(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """抓取放置失败"""
        ctx.logger.warning(f"NullPlugin received PICK_AND_PUT FAILED: {inbox.id}")
        return []

    @on_command("MOVE_FORWARD", result="SUCCESS")
    async def handle_move_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """流水线移动成功"""
        ctx.logger.info(f"NullPlugin received MOVE_FORWARD SUCCESS: {inbox.id}")
        return []

    @on_command("OUTPUT", result="SUCCESS")
    async def handle_output_success(self, ctx: "PluginContext", inbox: "WorklineInbox") -> list[RuntimeIntent]:
        """出料成功"""
        ctx.logger.info(f"NullPlugin received OUTPUT SUCCESS: {inbox.id}")
        return []


# 单例导出
null_plugin = NullPlugin()

__all__ = ["NullPlugin", "null_plugin"]
