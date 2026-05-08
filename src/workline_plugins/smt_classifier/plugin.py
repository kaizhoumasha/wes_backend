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

import inspect
from typing import TYPE_CHECKING, Any, ClassVar

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.core.logger import logger
from src.workline_runtime.contracts import DeviceErrorCode
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    build_payload_invalid_failure,
    build_state_mismatch_failure,
    on_command,
    on_event,
    resolve_normalized_command_envelope,
    resolve_normalized_command_failure,
    step,
)
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult  # noqa: TC001
from src.workline_runtime.types import CommandTargetScope

from .context import SmtClassifierContext, parse_smt_context
from .contract import (
    ScanEventPayload,
    build_default_bin_allocation,
    build_measurement_reel_params,
    build_move_forward_params,
    build_output_to_bin_params,
    build_pick_inspection_ng_params,
    build_pick_scan_ng_params,
    classify_smt_command_result,
    normalize_six_in_one_payload,
    parse_six_in_one_payload,
    resolve_smt_business_key,
)
from .normalizers import parse_measurement_result_data, parse_pick_place_result_data
from .state_machine import SmtClassifierState, SmtClassifierStateMachine

if TYPE_CHECKING:
    from src.workline_runtime.plugin_context import PluginContext


def _build_scan_ng_context(*, barcode: str, barcodes: list[str], location: str, device_code: str) -> dict[str, Any]:
    """统一构造扫码 NG 分流上下文。"""

    return SmtClassifierContext(
        plugin_state=SmtClassifierState.WAITING_PICK_PLACE,
        barcode=barcode,
        barcodes=barcodes,
        location=location,
        device_code=device_code,
        ng_reason="SCAN_NG",
        pick_place_reason="SCAN_NG",
    ).to_patch()


def _build_manual_hold_context(*, current_step: Any, error_code: str, error_message: str) -> dict[str, Any]:
    """统一构造设备失败转人工介入的上下文。"""

    return SmtClassifierContext(
        plugin_state=str(current_step or SmtClassifierState.MANUAL_HOLD),
        manual_hold=True,
        manual_hold_reason_code=error_code,
        manual_hold_reason_message=error_message,
    ).to_patch(plugin_state=SmtClassifierState.MANUAL_HOLD)


def _build_scan_ng_result(
    *,
    builder: PluginResultBuilder,
    barcode: str,
    barcodes: list[str],
    location: str,
    device_code: str,
    scan_ng_reason_code: str | None = None,
    scan_ng_reason_message: str | None = None,
):
    """统一构造扫码 NG 分流结果。"""

    context_patch = _build_scan_ng_context(
        barcode=barcode,
        barcodes=barcodes,
        location=location,
        device_code=device_code,
    )
    if scan_ng_reason_code or scan_ng_reason_message:
        context_patch.update(
            {
                "scan_ng_reason_code": scan_ng_reason_code or "BARCODE_INVALID",
                "scan_ng_reason_message": scan_ng_reason_message or f"条码格式错误: {barcode}",
            }
        )

    reason_code = scan_ng_reason_code or "SCAN_NG"
    reason_message = scan_ng_reason_message or "扫码判定 NG"
    return (
        builder.transition("scan_ng")
        .business_decision(
            reason_code=reason_code,
            message=reason_message,
            business_key=barcode or None,
            evidence={
                "barcode": barcode,
                "barcodes": barcodes,
                "location": location,
                "device_code": device_code,
            },
        )
        .command(
            command_type="PICK_AND_PUT",
            parameters=build_pick_scan_ng_params(barcode=barcode, location=location),
        )
        .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
        .context(context_patch)
        .build()
    )


def _build_manual_hold_result(
    *,
    builder: PluginResultBuilder,
    current_step: Any,
    error_code: str,
    error_message: str,
):
    """统一构造人工介入结果。"""

    return (
        builder.transition("manual_hold")
        .context(
            _build_manual_hold_context(
                current_step=current_step,
                error_code=error_code,
                error_message=error_message,
            )
        )
        .build()
    )


