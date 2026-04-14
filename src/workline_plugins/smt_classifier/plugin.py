"""
SMT 粗分机插件 - 基于装饰器框架

基于装饰器驱动的声明式模式实现的 SMT 粗分机插件。

功能：
- 扫码识别（OK/NG 判定）
- 机械臂抓取放置
- 流水线传输
- 料箱分配
- NG 分流
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.core.logger import logger
from src.workline_runtime.payloads import SixInOne
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    on_timeout,
    step,
)


class ScanEventData(SixInOne, BaseModel):
    """扫码事件 data 字段 - 硬件商约定"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")


class ScanEventPayload(SixInOne, BaseModel):
    """扫码完成事件 Payload - 自动合并 data 字段到顶层"""

    device_code: str
    event_type: str = Field(default="SCAN_COMPLETED")
    timestamp: int | None = Field(default=None)
    data: ScanEventData | None = Field(default=None)

    # 从 data 合并后的字段（由 plugin_base 自动合并）
    location: str | None = Field(default=None, description="扫描位置")

    # 支持简化的 barcode 字段（用于测试和兼容）
    barcode: str | None = Field(default=None, description="条码（简化字段）")
    scan_result: str | None = Field(default=None, description="扫描结果")

    @property
    def barcodes(self) -> list[str]:
        """获取所有非空条码列表"""
        # 包含 barcode 字段
        all_barcodes = [self.barcode, self.LotCode, self.DateCode, self.PONumber, self.MfrPN, self.ProductNo, self.Qty]
        return [b for b in all_barcodes if b]

    @property
    def first_barcode(self) -> str | None:
        """获取第一个条码"""
        if self.barcode:
            return self.barcode
        return self.LotCode or self.DateCode or self.PONumber or self.MfrPN or self.ProductNo or self.Qty


class TaskResultData(SixInOne, BaseModel):
    """任务结果 data 字段"""

    actual_qty: int = Field(default=1)
    location: str | None = Field(default=None)
    reel_diameter: str | None = Field(default=None)
    reel_thickness: str | None = Field(default=None)
    pick_and_put_result: str | None = Field(default=None)


class ErrorDetail(BaseModel):
    """错误详情"""

    error_code: str = Field(default="0")
    error_message: str | None = Field(default=None)


class TaskResultPayload(SixInOne, BaseModel):
    """任务结果回传 Payload - 自动合并 data 和 error_detail"""

    command_code: str
    device_code: str
    result: str = Field(description="执行结果：SUCCESS/FAILED")
    finish_time: int | None = Field(default=None, description="完成时间戳（毫秒）")
    message: str | None = Field(default=None, description="业务回传信息")
    data: TaskResultData | None = Field(default=None, description="业务数据")
    error_detail: ErrorDetail | None = Field(default=None, description="错误详情")

    # 从 data 合并后的字段（由 plugin_base 自动合并）
    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际搬运位置")
    reel_diameter: str | None = Field(default=None, description="料盘尺寸")
    reel_thickness: str | None = Field(default=None, description="料盘厚度")
    pick_and_put_result: str | None = Field(default=None, description="具体结果")

    # 从 error_detail 合并后的字段
    error_code: str = Field(default="0", description="错误码")
    error_message: str | None = Field(default=None, description="错误信息")

    # 支持命令结果简化字段
    command_type: str | None = Field(default=None, description="命令类型（用于匹配）")

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
    """急停事件 Payload"""

    device_code: str
    event_type: str = Field(default="ESTOP_PRESSED")
    timestamp: int | None = Field(default=None)
    data: dict | None = Field(default=None)


if TYPE_CHECKING:
    from src.workline_runtime.plugin_context import PluginContext


