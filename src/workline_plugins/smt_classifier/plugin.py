"""SMT 粗分机插件。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ValidationError

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.app.workline.domain.services import (
    SmtFullBoxExchangeRequest,
    SmtRackBinSchedulingDecision,
    smt_rack_bin_scheduling_service,
)
from src.core.logger import logger
from src.workline_runtime.contracts import DeviceErrorCode
from src.workline_runtime.plugin_base import (
    WorklinePlugin,
    build_payload_invalid_block,
    on_command,
    on_event,
    payload_invalid_block_if_missing_envelope,
    resolve_normalized_command_failure,
)
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult  # noqa: TC001
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent
from src.workline_runtime.utils import non_empty_str

from .context import SmtClassifierContext, parse_smt_context
from .contract import (
    INSPECTION_NG_REASONS,
    INSPECTION_SIZE_NG_REASON,
    ScanEventPayload,
    build_measurement_reel_params,
    build_move_forward_params,
    build_output_to_bin_params,
    build_pick_inspection_ng_params,
    build_pick_scan_ng_params,
    classify_smt_command_result,
    normalize_six_in_one_payload,
    parse_six_in_one_payload,
    resolve_smt_business_key,
    resolve_smt_material_identity,
    smt_ng_reason_catalog,
)
from .normalizers import parse_measurement_result_data, parse_pick_place_result_data

if TYPE_CHECKING:
    from src.workline_runtime.plugin_context import PluginContext


def _build_scan_ng_context(*, barcode: str, barcodes: list[str], location: str, device_code: str) -> dict[str, Any]:
    """统一构造扫码 NG 分流上下文。"""

    return SmtClassifierContext(
        barcode=barcode,
        barcodes=barcodes,
        location=location,
        device_code=device_code,
        ng_reason="SCAN_NG",
        pick_place_reason="SCAN_NG",
    ).to_patch()


def _build_manual_hold_context(*, error_code: str, error_message: str) -> dict[str, Any]:
    """统一构造设备失败转人工介入的上下文。"""

    return {
        "manual_hold": True,
        "manual_hold_reason_code": error_code,
        "manual_hold_reason_message": error_message,
    }


def _source_device_role(ctx: Any) -> str | None:
    source_role = ctx.source_device_role
    if source_role is not None and not isinstance(source_role, str):
        raise TypeError("ctx.source_device_role must be set to a string or None")
    return non_empty_str(source_role)


def _unexpected_source_role_block(ctx: Any, command_type: str) -> RuntimeIntent:
    source_role = _source_device_role(ctx) or "UNKNOWN"
    return ctx.next.block(
        scope=BlockScope.MATERIAL,
        reason_code="UNEXPECTED_DEVICE_ROLE",
        message=f"{command_type} 不支持来自设备角色 {source_role} 的回调",
    )


def _error_detail_missing(result: NormalizedCommandResult) -> bool:
    return not isinstance(getattr(result, "error_detail", None), dict) or not result.error_detail


def _manual_hold_intents(ctx: Any, *, error_code: str, error_message: str) -> list[RuntimeIntent]:
    return [
        ctx.next.update_context(_build_manual_hold_context(error_code=error_code, error_message=error_message)),
        ctx.next.block(
            scope=BlockScope.MATERIAL,
            reason_code=error_code,
            message=error_message,
        ),
    ]


def _mark_ng_payload(*, barcode: str, barcodes: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"barcode": barcode}
    if barcodes is not None:
        payload["barcodes"] = barcodes
    payload.update(extra)
    return payload


def _measurement_inspection_ng_reason(measurement_data: Any) -> str | None:
    inspection_result = (non_empty_str(getattr(measurement_data, "inspection_result", None)) or "").upper()
    reason_code = non_empty_str(getattr(measurement_data, "reason_code", None))
    if inspection_result != "NG":
        return None
    if reason_code in INSPECTION_NG_REASONS:
        return reason_code
    return INSPECTION_SIZE_NG_REASON


def _resolve_pkg_id_from_result(result: NormalizedCommandResult) -> str | None:
    """从标准化命令结果中恢复业务包裹标识。"""

    result_data = getattr(result, "data", None)
    if not isinstance(result_data, dict):
        return None

    return result_data.get("PkgID") or result_data.get("pkg_id") or None


def _normalize_full_box_exchange_request(value: Any) -> SmtFullBoxExchangeRequest:
    if isinstance(value, SmtFullBoxExchangeRequest):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("full_box_exchange_request must be a mapping or SmtFullBoxExchangeRequest")

    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("full_box_exchange_request.payload must be a mapping")
    return SmtFullBoxExchangeRequest(
        dispatch_key=str(value.get("dispatch_key") or ""),
        target_code=str(value.get("target_code") or ""),
        payload=dict(payload),
        timeout_seconds=int(value.get("timeout_seconds") or 1800),
        source_system=str(value.get("source_system") or "WMS_RCS"),
    )


def _normalize_bin_scheduling_decision(value: Any) -> SmtRackBinSchedulingDecision:
    if isinstance(value, SmtRackBinSchedulingDecision):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("bin scheduling result must be a mapping or SmtRackBinSchedulingDecision")

    if "full_box_exchange_request" in value:
        return SmtRackBinSchedulingDecision(
            full_box_exchange_request=_normalize_full_box_exchange_request(value["full_box_exchange_request"])
        )

    bin_location = value.get("bin_location") if "bin_location" in value else value
    if not isinstance(bin_location, Mapping):
        raise TypeError("bin scheduling result must include a bin_location mapping")
    return SmtRackBinSchedulingDecision(bin_location=dict(bin_location))


def _full_box_exchange_context(request: SmtFullBoxExchangeRequest) -> dict[str, Any]:
    return {
        "status": "REQUESTED",
        "dispatch_key": request.dispatch_key,
        "target_code": request.target_code,
        "source_system": request.source_system,
    }


class SmtClassifierPlugin(WorklinePlugin):
    """SMT 粗分机插件，handler 只产出 RuntimeIntent。"""

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
        context_model=SmtClassifierContext,
        business_key_resolver=resolve_smt_business_key,
        result_classifier=classify_smt_command_result,
        material_identity_resolver=resolve_smt_material_identity,
        ng_reason_catalog=smt_ng_reason_catalog(),
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

    INPUT_ARM = "INPUT_ARM"
    OUTPUT_ARM = "OUTPUT_ARM"
    CONVEYOR = "CONVEYOR"

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

    @on_event("SCAN_COMPLETED")
    async def handle_scan_completed(self, ctx: PluginContext, inbox: Any) -> RuntimeIntent | list[RuntimeIntent]:
        """扫码完成后按条码判定生成下一步 RuntimeIntent。"""

        payload = getattr(inbox, "payload_json", None) or {}
        try:
            event = ScanEventPayload.model_validate(payload)
        except ValidationError as exc:
            return ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code="PAYLOAD_INVALID",
                message=f"扫码事件 payload 非法: {exc}",
                suggested_action="检查设备回调 payload",
            )

        if event.data is None:
            return ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code="MISSING_SCAN_DATA",
                message="扫码事件缺少 data 字段",
                suggested_action="检查扫码设备 data 字段",
            )

        location = event.data.location
        barcode_decision = barcode_decision_service.evaluate(event.data)
        pkg_id = barcode_decision.six_in_one.PkgID or ""

        logger.info(f"Scan completed: pkg_id={pkg_id}, location={location}")

        is_invalid_scan = barcode_decision.decision in {
            BarcodeDecisionType.INVALID,
            BarcodeDecisionType.INCOMPLETE,
        }
        if is_invalid_scan or barcode_decision.decision == BarcodeDecisionType.NG:
            reason_code = barcode_decision.reason_code if is_invalid_scan else "SCAN_NG"
            reason_message = barcode_decision.reason_message if is_invalid_scan else "扫码判定 NG"
            context_patch = _build_scan_ng_context(
                barcode=pkg_id,
                barcodes=barcode_decision.barcodes,
                location=location,
                device_code=event.device_code,
            )
            if is_invalid_scan:
                context_patch.update(
                    {
                        "scan_ng_reason_code": reason_code,
                        "scan_ng_reason_message": reason_message,
                    }
                )
            return [
                ctx.next.mark_ng(
                    reason_code=reason_code or "",
                    message=reason_message or "",
                    payload=_mark_ng_payload(
                        barcode=pkg_id,
                        barcodes=barcode_decision.barcodes,
                        location=location,
                        device_code=event.device_code,
                    ),
                ),
                ctx.next.update_context(context_patch),
                ctx.next.command(
                    device_role=self.INPUT_ARM,
                    action="PICK_AND_PUT",
                    payload=build_pick_scan_ng_params(barcode=pkg_id, location=location),
                    destination_role=self.INPUT_ARM,
                    timeout_seconds=300,
                ),
            ]

        return [
            ctx.next.update_context(
                SmtClassifierContext(
                    device_code=event.device_code,
                    barcodes=barcode_decision.barcodes,
                    location=location,
                    barcode=pkg_id,
                ).to_patch()
            ),
            ctx.next.command(
                device_role=self.INPUT_ARM,
                action="MEASUREMENT_REEL",
                payload=build_measurement_reel_params(pkg_id),
                destination_role=self.INPUT_ARM,
                timeout_seconds=300,
            ),
        ]

    @on_command("MEASUREMENT_REEL", result="SUCCESS")
    async def handle_measurement_reel_success(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """测量成功后推进到流水线传输。"""

        invalid = payload_invalid_block_if_missing_envelope(result, "测量结果缺少 command_code 或 device_code")
        if invalid is not None:
            return invalid

        raw_measurement_data = getattr(result, "data", None)
        if not isinstance(raw_measurement_data, dict) or not raw_measurement_data:
            return build_payload_invalid_block("测量成功回调缺少 data 字段")

        normalized_measurement_payload = normalize_six_in_one_payload(raw_measurement_data) or {}
        measurement_pkg_id = normalized_measurement_payload.get("PkgID")
        if not isinstance(measurement_pkg_id, str) or not measurement_pkg_id:
            return build_payload_invalid_block("测量成功回调缺少 PkgID/pkg_id")

        measurement_data = parse_measurement_result_data(result)
        if measurement_data is None or measurement_data.PkgID is None:
            return build_payload_invalid_block("测量成功回调 data 非法")

        logger.info(f"测量成功: device_code={result.device_code}")

        inspection_ng_reason = _measurement_inspection_ng_reason(measurement_data)
        if inspection_ng_reason is not None:
            reason_message = non_empty_str(measurement_data.reason_message) or "检测结果 NG"
            return [
                ctx.next.mark_ng(
                    reason_code=inspection_ng_reason,
                    message=reason_message,
                    payload=_mark_ng_payload(
                        barcode=measurement_data.PkgID,
                        device_code=result.device_code,
                        command_code=result.command_code,
                    ),
                ),
                ctx.next.update_context(
                    {
                        "pkg_id": measurement_data.PkgID,
                        "reel_diameter": measurement_data.reel_diameter,
                        "reel_thickness": measurement_data.reel_thickness,
                        "inspection_error": inspection_ng_reason,
                    }
                ),
                ctx.next.command(
                    device_role=self.INPUT_ARM,
                    action="PICK_AND_PUT",
                    payload=build_pick_inspection_ng_params(barcode=measurement_data.PkgID),
                    destination_role=self.INPUT_ARM,
                    timeout_seconds=300,
                ),
            ]

        return [
            ctx.next.update_context(
                {
                    "pkg_id": measurement_data.PkgID,
                    "reel_diameter": measurement_data.reel_diameter,
                    "reel_thickness": measurement_data.reel_thickness,
                }
            ),
            ctx.next.command(
                device_role=self.CONVEYOR,
                action="MOVE_FORWARD",
                payload=build_move_forward_params(measurement_data.PkgID),
                destination_role=self.CONVEYOR,
                timeout_seconds=300,
            ),
        ]

    @on_command("PICK_AND_PUT", result="SUCCESS")
    async def handle_pick_and_put_success(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """按 source_device_role 路由 PICK_AND_PUT 成功结果。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "PICK_AND_PUT 成功回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid

        source_role = _source_device_role(ctx)
        if source_role == self.INPUT_ARM:
            smt_ctx = parse_smt_context(ctx)
            if smt_ctx.pick_place_reason == "SCAN_NG" or smt_ctx.ng_reason == "SCAN_NG" or smt_ctx.inspection_error:
                return [
                    ctx.next.update_context({"ng_handled": True}),
                    ctx.next.complete(),
                ]

            barcode = smt_ctx.barcode or smt_ctx.pkg_id or _resolve_pkg_id_from_result(result) or ""
            pick_place_data = parse_pick_place_result_data(result)
            context_patch: dict[str, Any] = {}
            if pick_place_data is not None:
                if pick_place_data.reel_diameter is not None:
                    context_patch["reel_diameter"] = pick_place_data.reel_diameter
                if pick_place_data.reel_thickness is not None:
                    context_patch["reel_thickness"] = pick_place_data.reel_thickness
            return [
                ctx.next.update_context(context_patch),
                ctx.next.command(
                    device_role=self.CONVEYOR,
                    action="MOVE_FORWARD",
                    payload=build_move_forward_params(barcode),
                    destination_role=self.CONVEYOR,
                    timeout_seconds=300,
                ),
            ]

        if source_role == self.OUTPUT_ARM:
            return ctx.next.complete()

        return _unexpected_source_role_block(ctx, "PICK_AND_PUT SUCCESS")

    @on_command("PICK_AND_PUT", result="FAILED")
    async def handle_pick_and_put_failed(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """按 source_device_role 路由 PICK_AND_PUT 失败结果。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "PICK_AND_PUT 失败回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid
        if _error_detail_missing(result):
            return build_payload_invalid_block("PICK_AND_PUT 失败回调缺少 error_detail 字段")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="UNKNOWN",
            default_message="未知错误",
        )
        requires_manual_hold = error_code in self.MANUAL_HOLD_ERROR_CODES
        source_role = _source_device_role(ctx)

        if source_role == self.INPUT_ARM:
            if requires_manual_hold:
                return _manual_hold_intents(ctx, error_code=error_code, error_message=error_msg)

            return ctx.next.block(
                scope=BlockScope.COMMAND,
                reason_code=error_code,
                message=f"抓取放置失败: {error_msg}",
            )

        if source_role == self.OUTPUT_ARM:
            if requires_manual_hold:
                return _manual_hold_intents(ctx, error_code=error_code, error_message=error_msg)
            return ctx.next.block(
                scope=BlockScope.COMMAND,
                reason_code=error_code,
                message=error_msg,
            )

        return _unexpected_source_role_block(ctx, "PICK_AND_PUT FAILED")

    @on_command("MOVE_FORWARD", result="SUCCESS")
    async def handle_conveyor_success(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """流水线传输成功后分配料箱，并下发出料命令。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "MOVE_FORWARD 成功回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid

        pkg_id = _resolve_pkg_id_from_result(result)
        if not isinstance(pkg_id, str) or not pkg_id:
            return build_payload_invalid_block("MOVE_FORWARD 成功回调缺少 pkg_id")

        smt_ctx = parse_smt_context(ctx)
        reel_diameter = smt_ctx.reel_diameter or ""
        allocation_decision = await self._allocate_bin(ctx, pkg_id)
        if allocation_decision.full_box_exchange_request is not None:
            request = allocation_decision.full_box_exchange_request
            context_patch = SmtClassifierContext(pkg_id=pkg_id).to_patch()
            context_patch["full_box_exchange"] = _full_box_exchange_context(request)
            return [
                ctx.next.update_context(context_patch),
                ctx.next.external_request(
                    dispatch_key=request.dispatch_key,
                    target_code=request.target_code,
                    payload=dict(request.payload),
                    timeout_seconds=request.timeout_seconds,
                    source_system=request.source_system,
                ),
            ]

        bin_location = dict(allocation_decision.bin_location or {})
        logger.info(f"Bin allocated: {bin_location}")

        return [
            ctx.next.update_context(
                SmtClassifierContext(
                    pkg_id=pkg_id,
                    bin_location=bin_location,
                ).to_patch()
            ),
            ctx.next.command(
                device_role=self.OUTPUT_ARM,
                action="PICK_AND_PUT",
                payload=build_output_to_bin_params(
                    pkg_id=pkg_id,
                    reel_diameter=str(reel_diameter),
                    bin_location=bin_location,
                ),
                destination_role=self.OUTPUT_ARM,
                timeout_seconds=300,
            ),
        ]

    @on_command("MOVE_FORWARD", result="FAILED")
    async def handle_conveyor_failed(
        self,
        ctx: PluginContext,
        result: NormalizedCommandResult,
    ) -> RuntimeIntent | list[RuntimeIntent]:
        """流水线传输失败后阻塞设备。"""

        invalid = payload_invalid_block_if_missing_envelope(
            result, "MOVE_FORWARD 失败回调缺少 command_code 或 device_code"
        )
        if invalid is not None:
            return invalid
        if _error_detail_missing(result):
            return build_payload_invalid_block("MOVE_FORWARD 失败回调缺少 error_detail 字段")

        error_code, error_msg = resolve_normalized_command_failure(
            result,
            default_code="CONVEYOR_ERROR",
            default_message="流水线传输失败",
        )

        return ctx.next.block(
            scope=BlockScope.DEVICE,
            reason_code=error_code,
            message=error_msg,
        )

    async def _allocate_bin(self, ctx: PluginContext, barcode: str) -> SmtRackBinSchedulingDecision:
        """料箱分配。"""

        allocator = ctx.services.bin_allocator or smt_rack_bin_scheduling_service
        context = dict(getattr(ctx.session, "context_json", None) or {})
        if hasattr(allocator, "plan_allocation"):
            allocation = allocator.plan_allocation(barcode, context=context)
        else:
            allocation = allocator.allocate(barcode)
        if inspect.isawaitable(allocation):
            allocation = await allocation
        return _normalize_bin_scheduling_decision(allocation)


smt_classifier_plugin = SmtClassifierPlugin()


__all__ = ["SmtClassifierPlugin", "smt_classifier_plugin"]
