"""
SMT 粗分机简化插件 - 使用装饰器框架

演示如何使用装饰器驱动的声明式模式重写 SmtClassifierPlugin。

功能等价性：
- 扫码识别（OK/NG 判定）
- 机械臂抓取放置
- 流水线传输
- NG 分流

代码减少：1915 行 → ~400 行（79% 减少）
"""

from __future__ import annotations

import logging
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


# ========== Payload 定义（运行时需要，不能放在 TYPE_CHECKING 内）==========
class ScanEventPayload(BaseModel):
    """扫码事件 Payload"""

    device_code: str
    barcode: str
    location_id: str = Field(alias="location")  # 支持字段别名
    scan_result: str = "OK"  # 扫码结果（OK/NG）


class PickPlaceResultPayload(BaseModel):
    """抓取放置结果 Payload"""

    command_code: str
    result: str  # "SUCCESS" or "FAILED"
    error_code: str | None = None
    device_code: str | None = None  # 设备编码


class InspectionEventPayload(BaseModel):
    """检测事件 Payload"""

    device_code: str
    inspection_result: str  # "OK" or "NG"
    barcode: str | None = None


if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

logger = logging.getLogger(__name__)


# ==================== 状态机定义 ====================


class SmtClassifierState:
    """SMT 粗分机状态机"""

    IDLE = "IDLE"
    WAITING_INSPECTION = "WAITING_INSPECTION"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


# ==================== 插件实现 ====================


