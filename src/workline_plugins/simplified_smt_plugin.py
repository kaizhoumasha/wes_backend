"""
SMT 粗分机简化插件 - 使用装饰器框架

基于装饰器驱动的声明式模式实现的 SMT 粗分机插件。

功能：
- 扫码识别（OK/NG 判定）
- 机械臂抓取放置
- 流水线传输
- NG 分流
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.core.logger import logger
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    on_timeout,
    step,
)

# ========== Payload 定义（硬件商约定）==========


class ScanEventData(BaseModel):
    """扫码事件 data 字段 - 硬件商约定"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")
    barcode1: str | None = Field(default=None, description="条码1")
    barcode2: str | None = Field(default=None, description="条码2")
    barcode3: str | None = Field(default=None, description="条码3")
    barcode4: str | None = Field(default=None, description="条码4")
    barcode5: str | None = Field(default=None, description="条码5")
    barcode6: str | None = Field(default=None, description="条码6")


class ScanEventPayload(BaseModel):
    """
    扫码完成事件 Payload - 硬件商约定

    顶层字段:
        device_id: 设备ID (ARM01/ARM03)
        event_type: "SCAN_COMPLETED"
        timestamp: 事件时间戳（毫秒）
        data: 扫码数据

    注: plugin_base._invoke_handler 会自动合并 data 子对象到顶层
    """

    device_id: str = Field(description="设备ID")
    event_type: str = Field(default="SCAN_COMPLETED", description="事件类型")
    timestamp: int | None = Field(default=None, description="事件时间戳（毫秒）")
    data: ScanEventData | None = Field(default=None, description="扫码数据")

    # 从 data 合并后的字段（由 plugin_base 自动合并）
    location: str | None = Field(default=None, description="扫描位置")
    barcode1: str | None = Field(default=None, description="条码1")
    barcode2: str | None = Field(default=None, description="条码2")
    barcode3: str | None = Field(default=None, description="条码3")
    barcode4: str | None = Field(default=None, description="条码4")
    barcode5: str | None = Field(default=None, description="条码5")
    barcode6: str | None = Field(default=None, description="条码6")

    @property
    def barcodes(self) -> list[str]:
        """获取所有非空条码列表"""
        return [
            b for b in [self.barcode1, self.barcode2, self.barcode3, self.barcode4, self.barcode5, self.barcode6] if b
        ]

    @property
    def first_barcode(self) -> str | None:
        """获取第一个条码（最常用）"""
        return self.barcode1 or self.barcode2 or self.barcode3 or self.barcode4 or self.barcode5 or self.barcode6


class TaskResultData(BaseModel):
    """任务结果 data 字段 - 硬件商约定"""

    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际搬运位置")
    barcode1: str | None = Field(default=None, description="条码1")
    barcode2: str | None = Field(default=None, description="条码2")
    barcode3: str | None = Field(default=None, description="条码3")
    barcode4: str | None = Field(default=None, description="条码4")
    barcode5: str | None = Field(default=None, description="条码5")
    barcode6: str | None = Field(default=None, description="条码6")
    reel_diameter: str | None = Field(default=None, description="料盘尺寸，如 15inch")
    reel_thickness: str | None = Field(default=None, description="料盘厚度（mm）")
    pick_and_put_result: str | None = Field(default=None, description="具体结果：PUT_FINISHED/MOVE_FINISHED")


class ErrorDetail(BaseModel):
    """错误详情 - 硬件商约定"""

    error_code: str = Field(default="0", description="错误码：0=无错误, 1001=尺寸检测异常, 1002=厚度检测异常")
    error_message: str | None = Field(default=None, description="具体错误信息")