def _resolve_pkg_id_from_result(result: NormalizedCommandResult) -> str | None:
    """从标准化命令结果中恢复业务包裹标识。

    不同设备回调约定不同：扫码臂用 PkgID，流水线用 pkg_id，两者都要支持。
    """

    result_data = getattr(result, "data", None)
    if not isinstance(result_data, dict):
        return None

    return result_data.get("PkgID") or result_data.get("pkg_id") or None


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
    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        required_device_roles=(
            DeviceRoleRequirement("INPUT_ARM", min_count=1, max_count=1),
            DeviceRoleRequirement("OUTPUT_ARM", min_count=1, max_count=1),
            DeviceRoleRequirement("CONVEYOR", min_count=1, max_count=1),
        ),
        state_machine_class=SmtClassifierStateMachine,
        context_model=SmtClassifierContext,
        business_key_resolver=resolve_smt_business_key,
        result_classifier=classify_smt_command_result,
        event_source_roles={
            "SCAN_COMPLETED": "INPUT_ARM",
        },
        command_target_roles={
            "MEASUREMENT_REEL": "INPUT_ARM",
            "MOVE_FORWARD": "CONVEYOR",
            "PICK_AND_PUT": ("INPUT_ARM", "OUTPUT_ARM"),
        },
        supported_events=frozenset({"SCAN_COMPLETED"}),
        supported_commands=frozenset({"MEASUREMENT_REEL", "MOVE_FORWARD", "PICK_AND_PUT"}),
    )

    # ========== 设备角色常量 ==========

    INPUT_ARM = "INPUT_ARM"
    OUTPUT_ARM = "OUTPUT_ARM"
    CONVEYOR = "CONVEYOR"

    # ========== 业务规则 ==========
    MANUAL_HOLD_ERROR_CODES: ClassVar[set[str]] = {
        DeviceErrorCode.SCAN_FAILED.value,
        DeviceErrorCode.PICK_AND_PUT_FAILED.value,
        DeviceErrorCode.BIN_FULL.value,
        DeviceErrorCode.DEVICE_FAULT.value,
        DeviceErrorCode.DEVICE_UNKNOWN_ERROR.value,
    }

    @classmethod
    def parse_six_in_one_payload(cls, payload: dict[str, Any] | None):
        """为 runtime 提供插件自有的 SixInOne 解析入口。"""

        return parse_six_in_one_payload(payload)

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
        pkg_id = barcode_decision.six_in_one.PkgID or ""

        logger.info(f"Scan completed: pkg_id={pkg_id}, location={location}")

        is_invalid_scan = barcode_decision.decision in {
            BarcodeDecisionType.INVALID,
            BarcodeDecisionType.INCOMPLETE,
        }
        if is_invalid_scan or barcode_decision.decision == BarcodeDecisionType.NG:
            return _build_scan_ng_result(
                builder=PluginResultBuilder(ctx),
                barcode=pkg_id,
                barcodes=barcode_decision.barcodes,
                location=location,
                device_code=event.device_code,
                scan_ng_reason_code=barcode_decision.reason_code if is_invalid_scan else None,
                scan_ng_reason_message=barcode_decision.reason_message if is_invalid_scan else None,
            )

        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                command_type="MEASUREMENT_REEL",
                parameters=build_measurement_reel_params(pkg_id),
            )
            .wait(event_type="MEASUREMENT_REEL", timeout_seconds=300)
            .context(
                SmtClassifierContext(
                    plugin_state=SmtClassifierState.WAITING_MEASUREMENT,
                    device_code=event.device_code,
                    barcodes=barcode_decision.barcodes,
                    location=location,
                    barcode=pkg_id,
                ).to_patch()
            )
            .build()
        )

    # ========== 命令结果处理 ==========
    @on_command("MEASUREMENT_REEL", result="SUCCESS")
    @step(SmtClassifierState.WAITING_MEASUREMENT)
    async def handle_measurement_reel_success(self, ctx: PluginContext, result: NormalizedCommandResult):
        """测量成功后推进到流水线传输。

        这里直接消费标准化命令结果，并从 `result.data` 中恢复测量业务字段。
        """
        smt_ctx = parse_smt_context(ctx)
        current_step = smt_ctx.plugin_state
        envelope = resolve_normalized_command_envelope(result)
        if envelope is None:
            return build_payload_invalid_failure(ctx, "测量结果缺少 command_code 或 device_code")

        _, device_code = envelope
        raw_measurement_data = getattr(result, "data", None)
        if not isinstance(raw_measurement_data, dict) or not raw_measurement_data:
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

        normalized_measurement_payload = normalize_six_in_one_payload(raw_measurement_data) or {}
        measurement_pkg_id = normalized_measurement_payload.get("PkgID")
        if not isinstance(measurement_pkg_id, str) or not measurement_pkg_id:
            logger.error(f"测量成功回调缺少 PkgID/pkg_id: device_code={device_code}, step={current_step}")
            return build_payload_invalid_failure(ctx, "测量成功回调缺少 PkgID/pkg_id")

        measurement_data = parse_measurement_result_data(result)
        if measurement_data is None or measurement_data.PkgID is None:
            logger.error(f"测量成功回调 data 非法: device_code={device_code}, step={current_step}")
            return build_payload_invalid_failure(ctx, "测量成功回调 data 非法")

        logger.info(f"扫码完成后，进行厚度和直径测量成功: device_code={device_code}, step={current_step}")

        return (
            PluginResultBuilder(ctx)
            .transition("pick_ok")
            .command(
                command_type="MOVE_FORWARD",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role=self.CONVEYOR,
                parameters=build_move_forward_params(measurement_data.PkgID),
            )
            .wait(event_type="MOVE_FORWARD", timeout_seconds=300)
            .context(
                SmtClassifierContext(
                    plugin_state=SmtClassifierState.WAITING_CONVEYOR,
                    pkg_id=measurement_data.PkgID,
                    reel_diameter=measurement_data.reel_diameter,
                    reel_thickness=measurement_data.reel_thickness,
                ).to_patch()
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
        smt_ctx = parse_smt_context(ctx)
        current_step = smt_ctx.plugin_state
        envelope = resolve_normalized_command_envelope(result)
        if envelope is None:
            return build_payload_invalid_failure(ctx, "PICK_AND_PUT 成功回调缺少 command_code 或 device_code")

        command_code, device_code = envelope
        logger.info(f"PICK_AND_PUT succeeded: device_code={device_code}, step={current_step}")

        # 路由1: 进料臂完成 → 流水线传输
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
            if smt_ctx.pick_place_reason == "SCAN_NG" or smt_ctx.ng_reason == "SCAN_NG":
                logger.info(f"NG pick-and-put succeeded: command_code={command_code}")
                return (
                    PluginResultBuilder(ctx)
                    .transition("pick_ng")
                    .context(
                        SmtClassifierContext(
                            plugin_state=SmtClassifierState.COMPLETED,
                            ng_handled=True,
                        ).to_patch()
                    )
                    .complete()
                    .build()
                )

            barcode = smt_ctx.barcode or ""
            pick_place_data = parse_pick_place_result_data(result)
            return (
                PluginResultBuilder(ctx)
                .transition("pick_ok")
                .command(
                    command_type="MOVE_FORWARD",
                    target_scope=CommandTargetScope.DOWNSTREAM,
                    device_role=self.CONVEYOR,
                    parameters=build_move_forward_params(barcode),
                )
                .wait(event_type="MOVE_FORWARD", timeout_seconds=300)
                .context(
                    SmtClassifierContext(
                        plugin_state=SmtClassifierState.WAITING_CONVEYOR,
                        reel_diameter=pick_place_data.reel_diameter if pick_place_data else None,
                        reel_thickness=pick_place_data.reel_thickness if pick_place_data else None,
                    ).to_patch()
                )
                .build()
            )

        # 路由2: 出料臂完成 → 结束
        if current_step == SmtClassifierState.WAITING_OUTPUT:
            logger.info(f"Output succeeded: command_code={result.command_code}")
            return (
                PluginResultBuilder(ctx)
                .transition("output_ok")
                .context(SmtClassifierContext(plugin_state=SmtClassifierState.COMPLETED).to_patch())
                .complete()
                .build()
            )

        # 状态不匹配
        logger.error(f"Unexpected plugin_state for PICK_AND_PUT SUCCESS: {current_step}")
        return build_state_mismatch_failure(ctx, "PICK_AND_PUT", "SUCCESS", current_step)

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
        - INSPECTION_SIZE_NG: 料盘尺寸检测异常
        - INSPECTION_THICKNESS_NG: 料盘厚度检测异常
        - SCAN_FAILED: 扫码执行失败
        - PICK_AND_PUT_FAILED: 机械臂搬运失败
        - BIN_FULL: 料箱已满
        """
        smt_ctx = parse_smt_context(ctx)
        current_step = smt_ctx.plugin_state
        if resolve_normalized_command_envelope(result) is None:
            return build_payload_invalid_failure(ctx, "PICK_AND_PUT 失败回调缺少 command_code 或 device_code")
        if not isinstance(getattr(result, "error_detail", None), dict) or not getattr(result, "error_detail", None):
            return build_payload_invalid_failure(ctx, "PICK_AND_PUT 失败回调缺少 error_detail 字段")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="UNKNOWN",
            default_message="未知错误",
        )
        logger.error(f"PICK_AND_PUT failed: step={current_step}, error_code={error_code}, message={error_msg}")

        is_dimension_error = error_code == DeviceErrorCode.INSPECTION_SIZE_NG.value
        is_thickness_error = error_code == DeviceErrorCode.INSPECTION_THICKNESS_NG.value
        requires_manual_hold = error_code in self.MANUAL_HOLD_ERROR_CODES

        # 路由1: 进料臂失败
        if current_step == SmtClassifierState.WAITING_PICK_PLACE:
            # 尺寸/厚度检测异常 → NG 缓存位
            if is_dimension_error or is_thickness_error:
                barcode = smt_ctx.barcode or ""
                return (
                    PluginResultBuilder(ctx)
                    .transition("inspection_ng")
                    .business_decision(
                        reason_code=error_code,
                        message=error_msg,
                        business_key=barcode or None,
                        evidence={
                            "barcode": barcode,
                            "device_code": result.device_code,
                            "command_code": result.command_code,
                        },
                    )
                    .command(
                        command_type="PICK_AND_PUT",
                        parameters=build_pick_inspection_ng_params(barcode=barcode),
                    )
                    .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
                    .context(
                        SmtClassifierContext(
                            plugin_state=SmtClassifierState.WAITING_PICK_PLACE,
                            inspection_error=error_code,
                        ).to_patch()
                    )
                    .build()
                )

            if requires_manual_hold:
                return _build_manual_hold_result(
                    builder=PluginResultBuilder(ctx),
                    current_step=current_step,
                    error_code=error_code,
                    error_message=error_msg,
                )

            # 其他错误
            return (
                PluginResultBuilder(ctx)
                .failure(
                    domain="HARDWARE",
                    code=error_code,
                    message=f"抓取放置失败: {error_msg}",
                )
                .build()
            )

        # 路由2: 出料臂失败
        if current_step == SmtClassifierState.WAITING_OUTPUT:
            output_error_code = error_code or "OUTPUT_ERROR"
            output_error_msg = error_msg or "出料失败"
            logger.error(f"Output failed: {output_error_code}")
            if output_error_code in self.MANUAL_HOLD_ERROR_CODES:
                return _build_manual_hold_result(
                    builder=PluginResultBuilder(ctx),
                    current_step=current_step,
                    error_code=output_error_code,
                    error_message=output_error_msg,
                )
            return (
                PluginResultBuilder(ctx)
                .failure(domain="HARDWARE", code=output_error_code, message=output_error_msg)
                .build()
            )

        # 状态不匹配
        logger.error(f"Unexpected plugin_state for PICK_AND_PUT FAILED: {current_step}")
        return build_state_mismatch_failure(ctx, "PICK_AND_PUT", "FAILED", current_step)

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
            return build_payload_invalid_failure(ctx, "MOVE_FORWARD 成功回调缺少 command_code 或 device_code")

        logger.info("Conveyor move succeeded, starting bin allocation")

        pkg_id = _resolve_pkg_id_from_result(result)
        if not isinstance(pkg_id, str) or not pkg_id:
            return build_payload_invalid_failure(ctx, "MOVE_FORWARD 成功回调缺少 pkg_id")

        smt_ctx = parse_smt_context(ctx)
        reel_diameter = smt_ctx.reel_diameter or ""

        # 料箱分配（TODO: 暂时使用随机料箱，后续集成真实分配服务）
        bin_location = await self._allocate_bin(ctx, pkg_id)
        logger.info(f"Bin allocated: {bin_location}")

        return (
            PluginResultBuilder(ctx)
            .transition("conveyor_ok")
            .command(
                command_type="PICK_AND_PUT",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role=self.OUTPUT_ARM,
                parameters=build_output_to_bin_params(
                    pkg_id=pkg_id,
                    reel_diameter=str(reel_diameter),
                    bin_location=bin_location,
                ),
            )
            .wait(event_type="PICK_AND_PUT", timeout_seconds=300)
            .context(
                SmtClassifierContext(
                    plugin_state=SmtClassifierState.WAITING_OUTPUT,
                    pkg_id=pkg_id,
                    bin_location=bin_location,
                ).to_patch()
            )
            .build()
        )

    @on_command("MOVE_FORWARD", result="FAILED")
    @step(SmtClassifierState.WAITING_CONVEYOR, SmtClassifierState.ERROR)
    async def handle_conveyor_failed(self, ctx: PluginContext, result: NormalizedCommandResult):
        """
        流水线传输失败 → 错误
        """
        if resolve_normalized_command_envelope(result) is None:
            return build_payload_invalid_failure(ctx, "MOVE_FORWARD 失败回调缺少 command_code 或 device_code")
        if not isinstance(getattr(result, "error_detail", None), dict) or not getattr(result, "error_detail", None):
            return build_payload_invalid_failure(ctx, "MOVE_FORWARD 失败回调缺少 error_detail 字段")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="CONVEYOR_ERROR",
            default_message="流水线传输失败",
        )

        logger.error(f"Conveyor move failed: {error_code}")

        return PluginResultBuilder(ctx).failure(domain="HARDWARE", code=error_code, message=error_msg).build()

    # ========== 辅助方法 ==========

    async def _allocate_bin(self, ctx: PluginContext, barcode: str) -> dict:
        """料箱分配。

        内部领域计算走 ctx.services；未注入服务时使用确定性 fallback，避免插件直接访问
        HTTP / Repository / SQL，也避免随机结果影响 replay/debug。
        """

        allocator = self._resolve_bin_allocator(ctx)
        if allocator is not None:
            allocation = allocator.allocate(barcode)
            if inspect.isawaitable(allocation):
                allocation = await allocation
            return dict(allocation)

        fallback = build_default_bin_allocation(barcode)
        logger.info(f"Using fallback bin allocation: barcode={barcode}, bin_id={fallback['bin_id']}")
        return fallback

    @staticmethod
    def _resolve_bin_allocator(ctx: PluginContext) -> Any | None:
        return ctx.services.bin_allocator


# ==================== 导出插件实例 ====================

smt_classifier_plugin = SmtClassifierPlugin()


__all__ = ["SmtClassifierPlugin", "smt_classifier_plugin"]
