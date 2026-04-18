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

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.core.logger import logger
from src.workline_runtime.payloads import SixInOne
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    on_timeout,
    resolve_normalized_command_envelope,
    resolve_normalized_command_failure,
    step,
)
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult


class ScanEventData(SixInOne, BaseModel):
    """扫码事件 data 字段 - 包含六合一码 + 扫描位置"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")


class ScanEventPayload(BaseModel):
    """扫码完成事件 Payload"""

    device_code: str
    event_type: str = Field(default="SCAN_COMPLETED")
    timestamp: int | None = Field(default=None)
    data: ScanEventData | None = Field(default=None)


class MeasurementResultData(SixInOne, BaseModel):
    """测量结果数据"""

    pkg_id: str = Field(..., description="料盘ID")
    reel_diameter: float | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: float | None = Field(default=None, description="料盘厚度测量值")


class PickPlaceResultData(BaseModel):
    """机械臂抓取放置执行结果 data 字段

    机械臂执行 PICK_AND_PUT 命令后，通过回调上报的执行结果，
    包含料盘物理尺寸测量数据（直径、厚度）。
    """

    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际放置位置")
    reel_diameter: str | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: str | None = Field(default=None, description="料盘厚度测量值")
    pick_and_place_result: str | None = Field(
        default=None,
        validation_alias=AliasChoices("pick_and_place_result", "pick_and_put_result"),
        description="抓取放置具体结果",
    )


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
    WAITING_MEASUREMENT = "WAITING_MEASUREMENT"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


def _parse_measurement_result_data(result: NormalizedCommandResult) -> MeasurementResultData | None:
    """从标准化命令结果中提取测量结果 data。"""

    if not isinstance(result.data, dict) or not result.data:
        return None

    try:
        return MeasurementResultData.model_validate(result.data)
    except ValidationError:
        return None


def _parse_pick_place_result_data(result: NormalizedCommandResult) -> PickPlaceResultData | None:
    """从标准化命令结果中提取抓取放置结果 data。"""

    if not isinstance(result.data, dict) or not result.data:
        return None

    try:
        return PickPlaceResultData.model_validate(result.data)
    except ValidationError:
        return None


def _build_payload_invalid_failure(ctx: PluginContext, message: str):
    """构造标准命令结果包络缺失时的统一失败返回。"""

    return PluginResultBuilder(ctx).failure(domain="DATA", code="PAYLOAD_INVALID", message=message).build()


def _build_state_mismatch_failure(ctx: PluginContext, command_type: str, result_name: str, step_code: str | None):
    """构造命令结果落在非法状态时的统一失败返回。"""

    return (
        PluginResultBuilder(ctx)
        .failure(
            domain="SOFTWARE",
            code="STATE_MISMATCH",
            message=f"{command_type} {result_name} 不期望在状态 {step_code}",
        )
        .build()
    )


def _scan_ng_command_parameters(*, pkg_id: str, location: str) -> dict[str, str]:
    """统一构造扫码 NG 分流命令参数。"""

    return {
        "barcode": pkg_id,
        "source_type": "INPUT_PLATFORM",
        "target_type": "NG_PLATFORM",
        "source_loc": location,
        "target_loc": "STATION_NG_PLATFORM1",
    }


def _scan_ng_context(*, pkg_id: str, barcodes: list[str], location: str, device_code: str) -> dict[str, Any]:
    """统一构造扫码 NG 分流上下文。"""

    return {
        "barcode": pkg_id,
        "barcodes": barcodes,
        "location": location,
        "device_code": device_code,
        "pick_place_reason": "SCAN_NG",
        "step_code": SmtClassifierState.WAITING_PICK_PLACE,
    }


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
              "location": "ARM01",
              "HHPN": "620100L00-011-G",
              "MfrPN": "CC0402JRNPO9BN220",
              "Qty": "7387",
              "DateCode": "122625",
              "LotCode": "8904936031",
              "PkgID": "SVYU00125TP4LCR02_2"
            }
        }
        """
        location = event.data.location if event.data else ""

        # 检查扫码数据是否存在
        if not event.data:
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(
                    domain="DATA",
                    code="MISSING_SCAN_DATA",
                    message="扫码事件缺少 data 字段",
                )
                .build()
            )

        # 使用六合一码进行判定
        barcode_decision = barcode_decision_service.evaluate(event.data)
        pkg_id = barcode_decision.pkg_id

        logger.info(f"Scan completed: pkg_id={pkg_id}, location={location}")

        if barcode_decision.decision in {BarcodeDecisionType.INVALID, BarcodeDecisionType.INCOMPLETE}:
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .command(
                    device_role=self.INPUT_ARM,
                    command_type="PICK_AND_PUT",
                    parameters=_scan_ng_command_parameters(pkg_id=pkg_id, location=location),
                )
                .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
                .context(
                    _scan_ng_context(
                        pkg_id=pkg_id,
                        barcodes=barcode_decision.barcodes,
                        location=location,
                        device_code=event.device_code,
                    )
                )
                .failure(
                    domain="DATA",
                    code=barcode_decision.reason_code or "BARCODE_INVALID",
                    message=barcode_decision.reason_message or f"条码格式错误: {pkg_id}",
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
                    parameters=_scan_ng_command_parameters(pkg_id=pkg_id, location=location),
                )
                .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
                .context(
                    _scan_ng_context(
                        pkg_id=pkg_id,
                        barcodes=barcode_decision.barcodes,
                        location=location,
                        device_code=event.device_code,
                    )
                )
                .build()
            )

        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role=self.INPUT_ARM,
                command_type="MEASUREMENT_REEL",
                parameters={
                    "pkg_id": pkg_id,
                    # "source_type": "INPUT_PLATFORM",
                    # "target_type": "PIPELINE_PLATFORM",
                    # "source_loc": location,
                    # "target_loc": "STATION_PIPELINE1_INPUT1",
                },
            )
            .wait(event_type="MEASUREMENT_REEL", timeout_seconds=300)
            .context(
                {
                    "device_code": event.device_code,
                    "barcodes": barcode_decision.barcodes,
                    "location": location,
                    "step_code": SmtClassifierState.WAITING_MEASUREMENT,
                }
            )
            .build()
        )

    # ========== 命令结果处理 ==========
    @on_command("MEASUREMENT_REEL", result="SUCCESS")
    @step(SmtClassifierState.WAITING_MEASUREMENT)
    async def handle_measurement_reel_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """测量成功后推进到流水线传输。

        这里直接消费标准化命令结果，并从 `result.data` 中恢复测量业务字段。
        这样插件不再依赖供应商 payload 顶层/嵌套字段布局。
        """
        current_step = ctx.session.context_json.get("step_code")
        envelope = resolve_normalized_command_envelope(result)
        if envelope is None:
            return _build_payload_invalid_failure(ctx, "测量结果缺少 command_code 或 device_code")

        _, device_code = envelope
        measurement_data = _parse_measurement_result_data(result)
        if measurement_data is None:
            logger.error(f"扫码完成后，进行厚度和直径测量缺少 data: device_code={device_code}, step={current_step}")
            return (
                PluginResultBuilder(ctx)
                .transition("measurement_ng")
                .failure(
                    domain="DATA",
                    code="MEASUREMENT_DATA_MISSING",
                    message="测量成功回调缺少 data 字段",
                )
                .build()
            )

        logger.info(f"扫码完成后，进行厚度和直径测量成功: device_code={device_code}, step={current_step}")

        return (
            PluginResultBuilder(ctx)
            .transition("pick_ok")
            .command(
                device_role=self.CONVEYOR, command_type="MOVE_FORWARD", parameters={"pkg_id": measurement_data.pkg_id}
            )
            .wait(event_type="MOVE_FORWARD", timeout_seconds=300)
            .context(
                {
                    "pkg_id": measurement_data.pkg_id,
                    "reel_diameter": measurement_data.reel_diameter,
                    "reel_thickness": measurement_data.reel_thickness,
                    "step_code": SmtClassifierState.WAITING_CONVEYOR,
                }
            )
            .build()
        )

    @on_command("PICK_AND_PUT", result="SUCCESS")
    async def handle_pick_and_put_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """
        PICK_AND_PUT 成功处理 - 根据当前状态路由

        这里直接消费标准化命令结果，让成功分支与失败分支都建立在统一输入模型上。

        状态路由：
        - WAITING_PICK_PLACE: 进料臂完成 → 流水线传输
        - WAITING_OUTPUT: 出料臂完成 → 结束
        """
        current_step = ctx.session.context_json.get("step_code")
        envelope = resolve_normalized_command_envelope(result)
        if envelope is None:
            return _build_payload_invalid_failure(ctx, "PICK_AND_PUT 成功回调缺少 command_code 或 device_code")

        command_code, device_code = envelope
        logger.info(f"PICK_AND_PUT succeeded: device_code={device_code}, step={current_step}")

        # 路由1: 进料臂完成 → 流水线传输
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
            if ctx.session.context_json.get("pick_place_reason") == "SCAN_NG":
                logger.info(f"NG pick-and-put succeeded: command_code={command_code}")
                return (
                    PluginResultBuilder(ctx)
                    .transition("pick_ng")
                    .context({"step_code": SmtClassifierState.COMPLETED, "ng_handled": True})
                    .complete()
                    .build()
                )

            barcode = ctx.session.context_json.get("barcode", "")
            pick_place_data = _parse_pick_place_result_data(result)
            return (
                PluginResultBuilder(ctx)
                .transition("pick_ok")
                .command(device_role=self.CONVEYOR, command_type="MOVE_FORWARD", parameters={"barcode": barcode})
                .wait(event_type="MOVE_FORWARD", timeout_seconds=300)
                .context(
                    {
                        "reel_diameter": pick_place_data.reel_diameter if pick_place_data else None,
                        "reel_thickness": pick_place_data.reel_thickness if pick_place_data else None,
                        "step_code": SmtClassifierState.WAITING_CONVEYOR,
                    }
                )
                .build()
            )

        # 路由2: 出料臂完成 → 结束
        if current_step == SmtClassifierState.WAITING_OUTPUT:
            logger.info(f"Output succeeded: command_code={result.command_code}")
            return (
                PluginResultBuilder(ctx)
                .transition("output_ok")
                .context({"step_code": SmtClassifierState.COMPLETED})
                .complete()
                .build()
            )

        # 状态不匹配
        logger.error(f"Unexpected step_code for PICK_AND_PUT SUCCESS: {current_step}")
        return _build_state_mismatch_failure(ctx, "PICK_AND_PUT", "SUCCESS", current_step)

    @on_command("PICK_AND_PUT", result="FAILED")
    async def handle_pick_and_put_failed(self, ctx: PluginContext, result: NormalizedCommandResult):
        """
        PICK_AND_PUT 失败处理 - 根据当前状态路由

        这里优先消费标准化结果模型，而不是依赖供应商 payload 的原始字段布局。
        这样 `ERROR` / `FAILED` / 其他 vendor 失败语义可以先归一化，再复用同一条失败处理路径。

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
        if not isinstance(result, NormalizedCommandResult):
            raise TypeError("handle_pick_and_put_failed expects NormalizedCommandResult")

        if resolve_normalized_command_envelope(result) is None:
            return _build_payload_invalid_failure(ctx, "PICK_AND_PUT 失败回调缺少 command_code 或 device_code")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="UNKNOWN",
            default_message="未知错误",
        )
        logger.error(f"PICK_AND_PUT failed: step={current_step}, error_code={error_code}, message={error_msg}")

        is_dimension_error = error_code == "1001"
        is_thickness_error = error_code == "1002"

        # 路由1: 进料臂失败
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
            # 尺寸/厚度检测异常 → NG 缓存位
            if is_dimension_error or is_thickness_error:
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
                    .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
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
            output_error_code = error_code or "OUTPUT_ERROR"
            output_error_msg = error_msg or "出料失败"
            logger.error(f"Output failed: {output_error_code}")
            return (
                PluginResultBuilder(ctx)
                .failure(domain="HARDWARE", code=output_error_code, message=output_error_msg)
                .build()
            )

        # 状态不匹配
        logger.error(f"Unexpected step_code for PICK_AND_PUT FAILED: {current_step}")
        return _build_state_mismatch_failure(ctx, "PICK_AND_PUT", "FAILED", current_step)

    @on_command("MOVE_FORWARD", result="SUCCESS")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.WAITING_OUTPUT)
    async def handle_conveyor_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """
        流水线传输成功 → 料箱分配 → 最终出料

        业务流程（完整版）：
        1. 流水线传输完成
        2. 料箱分配服务（allocation_mock）
        3. 若需要 AGV，调度 AGV 搬运空料箱（TODO）
        4. 下发出料命令到 ARM02

        当前实现：随机生成料箱位置，待集成真实分配服务
        """
        if resolve_normalized_command_envelope(result) is None:
            return _build_payload_invalid_failure(ctx, "MOVE_FORWARD 成功回调缺少 command_code 或 device_code")

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
            .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
            .context({"step_code": SmtClassifierState.WAITING_OUTPUT, "bin_location": bin_location})
            .build()
        )

    @on_command("MOVE_FORWARD", result="FAILED")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.ERROR)
    async def handle_conveyor_failed(self, ctx: PluginContext, result: NormalizedCommandResult):
        """
        流水线传输失败 → 错误
        """
        if resolve_normalized_command_envelope(result) is None:
            return _build_payload_invalid_failure(ctx, "MOVE_FORWARD 失败回调缺少 command_code 或 device_code")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="CONVEYOR_ERROR",
            default_message="流水线传输失败",
        )

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