class TaskResultPayload(BaseModel):
    """
    任务结果回传 Payload - 硬件商约定

    顶层字段:
        command_id: 命令ID
        device_id: 设备ID
        result: 执行结果 (SUCCESS/FAILED)
        finish_time: 完成时间戳（毫秒）
        message: 业务回传信息
        data: 业务数据
        error_detail: 错误详情

    注: plugin_base._invoke_handler 会自动合并 data 子对象到顶层
    """

    command_id: str = Field(description="命令ID")
    device_id: str = Field(description="设备ID")
    result: str = Field(description="执行结果：SUCCESS/FAILED")
    finish_time: int | None = Field(default=None, description="完成时间戳（毫秒）")
    message: str | None = Field(default=None, description="业务回传信息")
    data: TaskResultData | None = Field(default=None, description="业务数据")
    error_detail: ErrorDetail | None = Field(default=None, description="错误详情")

    # 从 data 合并后的字段（由 plugin_base 自动合并）
    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际搬运位置")
    barcode1: str | None = Field(default=None)
    barcode2: str | None = Field(default=None)
    barcode3: str | None = Field(default=None)
    barcode4: str | None = Field(default=None)
    barcode5: str | None = Field(default=None)
    barcode6: str | None = Field(default=None)
    reel_diameter: str | None = Field(default=None, description="料盘尺寸")
    reel_thickness: str | None = Field(default=None, description="料盘厚度")
    pick_and_put_result: str | None = Field(default=None, description="具体结果")

    # 从 error_detail 合并后的字段
    error_code: str = Field(default="0", description="错误码")
    error_message: str | None = Field(default=None, description="错误信息")

    @property
    def is_success(self) -> bool:
        """是否成功"""
        return self.result == "SUCCESS"

    @property
    def is_dimension_error(self) -> bool:
        """是否尺寸检测异常"""
        return self.error_code == "1001"

    @property
    def is_thickness_error(self) -> bool:
        """是否厚度检测异常"""
        return self.error_code == "1002"


class EStopEventPayload(BaseModel):
    """
    急停事件 Payload - 硬件商约定

    顶层字段:
        device_id: 设备ID
        event_type: "ESTOP_PRESSED"
        timestamp: 事件时间戳（毫秒）
        data: null
    """

    device_id: str = Field(description="设备ID")
    event_type: str = Field(default="ESTOP_PRESSED", description="事件类型")
    timestamp: int | None = Field(default=None, description="事件时间戳（毫秒）")
    data: dict | None = Field(default=None, description="急停事件无业务数据")