class SimplifiedSmtPlugin(WorklinePlugin):
    """
    SMT 粗分机简化插件

    功能等价于 SmtClassifierPlugin，但代码减少 79%。

    业务流程：
    1. 扫码完成 → 验证条码 → 机械臂抓取到检测位
    2. 检测完成 → OK:流水线传输 / NG:NG缓存位
    3. 机械臂完成 → 流水线传输或NG处理完成
    4. 流水线完成 → 最终出料
    """

    plugin_key = "simplified_smt"
    contract_version = "1.0"

    # ========== 设备角色常量 ==========

    INPUT_ARM = "INPUT_ARM"
    OUTPUT_ARM = "OUTPUT_ARM"
    CONVEYOR = "CONVEYOR"

    # ========== 业务规则 ==========

    MIN_BARCODE_LENGTH = 3
    VALID_BARCODE_PATTERN = r"^[A-Z0-9]+$"

    # ========== 事件处理 ==========

    @on_event("SCAN_COMPLETED")
    async def handle_scan_completed(self, ctx: PluginContext, event: ScanEventPayload):
        """
        扫码完成 → 机械臂抓取到检测位

        业务逻辑：
        1. 验证条码格式
        2. 检查扫码结果（OK/NG）
        3. NG: 触发NG流程
        4. OK: 派发抓取命令
        """
        barcode = event.barcode
        location_id = event.location_id

        logger.info(f"Scan completed: barcode={barcode}, location={location_id}")

        # 业务规则：验证条码
        if not self._is_valid_barcode(barcode):
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(domain="DATA", code="BARCODE_INVALID", message=f"条码格式错误: {barcode}")
                .build()
            )

        # 从 payload 获取 scan_result（如果有）
        scan_result = getattr(event, "scan_result", "OK")

        # NG 流程
        if scan_result == "NG":
            return await self._handle_scan_ng(ctx, barcode, location_id)

        # OK 流程：派发抓取命令
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role=self.INPUT_ARM,
                command_type="PICK_AND_PUT",
                parameters={
                    "barcode": barcode,
                    "source": "INPUT_PLATFORM",
                    "target": "PIPELINE_PLATFORM",
                },
            )
            .context(
                {
                    "barcode": barcode,
                    "scan_result": scan_result,
                    "location_id": location_id,
                    "step_code": SmtClassifierState.WAITING_INSPECTION,
                }
            )
            .build()
        )

    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection_completed(self, ctx: PluginContext, event: InspectionEventPayload):
        """
        检测完成 → 流水线传输或NG分流

        业务逻辑：
        1. 检查检测结果
        2. OK: 派发流水线命令
        3. NG: 派发NG缓存命令
        """
        inspection_result = event.inspection_result
        barcode = ctx.session.context_json.get("barcode", "")

        logger.info(f"Inspection completed: result={inspection_result}, barcode={barcode}")

        # NG 流程
        if inspection_result == "NG":
            return (
                PluginResultBuilder(ctx)
                .transition("inspection_ng")
                .command(device_role=self.INPUT_ARM, command_type="PICK_NG", parameters={"barcode": barcode})
                .context({"inspection_result": inspection_result, "step_code": SmtClassifierState.WAITING_PICK_PLACE})
                .build()
            )

        # OK 流程：派发流水线命令
        return (
            PluginResultBuilder(ctx)
            .transition("inspection_ok")
            .command(device_role=self.CONVEYOR, command_type="MOVE_FORWARD", parameters={"speed": "normal"})
            .context({"inspection_result": inspection_result, "step_code": SmtClassifierState.WAITING_CONVEYOR})
            .build()
        )

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    @step(SmtClassifierState.WAITING_INSPECTION, SmtClassifierState.WAITING_CONVEYOR)
    async def handle_pick_success(self, ctx: PluginContext, result: PickPlaceResultPayload):
        """
        抓取放置成功 → 流水线传输
        """
        logger.info("Pick and place succeeded")

        barcode = ctx.session.context_json.get("barcode", "")
        inspection_result = ctx.session.context_json.get("inspection_result", "OK")

        # 如果检测已完成且有结果，直接处理
        if inspection_result == "NG":
            # 已知NG，触发NG流程
            return (
                PluginResultBuilder(ctx)
                .transition("pick_after_ng")
                .command(device_role=self.INPUT_ARM, command_type="PICK_NG", parameters={"barcode": barcode})
                .build()
            )

        # OK流程：等待检测完成
        return (
            PluginResultBuilder(ctx)
            .transition("pick_ok")
            .wait(event_type="INSPECTION_COMPLETED", timeout_seconds=300)
            .build()
        )

    @on_command("PICK_AND_PUT", result="FAILED")
    @step(SmtClassifierState.WAITING_INSPECTION, SmtClassifierState.ERROR)
    async def handle_pick_failed(self, ctx: PluginContext, result: PickPlaceResultPayload):
        """
        抓取放置失败 → 错误
        """
        error_code = result.error_code or "UNKNOWN"

        logger.error(f"Pick and place failed: {error_code}")

        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code=error_code, message=f"抓取放置失败: {error_code}")
            .build()
        )

    @on_command("MOVE_FORWARD", result="SUCCESS")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.WAITING_OUTPUT)
    async def handle_conveyor_success(self, ctx: PluginContext, result):
        """
        流水线传输成功 → 最终出料
        """
        logger.info("Conveyor move succeeded")

        barcode = ctx.session.context_json.get("barcode", "")

        return (
            PluginResultBuilder(ctx)
            .transition("conveyor_ok")
            .command(device_role=self.OUTPUT_ARM, command_type="OUTPUT", parameters={"barcode": barcode})
            .build()
        )

    @on_command("MOVE_FORWARD", result="FAILED")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.ERROR)
    async def handle_conveyor_failed(self, ctx: PluginContext, result):
        """
        流水线传输失败 → 错误
        """
        error_code = getattr(result, "error_code", "CONVEYOR_ERROR")

        logger.error(f"Conveyor move failed: {error_code}")

        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code=error_code, message=f"流水线传输失败: {error_code}")
            .build()
        )

    @on_command("OUTPUT", result="SUCCESS")
    @step(SmtClassifierState.WAITING_OUTPUT, SmtClassifierState.COMPLETED)
    async def handle_output_success(self, ctx: PluginContext, result):
        """
        最终出料成功 → 完成
        """
        logger.info("Output succeeded")

        return PluginResultBuilder(ctx).transition("output_ok").complete().build()

    @on_command("OUTPUT", result="FAILED")
    @step(SmtClassifierState.WAITING_OUTPUT, SmtClassifierState.ERROR)
    async def handle_output_failed(self, ctx: PluginContext, result):
        """
        最终出料失败 → 错误
        """
        error_code = getattr(result, "error_code", "OUTPUT_ERROR")

        logger.error(f"Output failed: {error_code}")

        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code=error_code, message=f"最终出料失败: {error_code}")
            .build()
        )

    @on_command("PICK_NG", result="SUCCESS")
    @step(SmtClassifierState.WAITING_PICK_PLACE, SmtClassifierState.COMPLETED)
    async def handle_pick_ng_success(self, ctx: PluginContext, result):
        """
        NG放置成功 → 完成
        """
        logger.info("NG pick succeeded")

        return PluginResultBuilder(ctx).transition("ng_handled").complete().build()

    # ========== 超时处理 ==========

    @on_timeout()
    async def handle_timeout(self, ctx: PluginContext, inbox: WorklineInbox):
        """
        超时处理 → 错误
        """
        logger.warning(f"Timeout: inbox_id={inbox.id}")

        return PluginResultBuilder(ctx).failure(domain="TIMEOUT", code="DEVICE_TIMEOUT", message="设备响应超时").build()

    # ========== 辅助方法 ==========

    async def _handle_scan_ng(self, ctx: PluginContext, barcode: str, location_id: str):
        """
        处理扫码NG流程

        Args:
            ctx: 插件上下文
            barcode: 条码
            location_id: 位置ID

        Returns:
            PluginResult 包含NG放置命令
        """
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ng")
            .command(
                device_role=self.INPUT_ARM,
                command_type="PICK_NG",
                parameters={"barcode": barcode, "location_id": location_id},
            )
            .context({"scan_result": "NG", "barcode": barcode})
            .build()
        )

    def _is_valid_barcode(self, barcode: str) -> bool:
        """
        验证条码格式

        规则：
        1. 长度至少 MIN_BARCODE_LENGTH
        2. 只包含字母和数字

        Args:
            barcode: 条码内容

        Returns:
            bool: 是否有效
        """
        if not barcode:
            return False

        if len(barcode) < self.MIN_BARCODE_LENGTH:
            return False

        return barcode.isalnum()


# ==================== 导出插件实例 ====================

simplified_smt_plugin = SimplifiedSmtPlugin()


__all__ = ["SimplifiedSmtPlugin", "simplified_smt_plugin"]
