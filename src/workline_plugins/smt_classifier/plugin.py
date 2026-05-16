"""SMT 粗分机插件。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
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
    WMS_RACK_ARRIVED,
    WMS_RACK_EXCHANGE_FAILED,
    WMS_RACK_EXCHANGE_PROGRESS,
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

_WMS_RCS_CALLBACK_REQUIRED_FIELDS = (
    "dispatch_key",
    "source_system",
    "source_event_id",
    "source_version",
    "occurred_at",
    "request_id",
    "timestamp",
    "signature",
)


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

    kind = value.get("kind")
    if kind == "BLOCKED":
        reason_code = non_empty_str(value.get("reason_code"))
        if reason_code is None:
            raise TypeError("BLOCKED bin scheduling result must include reason_code")
        return SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code=reason_code,
            message=non_empty_str(value.get("message")),
        )
    if kind == "RACK_EXCHANGE_REQUIRED" or "external_request" in value:
        if "external_request" not in value:
            raise TypeError("RACK_EXCHANGE_REQUIRED bin scheduling result must include external_request")
        return SmtRackBinSchedulingDecision(
            external_request=_normalize_full_box_exchange_request(value["external_request"])
        )
    if kind is not None and kind != "ALLOCATED":
        raise TypeError(f"unsupported bin scheduling decision kind: {kind}")

    bin_location = value.get("bin_location") if kind == "ALLOCATED" or "bin_location" in value else value
    if not isinstance(bin_location, Mapping):
        raise TypeError("bin scheduling result must include a bin_location mapping")
    return SmtRackBinSchedulingDecision(bin_location=dict(bin_location))


def _is_mock_value(value: Any) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


def _merge_runtime_trace_context(ctx: PluginContext, context: dict[str, Any]) -> None:
    if "trace_id" not in context:
        trace_id = non_empty_str(getattr(ctx, "trace_id", None)) or non_empty_str(
            getattr(getattr(ctx, "session", None), "trace_id", None)
        )
        if trace_id is not None:
            context["trace_id"] = trace_id

    if "session_id" not in context:
        session_id = getattr(getattr(ctx, "session", None), "id", None)
        if session_id is not None and not _is_mock_value(session_id):
            context["session_id"] = session_id


def _merge_runtime_config_context(ctx: PluginContext, context: dict[str, Any]) -> None:
    merged_config: dict[str, Any] = {}
    workline_config = getattr(getattr(ctx, "workline", None), "config", None)
    if isinstance(workline_config, Mapping):
        merged_config.update(workline_config)

    ctx_config = getattr(ctx, "config", None)
    if isinstance(ctx_config, Mapping):
        merged_config.update(ctx_config)

    existing_config = context.get("config")
    if isinstance(existing_config, Mapping):
        merged_config.update(existing_config)

    if merged_config:
        context["config"] = merged_config


def _full_box_exchange_context(request: SmtFullBoxExchangeRequest) -> dict[str, Any]:
    return {
        "status": "REQUESTED",
        "dispatch_key": request.dispatch_key,
        "target_code": request.target_code,
        "source_system": request.source_system,
    }


def _rack_exchange_context(
    request: SmtFullBoxExchangeRequest,
    *,
    pkg_id: str,
    reason_code: str | None,
) -> dict[str, Any]:
    actions = request.payload.get("actions")
    return {
        "status": "REQUESTED",
        "dispatch_key": request.dispatch_key,
        "target_code": request.target_code,
        "source_system": request.source_system,
        "reason_code": reason_code or non_empty_str(request.payload.get("reason_code")),
        "requested_actions": list(actions)
        if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes))
        else [],
        "pkg_id": pkg_id,
    }


def _is_rack_exchange_and_supply_request(request: SmtFullBoxExchangeRequest) -> bool:
    return request.payload.get("request_type") == "SMT_RACK_EXCHANGE_AND_SUPPLY"


def _validated_bin_location_or_block(
    bin_location: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, RuntimeIntent | None]:
    location = dict(bin_location or {})
    for required_field in ("bin_id", "bin_type", "bin_cell_location"):
        if non_empty_str(location.get(required_field)) is None:
            return None, build_payload_invalid_block(f"料箱调度结果缺少 {required_field}")
    return location, None


def _validate_wms_rcs_callback_envelope(payload: Mapping[str, Any]) -> str | None:
    """校验 WMS/RCS 回调进入插件处理前的最小来源包络。"""

    for field_name in _WMS_RCS_CALLBACK_REQUIRED_FIELDS:
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"EXTERNAL_HTTP 回调缺少 {field_name}"

    source_system = non_empty_str(payload.get("source_system"))
    if source_system not in {"WMS", "RCS"}:
        return "EXTERNAL_HTTP 回调 source_system 必须是 WMS 或 RCS"
    return None


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
                    six_in_one=barcode_decision.six_in_one.model_dump(
                        include=set(barcode_decision.six_in_one.BUSINESS_FIELD_NAMES)
                    ),
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
        if allocation_decision.kind == "BLOCKED":
            return ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code=allocation_decision.reason_code or "BIN_SCHEDULING_BLOCKED",
                message=allocation_decision.message or "SMT 料箱调度阻断",
            )

        if allocation_decision.full_box_exchange_request is not None:
            request = allocation_decision.full_box_exchange_request
            context_patch = SmtClassifierContext(pkg_id=pkg_id).to_patch()
            if _is_rack_exchange_and_supply_request(request):
                context_patch["rack_exchange"] = _rack_exchange_context(
                    request,
                    pkg_id=pkg_id,
                    reason_code=allocation_decision.reason_code,
                )
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

        bin_location, invalid = _validated_bin_location_or_block(allocation_decision.bin_location)
        if invalid is not None or bin_location is None:
            return invalid or build_payload_invalid_block("料箱调度结果非法")

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

    async def on_external_http(self, ctx: PluginContext, inbox: Any) -> list[RuntimeIntent]:
        """处理 WMS/RCS 换架回调，并在空架到位后恢复出料。"""

        payload = getattr(inbox, "payload_json", None) or {}
        if not isinstance(payload, Mapping):
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调 payload 非法")]

        callback_type = non_empty_str(payload.get("callback_type"))
        if callback_type not in {
            WMS_RACK_EXCHANGE_PROGRESS,
            WMS_RACK_ARRIVED,
            WMS_RACK_EXCHANGE_FAILED,
        }:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调 callback_type 不支持")]

        envelope_error = _validate_wms_rcs_callback_envelope(payload)
        if envelope_error is not None:
            return [build_payload_invalid_block(envelope_error)]

        rack_exchange = self._rack_exchange_from_context(ctx)
        if rack_exchange is None:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调缺少待处理 rack_exchange 上下文")]

        expected_dispatch_key = non_empty_str(rack_exchange.get("dispatch_key"))
        callback_dispatch_key = non_empty_str(payload.get("dispatch_key"))
        if expected_dispatch_key is None or callback_dispatch_key is None:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调缺少 dispatch_key")]
        if callback_dispatch_key != expected_dispatch_key:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="DISPATCH_KEY_MISMATCH",
                    message="EXTERNAL_HTTP 回调 dispatch_key 与当前换架请求不匹配",
                )
            ]

        if not self._is_pending_rack_exchange_callback(ctx, rack_exchange):
            return [ctx.next.update_context({"rack_exchange": rack_exchange})]

        return await self._handle_rack_exchange_callback(ctx, payload, callback_type, rack_exchange)

    def _is_pending_rack_exchange_callback(self, ctx: PluginContext, rack_exchange: Mapping[str, Any]) -> bool:
        if getattr(ctx.session, "current_wait_type", None) != "EXTERNAL_HTTP":
            return False
        return non_empty_str(rack_exchange.get("status")) in {"REQUESTED", "IN_PROGRESS"}

    async def _handle_rack_exchange_callback(
        self,
        ctx: PluginContext,
        payload: Mapping[str, Any],
        callback_type: str,
        rack_exchange: dict[str, Any],
    ) -> list[RuntimeIntent]:
        """处理已通过 dispatch_key 校验的换架回调。"""

        if callback_type == WMS_RACK_EXCHANGE_PROGRESS:
            status = non_empty_str(payload.get("status")) or "IN_PROGRESS"
            return [ctx.next.update_context({"rack_exchange": {**rack_exchange, "status": status}})]

        if callback_type == WMS_RACK_EXCHANGE_FAILED:
            reason_code = non_empty_str(payload.get("reason_code")) or "WMS_RACK_EXCHANGE_FAILED"
            message = (
                non_empty_str(payload.get("reason_message"))
                or non_empty_str(payload.get("message"))
                or "WMS/RCS 换架失败"
            )
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=reason_code,
                    message=message,
                )
            ]

        active_bin_rack = payload.get("active_bin_rack")
        if not isinstance(active_bin_rack, Mapping):
            return [build_payload_invalid_block("WMS_RACK_ARRIVED 回调缺少 active_bin_rack")]

        session_context = dict(getattr(ctx.session, "context_json", None) or {})
        pkg_id = non_empty_str(rack_exchange.get("pkg_id")) or non_empty_str(session_context.get("pkg_id"))
        if pkg_id is None:
            return [build_payload_invalid_block("WMS_RACK_ARRIVED 回调缺少 pkg_id")]

        allocation_context = {**session_context, "active_bin_rack": dict(active_bin_rack)}
        allocation_decision = await self._allocate_bin(ctx, pkg_id, allocation_context=allocation_context)
        if allocation_decision.kind == "BLOCKED":
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=allocation_decision.reason_code or "BIN_SCHEDULING_BLOCKED",
                    message=allocation_decision.message or "SMT 料箱调度阻断",
                )
            ]
        if allocation_decision.full_box_exchange_request is not None:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=allocation_decision.reason_code or "RACK_EXCHANGE_STILL_REQUIRED",
                    message=allocation_decision.message or "空架到位后仍无可用料箱格",
                )
            ]

        bin_location, invalid = _validated_bin_location_or_block(allocation_decision.bin_location)
        if invalid is not None or bin_location is None:
            return [invalid or build_payload_invalid_block("料箱调度结果非法")]

        reel_diameter = parse_smt_context(ctx).reel_diameter or ""
        return [
            ctx.next.update_context(
                {
                    "active_bin_rack": dict(active_bin_rack),
                    "rack_exchange": {**rack_exchange, "status": "ARRIVED"},
                    "pkg_id": pkg_id,
                    "bin_location": bin_location,
                }
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

    def _rack_exchange_from_context(self, ctx: PluginContext) -> dict[str, Any] | None:
        context = getattr(ctx.session, "context_json", None)
        if not isinstance(context, Mapping):
            return None
        rack_exchange = context.get("rack_exchange")
        return dict(rack_exchange) if isinstance(rack_exchange, Mapping) else None

    async def _allocate_bin(
        self,
        ctx: PluginContext,
        barcode: str,
        *,
        allocation_context: Mapping[str, Any] | None = None,
    ) -> SmtRackBinSchedulingDecision:
        """料箱分配。"""

        allocator = ctx.services.bin_allocator or smt_rack_bin_scheduling_service
        context = (
            dict(allocation_context)
            if allocation_context is not None
            else dict(getattr(ctx.session, "context_json", None) or {})
        )
        _merge_runtime_trace_context(ctx, context)
        _merge_runtime_config_context(ctx, context)
        if hasattr(allocator, "plan_allocation"):
            allocation = allocator.plan_allocation(barcode, context=context)
        else:
            allocation = allocator.allocate(barcode)
        if inspect.isawaitable(allocation):
            allocation = await allocation
        return _normalize_bin_scheduling_decision(allocation)


smt_classifier_plugin = SmtClassifierPlugin()


__all__ = ["SmtClassifierPlugin", "smt_classifier_plugin"]