if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


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

    基于 @step 装饰器实现状态迁移，业务流程：
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
        扫码完成 → 机械臂抓取到流水线进料位置

        硬件商约定 payload:
        {
            "device_id": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1702627300000,
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "PKG1_12345678",
                ...
            }
        }
        """
        barcode = event.first_barcode or ""
        location = event.location or ""

        logger.info(f"Scan completed: barcode={barcode}, location={location}")

        # 业务规则：验证条码
        if not self._is_valid_barcode(barcode):
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(domain="DATA", code="BARCODE_INVALID", message=f"条码格式错误: {barcode}")
                .build()
            )

        # 派发抓取命令：从串杆 → 流水线进料位置
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role=self.INPUT_ARM,
                command_type="PICK_AND_PUT",
                parameters={
                    "barcode": barcode,
                    "source_location": location,
                    "target_location": "STATION_PIPELINE_INPUT1",  # 根据设备ID动态选择
                },
            )
            .context(
                {
                    "barcode": barcode,
                    "barcodes": event.barcodes,  # 所有条码
                    "location": location,
                    "device_id": event.device_id,
                    "step_code": SmtClassifierState.WAITING_PICK_PLACE,
                }
            )
            .build()
        )

    @on_event("ESTOP_PRESSED")
    async def handle_estop(self, ctx: PluginContext, event: EStopEventPayload):
        """
        急停事件 → 错误状态

        硬件商约定 payload:
        {
            "device_id": "ARM01",
            "event_type": "ESTOP_PRESSED",
            "timestamp": 1702627300000,
            "data": null
        }
        """
        logger.error(f"E-STOP pressed: device_id={event.device_id}")

        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code="ESTOP", message=f"急停触发: {event.device_id}")
            .build()
        )

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    @step(SmtClassifierState.WAITING_PICK_PLACE, SmtClassifierState.WAITING_CONVEYOR)
    async def handle_pick_success(self, ctx: PluginContext, _result: TaskResultPayload):
        """
        抓取放置成功 → 流水线传输

        硬件商约定 payload:
        {
            "command_id": "CMD-20251215-1001",
            "device_id": "ARM01",
            "result": "SUCCESS",
            "data": {
                "actual_qty": 1,
                "location": "STATION_PIPELINE1_INPUT1",
                "reel_diameter": "15inch",
                "reel_thickness": "20",
                "pick_and_put_result": "PUT_FINISHED"
            }
        }
        """
        logger.info(f"Pick and place succeeded: device_id={_result.device_id}")

        barcode = ctx.session.context_json.get("barcode", "")

        # 派发流水线传输命令
        return (
            PluginResultBuilder(ctx)
            .transition("pick_ok")
            .command(device_role=self.CONVEYOR, command_type="MOVE_FORWARD", parameters={"barcode": barcode})
            .context(
                {
                    "reel_diameter": _result.reel_diameter,
                    "reel_thickness": _result.reel_thickness,
                    "step_code": SmtClassifierState.WAITING_CONVEYOR,
                }
            )
            .build()
        )

    @on_command("PICK_AND_PUT", result="FAILED")
    @step(SmtClassifierState.WAITING_PICK_PLACE, SmtClassifierState.ERROR)
    async def handle_pick_failed(self, ctx: PluginContext, result: TaskResultPayload):
        """
        抓取放置失败 → 错误

        错误码约定:
        - 1001: 料盘尺寸检测异常
        - 1002: 料盘厚度检测异常
        - 2001: 扫码异常
        - 2002: 搬运失败
        """
        error_code = result.error_code or "UNKNOWN"
        error_msg = result.error_message or "未知错误"

        logger.error(f"Pick and place failed: error_code={error_code}, message={error_msg}")

        # 尺寸/厚度检测异常 → NG 缓存位
        if result.is_dimension_error or result.is_thickness_error:
            barcode = ctx.session.context_json.get("barcode", "")
            return (
                PluginResultBuilder(ctx)
                .transition("inspection_ng")
                .command(
                    device_role=self.INPUT_ARM,
                    command_type="PICK_AND_PUT",
                    parameters={
                        "barcode": barcode,
                        "source": "PIPELINE_PLATFORM",
                        "target": "NG_PLATFORM",
                    },
                )
                .context({"inspection_error": error_code, "step_code": SmtClassifierState.WAITING_PICK_PLACE})
                .build()
            )

        # 其他错误
        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code=error_code, message=f"抓取放置失败: {error_msg}")
            .build()
        )

    @on_command("MOVE_FORWARD", result="SUCCESS")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.WAITING_OUTPUT)
    async def handle_conveyor_success(self, ctx: PluginContext, _result: TaskResultPayload):
        """
        流水线传输成功 → 最终出料
        """
        logger.info("Conveyor move succeeded")

        barcode = ctx.session.context_json.get("barcode", "")
        reel_diameter = ctx.session.context_json.get("reel_diameter", "")

        return (
            PluginResultBuilder(ctx)
            .transition("conveyor_ok")
            .command(
                device_role=self.OUTPUT_ARM,
                command_type="PICK_AND_PUT",
                parameters={"barcode": barcode, "reel_diameter": reel_diameter},
            )
            .context({"step_code": SmtClassifierState.WAITING_OUTPUT})
            .build()
        )

    @on_command("MOVE_FORWARD", result="FAILED")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.ERROR)
    async def handle_conveyor_failed(self, ctx: PluginContext, result: TaskResultPayload):
        """
        流水线传输失败 → 错误
        """
        error_code = result.error_code or "CONVEYOR_ERROR"
        error_msg = result.error_message or "流水线传输失败"

        logger.error(f"Conveyor move failed: {error_code}")

        return PluginResultBuilder(ctx).failure(domain="HARDWARE", code=error_code, message=error_msg).build()

    @on_command("PICK_AND_PUT", result="SUCCESS")
    @step(SmtClassifierState.WAITING_OUTPUT, SmtClassifierState.COMPLETED)
    async def handle_output_success(self, ctx: PluginContext, _result: TaskResultPayload):
        """
        最终出料成功 → 完成
        """
        logger.info(f"Output succeeded: command_id={_result.command_id}")

        return PluginResultBuilder(ctx).transition("output_ok").complete().build()

    @on_command("PICK_AND_PUT", result="FAILED")
    @step(SmtClassifierState.WAITING_OUTPUT, SmtClassifierState.ERROR)
    async def handle_output_failed(self, ctx: PluginContext, result: TaskResultPayload):
        """
        最终出料失败 → 错误
        """
        error_code = result.error_code or "OUTPUT_ERROR"
        error_msg = result.error_message or "出料失败"

        logger.error(f"Output failed: {error_code}")

        return PluginResultBuilder(ctx).failure(domain="HARDWARE", code=error_code, message=error_msg).build()

    # ========== 超时处理 ==========

    @on_timeout()
    async def handle_timeout(self, ctx: PluginContext, inbox: WorklineInbox):
        """
        超时处理 → 错误
        """
        logger.warning(f"Timeout: inbox_id={inbox.id}")

        return PluginResultBuilder(ctx).failure(domain="TIMEOUT", code="DEVICE_TIMEOUT", message="设备响应超时").build()

    # ========== 辅助方法 ==========

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
