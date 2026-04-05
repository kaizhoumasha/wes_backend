"""
简化示例插件 - 展示新框架的使用方式

对比 SmtClassifierPlugin (1915 行) vs SimplePlugin (~150 行)

简化要点：
1. 装饰器声明事件类型：@on_event("SCAN_COMPLETED")
2. 装饰器声明状态迁移：@step("IDLE", "WAITING_INSPECTION")
3. 自动路由：无需手动判断 event_type
4. 自动解析：Pydantic 自动验证 payload
5. 链式构建：PluginResultBuilder 简化响应构建
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    on_timeout,
    step,
)
from workline_runtime import PluginResult

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


# ========== Payload 定义（Pydantic 自动验证）==========


class ScanEventPayload(BaseModel):
    """扫码事件 Payload"""

    device_code: str
    barcode: str
    location_id: str = Field(alias="location")  # 支持字段别名


class PickPlaceResultPayload(BaseModel):
    """抓取放置结果 Payload"""

    command_code: str
    result: str  # "SUCCESS" or "FAILED"
    error_code: str | None = None


# ========== 插件实现 ==========


class SimplePlugin(WorklinePlugin):
    """
    简化示例插件

    业务流程：
    1. 扫码完成 → 抓取放置
    2. 抓取成功 → 完成
    3. 抓取失败 → 错误
    """

    plugin_key = "simple"
    contract_version = "1.0"

    # 状态常量
    IDLE = "IDLE"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

    # ========== 事件处理 ==========

    @on_event("SCAN_COMPLETED")
    @step(IDLE, WAITING_PICK_PLACE)
    async def handle_scan_completed(self, ctx: PluginContext, event: ScanEventPayload) -> PluginResult:
        """
        扫码完成 → 抓取放置

        自动校验：
        - event_type == "SCAN_COMPLETED"
        - 当前状态 == IDLE
        - 自动设置目标状态 == WAITING_PICK_PLACE
        """
        ctx.logger.info(f"Scan completed: barcode={event.barcode}")

        # 业务逻辑：验证条码
        if not self._validate_barcode(event.barcode):
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(
                    domain="DATA",
                    code="BARCODE_NG",
                    message=f"条码格式错误: {event.barcode}",
                )
                .build()
            )

        # 派发抓取放置命令
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role="INPUT_ARM",
                command_type="PICK_AND_PUT",
                parameters={
                    "barcode": event.barcode,
                    "source": "INPUT_PLATFORM",
                    "target": "PIPELINE_PLATFORM",
                },
            )
            .context({"last_barcode": event.barcode})
            .build()
        )

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    @step(WAITING_PICK_PLACE, COMPLETED)
    async def handle_pick_place_success(self, ctx: PluginContext, _result: PickPlaceResultPayload) -> PluginResult:
        """
        抓取放置成功 → 完成

        自动路由：
        - command_type == "PICK_AND_PUT"
        - result == "SUCCESS"
        """
        ctx.logger.info("Pick and place succeeded")

        return PluginResultBuilder(ctx).transition("pick_place_ok").complete().build()

    @on_command("PICK_AND_PUT", result="FAILED")
    @step(WAITING_PICK_PLACE, ERROR)
    async def handle_pick_place_failed(self, ctx: PluginContext, result: PickPlaceResultPayload) -> PluginResult:
        """
        抓取放置失败 → 错误

        自动路由：
        - command_type == "PICK_AND_PUT"
        - result == "FAILED"
        """
        ctx.logger.error(f"Pick and place failed: {result.error_code}")

        return (
            PluginResultBuilder(ctx)
            .transition("error")
            .failure(
                domain="HARDWARE",
                code=result.error_code or "UNKNOWN",
                message=f"抓取放置失败: {result.error_code}",
            )
            .build()
        )

    # ========== 超时处理 ==========

    @on_timeout()
    async def handle_timeout(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        """超时 → 错误"""
        ctx.logger.warning(f"Timeout: inbox_id={inbox.id}")

        return (
            PluginResultBuilder(ctx)
            .transition("error")
            .failure(
                domain="TIMEOUT",
                code="DEVICE_TIMEOUT",
                message="设备响应超时",
            )
            .build()
        )

    # ========== 辅助方法 ==========

    def _validate_barcode(self, barcode: str) -> bool:
        """验证条码格式（示例：至少3位字母数字）"""
        return len(barcode) >= 3 and barcode.isalnum()


# ========== 导出插件实例 ==========

simple_plugin = SimplePlugin()


# ========== 对比：传统方式 vs 简化方式 ==========

"""
传统方式需要：
1. 实现 on_device_event 方法（手动判断 event_type）
2. 实现 on_command_result 方法（手动判断 command_type + result）
3. 实现 on_timeout 方法
4. 手动解析 payload（~20 行）
5. 手动构建 PluginResult（~15 行）
6. 手动管理状态（~10 行）

总代码：~200 行

简化方式：
1. @on_event("SCAN_COMPLETED") - 1 行
2. @on_command("PICK_AND_PUT", result="SUCCESS") - 1 行
3. @on_timeout() - 1 行
4. Pydantic 自动解析 - 0 行（声明式）
5. PluginResultBuilder 链式构建 - ~10 行
6. @step 自动状态管理 - 1 行

总代码：~50 行

简化幅度：**75%**
"""


__all__ = ["SimplePlugin", "simple_plugin"]