class SmtClassifierState:
    """SMT 粗分机状态机"""

    IDLE = "IDLE"
    WAITING_INSPECTION = "WAITING_INSPECTION"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class SmtClassifierPlugin(WorklinePlugin):
    """
    SMT 粗分机插件

    基于 @step 装饰器实现状态迁移，业务流程：
    1. 扫码完成 → 验证条码 → 机械臂抓取到检测位
    2. 检测完成 → OK:流水线传输 / NG:NG缓存位
    3. 机械臂完成 → 流水线传输或NG处理完成
    4. 流水线完成 → 料箱分配 → 最终出料
    """

    plugin_key = "smt_classifier"
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
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1702627300000,
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "PKG1_12345678",
                ...
            }
        }
        """
        location = event.location or ""
        barcode_decision = barcode_decision_service.evaluate_scan(
            barcode=event.barcode,
            lot_code=event.LotCode,
            date_code=event.DateCode,
            po_number=event.PONumber,
            mfr_pn=event.MfrPN,
            product_no=event.ProductNo,
            qty=event.Qty,
        )
        barcode = barcode_decision.barcode

        logger.info(f"Scan completed: barcode={barcode}, location={location}")

        if barcode_decision.decision == BarcodeDecisionType.INVALID:
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(
                    domain="DATA",
                    code=barcode_decision.reason_code or "BARCODE_INVALID",
                    message=barcode_decision.reason_message or f"条码格式错误: {barcode}",
                )
                .build()
            )

        if barcode_decision.decision == BarcodeDecisionType.NG:
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .command(
                    device_role=self.INPUT_ARM,
                    command_type="PICK_AND_PUT",
                    parameters={
                        "barcode": barcode,
                        "source_type": "INPUT_PLATFORM",
                        "target_type": "NG_PLATFORM",
                        "source_loc": location,
                        "target_loc": "STATION_NG_PLATFORM1",
                    },
                )
                .context(
                    {
                        "barcode": barcode,
                        "barcodes": barcode_decision.barcodes,
                        "location": location,
                        "device_code": event.device_code,
                        "pick_place_reason": "SCAN_NG",
                        "step_code": SmtClassifierState.WAITING_PICK_PLACE,
                    }
                )
                .build()
            )

        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role=self.INPUT_ARM,
                command_type="PICK_AND_PUT",
                parameters={
                    "barcode": barcode,
                    "source_type": "INPUT_PLATFORM",
                    "target_type": "PIPELINE_PLATFORM",
                    "source_loc": location,
                    "target_loc": "STATION_PIPELINE1_INPUT1",
                },
            )
            .context(
                {
                    "barcode": barcode,
                    "barcodes": barcode_decision.barcodes,
                    "location": location,
                    "device_code": event.device_code,
                    "pick_place_reason": "INPUT",
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
            "device_code": "ARM01",
            "event_type": "ESTOP_PRESSED",
            "timestamp": 1702627300000,
            "data": null
        }
        """
        logger.error(f"E-STOP pressed: device_code={event.device_code}")

        return (
            PluginResultBuilder(ctx)
            .failure(domain="HARDWARE", code="ESTOP", message=f"急停触发: {event.device_code}")
            .build()
        )

    # ========== 命令结果处理 ==========

    @on_command("PICK_AND_PUT", result="SUCCESS")
    async def handle_pick_and_put_success(self, ctx: PluginContext, _result: TaskResultPayload):
        """
        PICK_AND_PUT 成功处理 - 根据当前状态路由

        状态路由：
        - WAITING_PICK_PLACE: 进料臂完成 → 流水线传输
        - WAITING_OUTPUT: 出料臂完成 → 结束
        """
        current_step = ctx.session.context_json.get("step_code")
        logger.info(f"PICK_AND_PUT succeeded: device_code={_result.device_code}, step={current_step}")

        # 路由1: 进料臂完成 → 流水线传输
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
            if ctx.session.context_json.get("pick_place_reason") == "SCAN_NG":
                logger.info(f"NG pick-and-put succeeded: command_code={_result.command_code}")
                return (
                    PluginResultBuilder(ctx)
                    .transition("pick_ng")
                    .context({"step_code": SmtClassifierState.COMPLETED, "ng_handled": True})
                    .complete()
                    .build()
                )

            barcode = ctx.session.context_json.get("barcode", "")
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

        # 路由2: 出料臂完成 → 结束
        if current_step == SmtClassifierState.WAITING_OUTPUT:
            logger.info(f"Output succeeded: command_code={_result.command_code}")
            return (
                PluginResultBuilder(ctx)
                .transition("output_ok")
                .context({"step_code": SmtClassifierState.COMPLETED})
                .complete()
                .build()
            )

        # 状态不匹配
        logger.error(f"Unexpected step_code for PICK_AND_PUT SUCCESS: {current_step}")
        return (
            PluginResultBuilder(ctx)
            .failure(
                domain="SOFTWARE",
                code="STATE_MISMATCH",
                message=f"PICK_AND_PUT SUCCESS 不期望在状态 {current_step}",
            )
            .build()
        )

    @on_command("PICK_AND_PUT", result="FAILED")
    async def handle_pick_and_put_failed(self, ctx: PluginContext, result: TaskResultPayload):
        """
        PICK_AND_PUT 失败处理 - 根据当前状态路由

        状态路由：
        - WAITING_PICK_PLACE: 进料臂失败 → NG处理或错误
        - WAITING_OUTPUT: 出料臂失败 → 错误

        错误码约定:
        - 1001: 料盘尺寸检测异常
        - 1002: 料盘厚度检测异常
        - 2001: 扫码异常
        - 2002: 搬运失败
        """
        current_step = ctx.session.context_json.get("step_code")
        error_code = result.error_code or "UNKNOWN"
        error_msg = result.error_message or "未知错误"
        logger.error(f"PICK_AND_PUT failed: step={current_step}, error_code={error_code}, message={error_msg}")

        # 路由1: 进料臂失败
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
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
                            "source_type": "PIPELINE_PLATFORM",  # ✅ 使用 Mock 识别的参数名
                            "target_type": "NG_PLATFORM",  # ✅ 使用 Mock 识别的参数名
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

        # 路由2: 出料臂失败
        if current_step == SmtClassifierState.WAITING_OUTPUT:
            error_code = result.error_code or "OUTPUT_ERROR"
            error_msg = result.error_message or "出料失败"
            logger.error(f"Output failed: {error_code}")
            return PluginResultBuilder(ctx).failure(domain="HARDWARE", code=error_code, message=error_msg).build()

        # 状态不匹配
        logger.error(f"Unexpected step_code for PICK_AND_PUT FAILED: {current_step}")
        return (
            PluginResultBuilder(ctx)
            .failure(
                domain="SOFTWARE",
                code="STATE_MISMATCH",
                message=f"PICK_AND_PUT FAILED 不期望在状态 {current_step}",
            )
            .build()
        )

    @on_command("MOVE_FORWARD", result="SUCCESS")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.WAITING_OUTPUT)
    async def handle_conveyor_success(self, ctx: PluginContext, _result: TaskResultPayload):
        """
        流水线传输成功 → 料箱分配 → 最终出料

        业务流程（完整版）：
        1. 流水线传输完成
        2. 料箱分配服务（allocation_mock）
        3. 若需要 AGV，调度 AGV 搬运空料箱（TODO）
        4. 下发出料命令到 ARM02

        当前实现：随机生成料箱位置，待集成真实分配服务
        """
        logger.info("Conveyor move succeeded, starting bin allocation")

        barcode = ctx.session.context_json.get("barcode", "")
        reel_diameter = ctx.session.context_json.get("reel_diameter", "")

        # 料箱分配（暂时使用随机料箱，后续集成真实分配服务）
        bin_location = await self._allocate_bin(barcode)
        logger.info(f"Bin allocated: {bin_location}")

        return (
            PluginResultBuilder(ctx)
            .transition("conveyor_ok")
            .command(
                device_role=self.OUTPUT_ARM,
                command_type="PICK_AND_PUT",
                parameters={
                    "barcode": barcode,
                    "reel_diameter": reel_diameter,
                    "target_type": "BIN",
                    "target_loc": bin_location["bin_id"],
                    "bin_type": bin_location["bin_type"],
                },
            )
            .context({"step_code": SmtClassifierState.WAITING_OUTPUT, "bin_location": bin_location})
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

    # ========== 超时处理 ==========

    @on_timeout()
    async def handle_timeout(self, ctx: PluginContext, _inbox: Any):
        """
        超时处理 → 错误

        注意：使用 Any 类型避免 Pydantic 自动解析（inbox 应该是原始对象，不是从 payload 解析）
        """
        logger.warning(f"Timeout: session_id={ctx.session.id}")

        return PluginResultBuilder(ctx).failure(domain="TIMEOUT", code="DEVICE_TIMEOUT", message="设备响应超时").build()

    # ========== 辅助方法 ==========

    async def _allocate_bin(self, barcode: str) -> dict:
        """
        料箱分配（完整流程待实现）

        完整业务流程：
        1. 调用 /api/v1/bin-allocation/allocate
        2. 若返回 AGV_REQUIRED，调度 AGV 搬运空料箱（TODO）
        3. 若返回 ALLOCATED，使用分配的料箱位置

        当前实现：随机生成料箱位置
        TODO: 集成真实料箱分配服务
        TODO: 集成 AGV 调度服务

        Args:
            barcode: 物料条码

        Returns:
            dict: 料箱位置信息 {bin_id, bin_type, bin_cell_location}
        """
        # TODO: 集成真实分配服务
        # allocation_response = await self._call_allocation_service(barcode)
        # if allocation_response.allocation_status == "AGV_REQUIRED":
        #     # TODO: 调度 AGV
        #     await self._dispatch_agv(allocation_response.target_bin)
        # return allocation_response.target_bin

        # 临时实现：随机料箱
        return self._generate_random_bin(barcode)

    def _generate_random_bin(self, barcode: str) -> dict:
        """
        生成随机料箱位置（临时实现）

        Args:
            barcode: 物料条码

        Returns:
            dict: 料箱位置信息
        """
        bin_id = f"BIN_{random.randint(100, 999)}"
        bin_types = ["三格箱", "五格箱", "九格箱"]
        bin_type = random.choice(bin_types)
        cell_location = str(random.randint(1, 9))

        logger.info(f"Generated random bin: barcode={barcode}, bin_id={bin_id}, bin_type={bin_type}")

        return {
            "bin_id": bin_id,
            "bin_type": bin_type,
            "bin_cell_location": cell_location,
        }

    async def _call_allocation_service(self, barcode: str) -> dict:
        """
        调用料箱分配服务（TODO: 实现）

        Args:
            barcode: 物料条码

        Returns:
            dict: 分配结果
        """
        # TODO: 实现 HTTP 调用到 /api/v1/bin-allocation/allocate
        raise NotImplementedError("料箱分配服务待集成")

    async def _dispatch_agv(self, target_bin: dict) -> None:
        """
        调度 AGV 搬运空料箱（TODO: 实现）

        Args:
            target_bin: 目标料箱位置
        """
        # TODO: 实现 AGV 调度逻辑
        # 1. 调用 AGV 调度接口
        # 2. 等待 AGV 任务完成
        # 3. 继续出料流程
        raise NotImplementedError("AGV 调度服务待集成")


# ==================== 导出插件实例 ====================

smt_classifier_plugin = SmtClassifierPlugin()


__all__ = ["SmtClassifierPlugin", "smt_classifier_plugin"]
