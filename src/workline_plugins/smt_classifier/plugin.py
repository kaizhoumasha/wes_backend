"""SMT 粗分机插件。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import ValidationError

from src.app.resource.services import (
    SmtRackBinSchedulingDecision,
    SmtRackBinSchedulingDecisionKind,
    SmtRackOperationRequest,
    smt_rack_bin_scheduling_service,
)
from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
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
_RACK_OPERATION_TASK_TYPES_BY_SEQUENCE = {
    ("REPLACE_CLASSIFIER_WORK_RACK", 1): "MOVE_RACK",
    ("REPLACE_CLASSIFIER_WORK_RACK", 2): "ALLOCATE_AND_MOVE_RACK",
}
_RACK_EXCHANGE_PROGRESS_CALLBACK_TYPES = frozenset({WMS_RACK_EXCHANGE_PROGRESS, "RCS_RACK_EXCHANGE_PROGRESS"})
_RACK_ARRIVED_CALLBACK_TYPES = frozenset({WMS_RACK_ARRIVED, "RCS_RACK_ARRIVED"})
_RACK_EXCHANGE_FAILED_CALLBACK_TYPES = frozenset({WMS_RACK_EXCHANGE_FAILED, "RCS_RACK_EXCHANGE_FAILED"})
_RACK_OPERATION_CALLBACK_TYPES = (
    _RACK_EXCHANGE_PROGRESS_CALLBACK_TYPES | _RACK_ARRIVED_CALLBACK_TYPES | _RACK_EXCHANGE_FAILED_CALLBACK_TYPES
)
_RACK_OPERATION_WAITING_STATUSES = frozenset(
    {
        "REQUESTED",
        "PENDING",
        "IN_PROGRESS",
        "ACCEPTED",
        "QUEUED",
        "PHYSICAL_COMPLETED",
        "RESOURCE_PROJECTED",
        "WMS_CONFIRMED",
    }
)
_RACK_OPERATION_PRE_PROJECTION_STATUSES = frozenset({"RECONCILING"})
DEFAULT_CLASSIFIER_WORK_POSITION_CODE = "SINGLE_LAYER_A"
DEFAULT_CLASSIFIER_NEW_RACK_KIND = "SINGLE_LAYER"
DEFAULT_CLASSIFIER_MOVE_OUT_TARGET_POSITION_ROLE = "SMT_EMPTY_RACK_AREA"
DEFAULT_CLASSIFIER_WORK_POSITION_ROLE = "SMT_CLASSIFIER_SINGLE_RACK_WORK"


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

    data = cast("dict[str, Any]", result_data)
    return cast("str | None", data.get("PkgID") or data.get("pkg_id") or None)


def _normalize_rack_operation_request_fields(
    value: Any,
    *,
    field_name: str,
    type_name: str,
) -> tuple[str, str, dict[str, Any], int, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping or {type_name}")

    request = cast("Mapping[str, Any]", value)
    payload = request.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError(f"{field_name}.payload must be a mapping")

    return (
        str(request.get("operation_key") or ""),
        str(request.get("target_code") or ""),
        dict(cast("Mapping[str, Any]", payload)),
        int(request.get("timeout_seconds") or 1800),
        str(request.get("source_system") or "WMS_RCS"),
    )


def _normalize_rack_operation_request(value: Any) -> SmtRackOperationRequest:
    if isinstance(value, SmtRackOperationRequest):
        return value
    operation_key, target_code, payload, timeout_seconds, source_system = _normalize_rack_operation_request_fields(
        value,
        field_name="rack_operation_request",
        type_name="SmtRackOperationRequest",
    )
    return SmtRackOperationRequest(
        operation_key=operation_key,
        target_code=target_code,
        payload=payload,
        timeout_seconds=timeout_seconds,
        source_system=source_system,
    )


def _normalize_scheduling_decision_kind(
    value: Any, default: SmtRackBinSchedulingDecisionKind
) -> SmtRackBinSchedulingDecisionKind:
    kind = str(value or default)
    if kind not in {"ALLOCATED", "RACK_OPERATION_REQUIRED", "BLOCKED"}:
        raise TypeError(f"unsupported bin scheduling decision kind: {kind}")
    return cast("SmtRackBinSchedulingDecisionKind", kind)


def _normalize_bin_scheduling_decision(value: Any) -> SmtRackBinSchedulingDecision:
    if isinstance(value, SmtRackBinSchedulingDecision):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("bin scheduling result must be a mapping or SmtRackBinSchedulingDecision")

    decision = cast("Mapping[str, Any]", value)
    if "rack_operation_request" in decision:
        return SmtRackBinSchedulingDecision(
            kind=_normalize_scheduling_decision_kind(decision.get("kind"), "RACK_OPERATION_REQUIRED"),
            rack_operation_request=_normalize_rack_operation_request(decision["rack_operation_request"]),
            reason_code=non_empty_str(decision.get("reason_code")),
            message=non_empty_str(decision.get("message")),
        )

    kind = decision.get("kind")
    if kind == "BLOCKED":
        reason_code = non_empty_str(decision.get("reason_code"))
        if reason_code is None:
            raise TypeError("BLOCKED bin scheduling result must include reason_code")
        return SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code=reason_code,
            message=non_empty_str(decision.get("message")),
        )
    if kind == "RACK_OPERATION_REQUIRED":
        raise TypeError("RACK_OPERATION_REQUIRED bin scheduling result must include rack_operation_request")
    if kind is not None and kind != "ALLOCATED":
        raise TypeError(f"unsupported bin scheduling decision kind: {kind}")

    bin_location = decision.get("bin_location") if kind == "ALLOCATED" or "bin_location" in decision else decision
    if not isinstance(bin_location, Mapping):
        raise TypeError("bin scheduling result must include a bin_location mapping")
    return SmtRackBinSchedulingDecision(bin_location=dict(cast("Mapping[str, Any]", bin_location)))


def _is_mock_value(value: Any) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


def _merge_mapping(target: dict[str, Any], source: Any) -> None:
    if isinstance(source, Mapping):
        target.update(cast("Mapping[str, Any]", source))


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
    _merge_mapping(merged_config, getattr(getattr(ctx, "workline", None), "config", None))
    _merge_mapping(merged_config, getattr(ctx, "config", None))
    _merge_mapping(merged_config, context.get("config"))

    if merged_config:
        context["config"] = merged_config


def _rack_operation_context(
    request: SmtRackOperationRequest,
    *,
    pkg_id: str,
    reason_code: str | None,
    resume_source_device_code: str | None = None,
    resume_source_device_role: str | None = None,
) -> dict[str, Any]:
    actions = request.payload.get("actions")
    requested_actions: list[Any] = []
    if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes)):
        requested_actions = list(cast("Sequence[Any]", actions))

    context: dict[str, Any] = {
        "status": "REQUESTED",
        "operation_key": request.operation_key,
        "target_code": request.target_code,
        "source_system": request.source_system,
        "reason_code": reason_code or non_empty_str(request.payload.get("reason_code")),
        "requested_actions": requested_actions,
        "pkg_id": pkg_id,
        "resume_callback_type": "WMS_RACK_ARRIVED",
    }
    work_position_code = (
        non_empty_str(request.payload.get("work_position_code"))
        or non_empty_str(request.payload.get("target_position_code"))
        or non_empty_str(request.payload.get("position_code"))
    )
    if work_position_code is not None:
        context["work_position_code"] = work_position_code
        context["target_position_code"] = work_position_code
    if resume_source_device_code is not None:
        context["resume_source_device_code"] = resume_source_device_code
    if resume_source_device_role is not None:
        context["resume_source_device_role"] = resume_source_device_role
    return context


def _rack_operation_payload(
    ctx: Any,
    request: SmtRackOperationRequest,
    *,
    reason_code: str | None = None,
) -> dict[str, Any]:
    payload = dict(request.payload)
    payload["operation_type"] = "REPLACE_CLASSIFIER_WORK_RACK"
    payload["operation_key"] = request.operation_key
    work_position_code = (
        non_empty_str(payload.get("work_position_code"))
        or non_empty_str(payload.get("target_position_code"))
        or non_empty_str(payload.get("position_code"))
        or DEFAULT_CLASSIFIER_WORK_POSITION_CODE
    )
    payload["work_position_code"] = work_position_code
    payload["new_rack_kind"] = (
        non_empty_str(payload.get("new_rack_kind"))
        or non_empty_str(payload.get("rack_kind"))
        or DEFAULT_CLASSIFIER_NEW_RACK_KIND
    )
    payload["move_out_target_position_role"] = (
        non_empty_str(payload.get("move_out_target_position_role"))
        or non_empty_str(payload.get("target_position_role"))
        or DEFAULT_CLASSIFIER_MOVE_OUT_TARGET_POSITION_ROLE
    )
    if "rack_tasks" not in payload and "task_specs" not in payload:
        payload["rack_tasks"] = _rack_operation_task_specs(payload)
    payload["target_code"] = request.target_code
    resolved_reason_code = non_empty_str(payload.get("reason_code")) or reason_code
    if resolved_reason_code is not None:
        payload["reason_code"] = resolved_reason_code
    trace_id = non_empty_str(payload.get("trace_id")) or non_empty_str(getattr(ctx, "trace_id", None))
    if trace_id is not None:
        payload["trace_id"] = trace_id
    return payload


def _rack_operation_task_specs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    work_position_code = non_empty_str(payload.get("work_position_code")) or DEFAULT_CLASSIFIER_WORK_POSITION_CODE
    new_rack_kind = non_empty_str(payload.get("new_rack_kind")) or DEFAULT_CLASSIFIER_NEW_RACK_KIND
    move_out_target_position_role = (
        non_empty_str(payload.get("move_out_target_position_role")) or DEFAULT_CLASSIFIER_MOVE_OUT_TARGET_POSITION_ROLE
    )
    active_rack = payload.get("current_rack_snapshot")
    active_rack_map = active_rack if isinstance(active_rack, Mapping) else {}
    active_rack_code = (
        non_empty_str(payload.get("single_layer_rack_code"))
        or non_empty_str(payload.get("single_layer_rack_id"))
        or non_empty_str(active_rack_map.get("rack_code"))
        or non_empty_str(active_rack_map.get("rack_id"))
    )
    active_rack_kind = non_empty_str(active_rack_map.get("rack_kind"))

    specs: list[dict[str, Any]] = []
    if active_rack_code is not None:
        move_out_spec = {
            "sequence_no": 1,
            "task_type": "MOVE_RACK",
            "rack_code": active_rack_code,
            "source_position_code": work_position_code,
            "target_position_role": move_out_target_position_role,
        }
        if active_rack_kind is not None:
            move_out_spec["rack_kind"] = active_rack_kind
        specs.append(move_out_spec)
    specs.append(
        {
            "sequence_no": 2,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_kind": new_rack_kind,
            "target_position_code": work_position_code,
            "target_position_role": DEFAULT_CLASSIFIER_WORK_POSITION_ROLE,
        }
    )
    return specs


def _is_rack_operation_request(request: SmtRackOperationRequest) -> bool:
    return request.payload.get("request_type") == "SMT_RACK_OPERATION"


def _validated_bin_location_or_block(
    bin_location: Mapping[str, Any] | None,
    *,
    reel_diameter: Any = None,
) -> tuple[dict[str, Any] | None, RuntimeIntent | None]:
    location = dict(bin_location or {})
    for required_field in (
        "rack_id",
        "rack_slot_code",
        "rack_slot_location_code",
        "bin_id",
        "bin_type",
        "bin_cell_location",
    ):
        if non_empty_str(location.get(required_field)) is None:
            return None, build_payload_invalid_block(f"料箱调度结果缺少 {required_field}")

    normalized = smt_rack_bin_scheduling_service.normalize_bin_location(location)
    if normalized is None:
        return None, build_payload_invalid_block("料箱调度结果不符合 SMT 6/3 格箱料格规则")
    if non_empty_str(
        reel_diameter
    ) is not None and not smt_rack_bin_scheduling_service.is_bin_location_compatible_with_reel(
        normalized, reel_diameter
    ):
        return None, build_payload_invalid_block("料箱调度结果与料盘尺寸不匹配")
    return normalized, None


def _material_identity_key_from_context(context: Mapping[str, Any], *, pkg_code: str) -> str:
    six_in_one = context.get("six_in_one")
    normalized = (
        normalize_six_in_one_payload(dict(cast("Mapping[str, Any]", six_in_one)))
        if isinstance(six_in_one, Mapping)
        else None
    )
    if normalized:
        hhp_n = non_empty_str(normalized.get("HHPN"))
        mfr_pn = non_empty_str(normalized.get("MfrPN"))
        date_code = non_empty_str(normalized.get("DateCode"))
        lot_code = non_empty_str(normalized.get("LotCode"))
        if hhp_n or date_code or lot_code:
            return f"MAT:{hhp_n or ''}:{mfr_pn or ''}:{date_code or ''}:{lot_code or ''}"
    return f"PKG:{pkg_code}"


def _build_bin_cell_reservation_intent(
    ctx: PluginContext,
    *,
    pkg_code: str,
    bin_location: Mapping[str, Any],
    source_event_id: str | None,
) -> RuntimeIntent:
    bin_code = str(bin_location["bin_id"])
    bin_cell_index = str(bin_location["bin_cell_index"])
    payload = {
        "pkg_code": pkg_code,
        "bin_code": bin_code,
        "bin_cell_code": non_empty_str(bin_location.get("bin_cell_location")),
        "bin_cell_index": bin_cell_index,
        "material_identity_key": _material_identity_key_from_context(
            dict(cast("Mapping[str, Any]", getattr(ctx.session, "context_json", None) or {})),
            pkg_code=pkg_code,
        ),
        "source_event_id": source_event_id,
    }
    session_id = getattr(getattr(ctx, "session", None), "id", "session")
    return ctx.next.resource_reservation(
        operation="CLAIM_BIN_CELL",
        payload=payload,
        idempotency_key=f"CLAIM_BIN_CELL:{session_id}:{bin_code}:{bin_cell_index}:{pkg_code}",
    )


def _build_material_mounted_fact_intent(
    ctx: PluginContext,
    *,
    result: NormalizedCommandResult,
) -> RuntimeIntent | None:
    context = dict(cast("Mapping[str, Any]", getattr(ctx.session, "context_json", None) or {}))
    bin_location = context.get("bin_location")
    if not isinstance(bin_location, Mapping):
        return None
    pkg_code = (
        non_empty_str(context.get("pkg_id"))
        or non_empty_str(context.get("barcode"))
        or _resolve_pkg_id_from_result(result)
    )
    if pkg_code is None:
        return None
    bin_code = non_empty_str(bin_location.get("bin_id"))
    bin_cell_index = non_empty_str(bin_location.get("bin_cell_index"))
    if bin_code is None or bin_cell_index is None:
        return None

    six_in_one = context.get("six_in_one")
    normalized_six: dict[str, Any] = {}
    if isinstance(six_in_one, Mapping):
        normalized_six = normalize_six_in_one_payload(dict(cast("Mapping[str, Any]", six_in_one))) or {}
    source_event_id = (
        non_empty_str(getattr(result, "command_code", None)) or f"OUTPUT_ARM:{pkg_code}:{bin_code}:{bin_cell_index}"
    )
    payload = {
        "pkg_code": pkg_code,
        "bin_code": bin_code,
        "bin_cell_code": non_empty_str(bin_location.get("bin_cell_location")),
        "bin_cell_index": bin_cell_index,
        "material_identity_key": _material_identity_key_from_context(context, pkg_code=pkg_code),
        "material_code": non_empty_str(normalized_six.get("HHPN")),
        "lot_code": non_empty_str(normalized_six.get("LotCode")),
        "date_code": non_empty_str(normalized_six.get("DateCode")),
        "qty_snapshot": normalized_six.get("Qty"),
        "reel_diameter": non_empty_str(context.get("reel_diameter")),
        "reel_thickness": non_empty_str(context.get("reel_thickness")),
        "cell_capacity_depth_mm": bin_location.get("capacity_depth_mm"),
        "source_system": "WES_RUNTIME",
        "source_event_id": source_event_id,
    }
    return ctx.next.resource_fact(
        fact_type="MATERIAL_MOUNTED",
        payload=payload,
        idempotency_key=f"MATERIAL_MOUNTED:{source_event_id}:{pkg_code}:{bin_code}:{bin_cell_index}",
    )


def _build_bin_cell_consumption_intent(
    ctx: PluginContext,
    *,
    result: NormalizedCommandResult,
) -> RuntimeIntent | None:
    context = getattr(getattr(ctx, "session", None), "context_json", None)
    if not isinstance(context, Mapping):
        return None
    bin_location = context.get("bin_location")
    if not isinstance(bin_location, Mapping):
        return None
    bin_code = non_empty_str(bin_location.get("bin_id"))
    bin_cell_index = non_empty_str(bin_location.get("bin_cell_index"))
    if bin_code is None or bin_cell_index is None:
        return None

    session_id = getattr(getattr(ctx, "session", None), "id", None)
    source_event_id = non_empty_str(getattr(result, "command_code", None)) or f"OUTPUT_ARM:{bin_code}:{bin_cell_index}"
    return ctx.next.resource_reservation(
        operation="CONSUME_BIN_CELL",
        payload={"bin_code": bin_code, "bin_cell_index": bin_cell_index},
        idempotency_key=f"CONSUME_BIN_CELL:{session_id}:{source_event_id}:{bin_code}:{bin_cell_index}",
    )


def _build_bin_cell_release_intent(
    ctx: PluginContext,
    *,
    result: NormalizedCommandResult,
) -> RuntimeIntent | None:
    context = getattr(getattr(ctx, "session", None), "context_json", None)
    if not isinstance(context, Mapping):
        return None
    bin_location = context.get("bin_location")
    if not isinstance(bin_location, Mapping):
        return None
    bin_code = non_empty_str(bin_location.get("bin_id"))
    bin_cell_index = non_empty_str(bin_location.get("bin_cell_index"))
    if bin_code is None or bin_cell_index is None:
        return None

    session_id = getattr(getattr(ctx, "session", None), "id", None)
    source_event_id = (
        non_empty_str(getattr(result, "command_code", None)) or f"PICK_AND_PUT_FAILED:{bin_code}:{bin_cell_index}"
    )
    return ctx.next.resource_reservation(
        operation="RELEASE_BIN_CELL",
        payload={
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "source_event_id": source_event_id,
        },
        idempotency_key=f"RELEASE_BIN_CELL:{session_id}:{source_event_id}:{bin_code}:{bin_cell_index}",
    )


def _pick_failed_output_arm_intents(
    ctx: PluginContext,
    *,
    result: NormalizedCommandResult,
    error_code: str,
    error_message: str,
    requires_manual_hold: bool,
) -> RuntimeIntent | list[RuntimeIntent]:
    release_reservation = _build_bin_cell_release_intent(ctx, result=result)
    if requires_manual_hold:
        manual_hold_intents = _manual_hold_intents(ctx, error_code=error_code, error_message=error_message)
        if release_reservation is None:
            return manual_hold_intents
        return [release_reservation, *manual_hold_intents]

    block_intent = ctx.next.block(
        scope=BlockScope.COMMAND,
        reason_code=error_code,
        message=error_message,
    )
    if release_reservation is None:
        return block_intent
    return [release_reservation, block_intent]


def _rack_expected_position_code_from_operation(rack_operation: Mapping[str, Any]) -> str | None:
    return (
        non_empty_str(rack_operation.get("work_position_code"))
        or non_empty_str(rack_operation.get("target_position_code"))
        or non_empty_str(rack_operation.get("position_code"))
    )


def _rack_position_code_from_operation(
    rack_operation: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
) -> str:
    callback_position_code = non_empty_str(payload.get("position_code")) if payload is not None else None
    return _rack_expected_position_code_from_operation(rack_operation) or callback_position_code or "SINGLE_LAYER_A"


def _workline_code_from_context(ctx: PluginContext) -> str | None:
    workline = getattr(ctx, "workline", None)
    return non_empty_str(getattr(workline, "line_code", None)) or non_empty_str(
        getattr(workline, "workline_code", None)
    )


def _rack_workline_code_from_operation(ctx: PluginContext, rack_operation: Mapping[str, Any]) -> str:
    return non_empty_str(rack_operation.get("workline_code")) or _workline_code_from_context(ctx) or ""


def _rack_operation_expected_dispatch_keys(rack_operation: Mapping[str, Any]) -> set[str]:
    dispatch_keys: set[str] = set()
    for field_name in ("dispatch_key", "task_dispatch_key"):
        dispatch_key = non_empty_str(rack_operation.get(field_name))
        if dispatch_key is not None:
            dispatch_keys.add(dispatch_key)

    for field_name in ("dispatch_keys", "task_dispatch_keys", "required_task_dispatch_keys"):
        raw_dispatch_keys = rack_operation.get(field_name)
        if isinstance(raw_dispatch_keys, Sequence) and not isinstance(raw_dispatch_keys, (str, bytes)):
            dispatch_keys.update(
                dispatch_key
                for raw_dispatch_key in raw_dispatch_keys
                if (dispatch_key := non_empty_str(raw_dispatch_key)) is not None
            )

    operation_key = non_empty_str(rack_operation.get("operation_key"))
    operation_type = non_empty_str(rack_operation.get("operation_type"))
    raw_sequences = rack_operation.get("task_sequences")
    if (
        operation_key is None
        or operation_type is None
        or not isinstance(raw_sequences, Sequence)
        or isinstance(raw_sequences, (str, bytes))
    ):
        return dispatch_keys

    for raw_sequence in raw_sequences:
        if isinstance(raw_sequence, bool):
            continue
        sequence_no = raw_sequence if isinstance(raw_sequence, int) else None
        if sequence_no is None:
            continue
        task_type = _RACK_OPERATION_TASK_TYPES_BY_SEQUENCE.get((operation_type, sequence_no))
        if task_type is not None:
            dispatch_keys.add(f"rack-operation:{operation_key}:{sequence_no}:{task_type}")
    return dispatch_keys


def _rack_operation_released_rack_codes(rack_operation: Mapping[str, Any]) -> list[str]:
    raw_codes = rack_operation.get("released_rack_codes") or rack_operation.get("move_out_rack_codes")
    if raw_codes is None:
        return []
    if isinstance(raw_codes, str | bytes):
        candidates = [raw_codes]
    elif isinstance(raw_codes, Sequence):
        candidates = list(raw_codes)
    else:
        candidates = [raw_codes]

    result: list[str] = []
    seen: set[str] = set()
    for raw_code in candidates:
        rack_code = non_empty_str(raw_code)
        if rack_code is None or rack_code in seen:
            continue
        result.append(rack_code)
        seen.add(rack_code)
    return result


def _rack_operation_target_mismatch(
    ctx: PluginContext,
    *,
    rack_operation: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    expected_workline_code = _rack_workline_code_from_operation(ctx, rack_operation)
    callback_workline_code = non_empty_str(payload.get("workline_code"))
    if (
        callback_workline_code is not None
        and expected_workline_code
        and callback_workline_code != expected_workline_code
    ):
        return ("workline_code", expected_workline_code, callback_workline_code)

    expected_position_code = _rack_expected_position_code_from_operation(rack_operation)
    callback_position_code = non_empty_str(payload.get("position_code"))
    if (
        expected_position_code is not None
        and callback_position_code is not None
        and callback_position_code != expected_position_code
    ):
        return ("position_code", expected_position_code, callback_position_code)
    return None


def _rack_arrived_fact_intent(
    ctx: PluginContext,
    *,
    payload: Mapping[str, Any],
    rack_operation: Mapping[str, Any],
    active_bin_rack: Mapping[str, Any],
) -> RuntimeIntent:
    rack_code = non_empty_str(active_bin_rack.get("rack_code")) or non_empty_str(active_bin_rack.get("rack_id")) or ""
    source_event_id = non_empty_str(payload.get("source_event_id")) or f"WMS_RACK_ARRIVED:{rack_code}"
    workline_code = _rack_workline_code_from_operation(ctx, rack_operation)
    position_code = _rack_position_code_from_operation(rack_operation, payload=payload)
    fact_payload = {
        "rack_code": rack_code,
        "rack_kind": non_empty_str(active_bin_rack.get("rack_kind")) or "SINGLE_LAYER",
        "workline_code": workline_code,
        "position_code": position_code,
        "source_system": non_empty_str(payload.get("source_system")) or "WMS",
        "source_event_id": source_event_id,
        "source_version": non_empty_str(payload.get("source_version")),
        "source_task_id": non_empty_str(payload.get("request_id")),
        "external_location_code": non_empty_str(payload.get("external_location_code")),
        "occurred_at": payload.get("occurred_at"),
    }
    released_rack_codes = _rack_operation_released_rack_codes(rack_operation)
    if released_rack_codes:
        fact_payload["released_rack_codes"] = released_rack_codes
    return ctx.next.resource_fact(
        fact_type="RACK_ARRIVED",
        payload=fact_payload,
        idempotency_key=f"WMS_RACK_ARRIVED:{rack_code}:{workline_code}:{position_code}:{source_event_id}",
    )


def _bin_mounted_fact_intent(
    ctx: PluginContext,
    *,
    payload: Mapping[str, Any],
    active_bin_rack: Mapping[str, Any],
) -> RuntimeIntent:
    rack_code = non_empty_str(active_bin_rack.get("rack_code")) or non_empty_str(active_bin_rack.get("rack_id")) or ""
    source_event_id = non_empty_str(payload.get("source_event_id")) or f"BIN_MOUNTED:{rack_code}"
    cells = active_bin_rack.get("cells")
    bin_mounts: list[dict[str, Any]] = []
    seen_mounts: set[tuple[str, str]] = set()
    if isinstance(cells, Sequence) and not isinstance(cells, (str, bytes)):
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            rack_slot_code = non_empty_str(cell.get("rack_slot_code"))
            bin_code = non_empty_str(cell.get("bin_code")) or non_empty_str(cell.get("bin_id"))
            if rack_slot_code is None or bin_code is None:
                continue
            mount_key = (rack_slot_code, bin_code)
            if mount_key in seen_mounts:
                continue
            seen_mounts.add(mount_key)
            bin_mounts.append({"rack_slot_code": rack_slot_code, "bin_code": bin_code})
    return ctx.next.resource_fact(
        fact_type="BIN_MOUNTED",
        payload={
            "rack_code": rack_code,
            "bin_mounts": bin_mounts,
            "source_system": non_empty_str(payload.get("source_system")) or "WMS",
            "source_event_id": source_event_id,
            "source_version": non_empty_str(payload.get("source_version")),
            "occurred_at": payload.get("occurred_at"),
        },
        idempotency_key=f"BIN_MOUNTED:{rack_code}:{source_event_id}:{len(bin_mounts)}",
    )


def _duplicate_bin_mount_message(active_bin_rack: Mapping[str, Any]) -> str | None:
    cells = active_bin_rack.get("cells")
    if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
        return None

    bin_by_slot: dict[str, str] = {}
    slot_by_bin: dict[str, str] = {}
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        rack_slot_code = non_empty_str(cell.get("rack_slot_code"))
        bin_code = non_empty_str(cell.get("bin_code")) or non_empty_str(cell.get("bin_id"))
        if rack_slot_code is None or bin_code is None:
            continue

        mounted_bin = bin_by_slot.get(rack_slot_code)
        if mounted_bin is not None and mounted_bin != bin_code:
            return (
                "WMS_RACK_ARRIVED 回调 active_bin_rack 同一槽位出现多个料箱"
                f": rack_slot_code={rack_slot_code}, bin_codes={mounted_bin},{bin_code}"
            )
        bin_by_slot[rack_slot_code] = bin_code

        mounted_slot = slot_by_bin.get(bin_code)
        if mounted_slot is not None and mounted_slot != rack_slot_code:
            return (
                "WMS_RACK_ARRIVED 回调 active_bin_rack 同一料箱出现在多个槽位"
                f": bin_code={bin_code}, rack_slot_codes={mounted_slot},{rack_slot_code}"
            )
        slot_by_bin[bin_code] = rack_slot_code
    return None


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

        payload: Any = getattr(inbox, "payload_json", None) or {}
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

        measurement_payload = cast("dict[str, Any]", raw_measurement_data)
        normalized_measurement_payload = normalize_six_in_one_payload(measurement_payload) or {}
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
            material_fact = _build_material_mounted_fact_intent(ctx, result=result)
            if material_fact is None:
                return build_payload_invalid_block("OUTPUT_ARM 成功回调缺少物料占格上下文")
            consume_reservation = _build_bin_cell_consumption_intent(ctx, result=result)
            if consume_reservation is None:
                return build_payload_invalid_block("OUTPUT_ARM 成功回调缺少料格预占消费上下文")
            return [
                material_fact,
                consume_reservation,
                ctx.next.complete({"material_mounted": True}),
            ]

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
            return _pick_failed_output_arm_intents(
                ctx,
                result=result,
                error_code=error_code,
                error_message=error_msg,
                requires_manual_hold=requires_manual_hold,
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
        waiting_operation_intents = await self._waiting_rack_operation_intents(
            ctx,
            pkg_id=pkg_id,
            reel_diameter=reel_diameter,
            source_event_id=non_empty_str(result.command_code),
            resume_source_device_code=non_empty_str(result.device_code),
            resume_source_device_role=_source_device_role(ctx) or self.CONVEYOR,
        )
        if waiting_operation_intents is not None:
            return waiting_operation_intents

        allocation_decision = await self._allocate_bin(ctx, pkg_id)
        if allocation_decision.kind == "BLOCKED":
            return ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code=allocation_decision.reason_code or "BIN_SCHEDULING_BLOCKED",
                message=allocation_decision.message or "SMT 料箱调度阻断",
            )

        if allocation_decision.rack_operation_request is not None:
            return self._rack_operation_required_intents(
                ctx,
                allocation_decision,
                pkg_id=pkg_id,
                resume_source_device_code=non_empty_str(result.device_code),
                resume_source_device_role=_source_device_role(ctx) or self.CONVEYOR,
            )

        bin_location, invalid = _validated_bin_location_or_block(
            allocation_decision.bin_location,
            reel_diameter=reel_diameter,
        )
        if invalid is not None or bin_location is None:
            return invalid or build_payload_invalid_block("料箱调度结果非法")

        logger.info(f"Bin allocated: {bin_location}")

        return self._allocated_bin_intents(
            ctx,
            pkg_id=pkg_id,
            reel_diameter=str(reel_diameter),
            bin_location=bin_location,
            source_event_id=non_empty_str(result.command_code),
        )

    def _rack_operation_required_intents(
        self,
        ctx: PluginContext,
        allocation_decision: SmtRackBinSchedulingDecision,
        *,
        pkg_id: str,
        resume_source_device_code: str | None = None,
        resume_source_device_role: str | None = None,
    ) -> list[RuntimeIntent]:
        request = allocation_decision.rack_operation_request
        if request is None:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="RACK_OPERATION_REQUEST_MISSING",
                    message="SMT 料箱调度缺少货架 operation 请求",
                )
            ]

        request_payload = _rack_operation_payload(ctx, request, reason_code=allocation_decision.reason_code)
        request_reason_code = non_empty_str(request_payload.get("reason_code"))
        if request_reason_code is None:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="RACK_OPERATION_REASON_MISSING",
                    message="SMT 货架 operation 请求缺少 reason_code",
                )
            ]

        context_patch = SmtClassifierContext(pkg_id=pkg_id).to_patch()
        if _is_rack_operation_request(request):
            context_patch["rack_operation"] = _rack_operation_context(
                request,
                pkg_id=pkg_id,
                reason_code=request_reason_code,
                resume_source_device_code=resume_source_device_code,
                resume_source_device_role=resume_source_device_role,
            )
        context_patch["waiting_rack_operation_key"] = request.operation_key

        return [
            ctx.next.update_context(context_patch),
            ctx.next.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key=request.operation_key,
                target_code=request.target_code,
                payload=request_payload,
                timeout_seconds=request.timeout_seconds,
            ),
        ]

    async def _waiting_rack_operation_intents(
        self,
        ctx: PluginContext,
        *,
        pkg_id: str,
        reel_diameter: str,
        source_event_id: str | None,
        resume_source_device_code: str | None,
        resume_source_device_role: str | None,
    ) -> list[RuntimeIntent] | None:
        session_context = dict(cast("Mapping[str, Any]", getattr(ctx.session, "context_json", None) or {}))
        waiting_operation_key = non_empty_str(session_context.get("waiting_rack_operation_key"))
        if waiting_operation_key is None:
            return None

        status_provider = ctx.services.rack_operation_status_provider
        if status_provider is None:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="RACK_OPERATION_STATUS_PROVIDER_MISSING",
                    message="SMT 货架 operation 等待态缺少状态读取服务",
                )
            ]

        status = str(await status_provider.derive_operation_status(waiting_operation_key)).upper()
        existing_operation = session_context.get("rack_operation")
        rack_operation = (
            dict(cast("Mapping[str, Any]", existing_operation)) if isinstance(existing_operation, Mapping) else {}
        )
        rack_operation.update({"operation_key": waiting_operation_key, "status": status})

        status_intents = self._waiting_rack_operation_status_intents(
            ctx,
            status=status,
            rack_operation=rack_operation,
        )
        if status_intents is not None:
            return status_intents

        allocation_context = {**session_context, "waiting_rack_operation_key": None, "rack_operation": rack_operation}
        allocation_decision = await self._allocate_bin(ctx, pkg_id, allocation_context=allocation_context)
        if allocation_decision.kind == "BLOCKED":
            return [
                ctx.next.update_context({"waiting_rack_operation_key": None, "rack_operation": rack_operation}),
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=allocation_decision.reason_code or "BIN_SCHEDULING_BLOCKED",
                    message=allocation_decision.message or "SMT 料箱调度阻断",
                ),
            ]

        if allocation_decision.rack_operation_request is not None:
            return self._rack_operation_required_intents(
                ctx,
                allocation_decision,
                pkg_id=pkg_id,
                resume_source_device_code=resume_source_device_code,
                resume_source_device_role=resume_source_device_role,
            )

        bin_location, invalid = _validated_bin_location_or_block(
            allocation_decision.bin_location,
            reel_diameter=reel_diameter,
        )
        if invalid is not None or bin_location is None:
            return [invalid or build_payload_invalid_block("料箱调度结果非法")]

        return [
            ctx.next.update_context({"waiting_rack_operation_key": None, "rack_operation": rack_operation}),
            *self._allocated_bin_intents(
                ctx,
                pkg_id=pkg_id,
                reel_diameter=str(reel_diameter),
                bin_location=bin_location,
                source_event_id=source_event_id,
            ),
        ]

    def _waiting_rack_operation_status_intents(
        self,
        ctx: PluginContext,
        *,
        status: str,
        rack_operation: Mapping[str, Any],
    ) -> list[RuntimeIntent] | None:
        if status == "SUCCEEDED":
            return None
        if status == "PENDING":
            return [ctx.next.update_context({"rack_operation": dict(rack_operation)})]
        if status in {"FAILED", "RECONCILING"}:
            reason_code = f"RACK_OPERATION_{status}"
            return [
                ctx.next.update_context({"rack_operation": dict(rack_operation)}),
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=reason_code,
                    message=f"SMT 货架 operation 状态为 {status}，当前物料等待人工处理",
                ),
            ]
        return [
            ctx.next.block(
                scope=BlockScope.MATERIAL,
                reason_code="RACK_OPERATION_STATUS_UNSUPPORTED",
                message=f"SMT 货架 operation 状态不支持: {status}",
            )
        ]

    def _allocated_bin_intents(
        self,
        ctx: PluginContext,
        *,
        pkg_id: str,
        reel_diameter: str,
        bin_location: Mapping[str, Any],
        source_event_id: str | None,
    ) -> list[RuntimeIntent]:
        return [
            ctx.next.update_context(
                SmtClassifierContext(
                    pkg_id=pkg_id,
                    bin_location=bin_location,
                ).to_patch()
            ),
            _build_bin_cell_reservation_intent(
                ctx,
                pkg_code=pkg_id,
                bin_location=bin_location,
                source_event_id=source_event_id,
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

        raw_payload: Any = getattr(inbox, "payload_json", None) or {}
        if not isinstance(raw_payload, Mapping):
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调 payload 非法")]
        payload = cast("Mapping[str, Any]", raw_payload)

        callback_type = non_empty_str(payload.get("callback_type"))
        if callback_type not in _RACK_OPERATION_CALLBACK_TYPES:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调 callback_type 不支持")]

        envelope_error = _validate_wms_rcs_callback_envelope(payload)
        if envelope_error is not None:
            return [build_payload_invalid_block(envelope_error)]

        rack_operation = self._rack_operation_from_context(ctx)
        if rack_operation is None:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调缺少待处理 rack_operation 上下文")]

        expected_dispatch_keys = _rack_operation_expected_dispatch_keys(rack_operation)
        callback_dispatch_key = non_empty_str(payload.get("dispatch_key"))
        if not expected_dispatch_keys or callback_dispatch_key is None:
            return [build_payload_invalid_block("EXTERNAL_HTTP 回调缺少 dispatch_key")]
        if callback_dispatch_key not in expected_dispatch_keys:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="DISPATCH_KEY_MISMATCH",
                    message="EXTERNAL_HTTP 回调 dispatch_key 与当前换架请求不匹配",
                )
            ]

        if not self._is_pending_rack_operation_callback(ctx, rack_operation) and not (
            callback_type in _RACK_ARRIVED_CALLBACK_TYPES
            and self._is_pre_projection_rack_arrival_callback(ctx, rack_operation)
        ):
            return [ctx.next.update_context({"rack_operation": rack_operation})]

        return await self._handle_rack_operation_callback(ctx, payload, callback_type, rack_operation)

    def _is_pending_rack_operation_callback(self, ctx: PluginContext, rack_operation: Mapping[str, Any]) -> bool:
        status = non_empty_str(rack_operation.get("status"))
        if status is None or status.upper() not in _RACK_OPERATION_WAITING_STATUSES:
            return False
        return self._rack_operation_matches_waiting_context(ctx, rack_operation)

    def _is_pre_projection_rack_arrival_callback(self, ctx: PluginContext, rack_operation: Mapping[str, Any]) -> bool:
        status = non_empty_str(rack_operation.get("status"))
        if status is None or status.upper() not in _RACK_OPERATION_PRE_PROJECTION_STATUSES:
            return False
        return self._rack_operation_matches_waiting_context(ctx, rack_operation)

    def _rack_operation_matches_waiting_context(self, ctx: PluginContext, rack_operation: Mapping[str, Any]) -> bool:
        session_context = getattr(ctx.session, "context_json", None)
        if not isinstance(session_context, Mapping):
            return False
        waiting_operation_key = non_empty_str(session_context.get("waiting_rack_operation_key"))
        operation_key = non_empty_str(rack_operation.get("operation_key"))
        return waiting_operation_key is not None and waiting_operation_key == operation_key

    async def _handle_rack_operation_callback(  # noqa: PLR0911 - WMS/RCS 回调按状态和校验失败显式早返回
        self,
        ctx: PluginContext,
        payload: Mapping[str, Any],
        callback_type: str,
        rack_operation: dict[str, Any],
    ) -> list[RuntimeIntent]:
        """处理已通过 operation_key 校验的货架 operation 回调。"""

        if callback_type in _RACK_EXCHANGE_PROGRESS_CALLBACK_TYPES:
            status = non_empty_str(payload.get("status")) or "IN_PROGRESS"
            updated_operation = {**rack_operation, "status": status}
            return [ctx.next.update_context({"rack_operation": updated_operation})]

        if callback_type in _RACK_EXCHANGE_FAILED_CALLBACK_TYPES:
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
        duplicate_mount_message = _duplicate_bin_mount_message(active_bin_rack)
        if duplicate_mount_message is not None:
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="ACTIVE_RACK_BIN_MOUNT_DUPLICATE",
                    message=duplicate_mount_message,
                )
            ]

        session_context = dict(cast("Mapping[str, Any]", getattr(ctx.session, "context_json", None) or {}))
        pkg_id = non_empty_str(rack_operation.get("pkg_id")) or non_empty_str(session_context.get("pkg_id"))
        if pkg_id is None:
            return [build_payload_invalid_block("WMS_RACK_ARRIVED 回调缺少 pkg_id")]

        target_mismatch = _rack_operation_target_mismatch(ctx, rack_operation=rack_operation, payload=payload)
        if target_mismatch is not None:
            field_name, expected, actual = target_mismatch
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code="RACK_OPERATION_TARGET_MISMATCH",
                    message=(
                        f"WMS_RACK_ARRIVED 回调 {field_name} 与当前等待 Session 目标不一致"
                        f": expected={expected}, actual={actual}"
                    ),
                )
            ]

        active_rack_snapshot = dict(cast("Mapping[str, Any]", active_bin_rack))
        operation_for_allocation = {**rack_operation, "status": "SUCCEEDED"}
        allocation_context: dict[str, Any] = {
            **session_context,
            "active_bin_rack": active_rack_snapshot,
            "rack_operation": operation_for_allocation,
            "waiting_rack_operation_key": None,
        }
        allocation_decision = await self._allocate_bin(ctx, pkg_id, allocation_context=allocation_context)
        if allocation_decision.kind == "BLOCKED":
            return [
                ctx.next.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=allocation_decision.reason_code or "BIN_SCHEDULING_BLOCKED",
                    message=allocation_decision.message or "SMT 料箱调度阻断",
                )
            ]
        if allocation_decision.rack_operation_request is not None:
            return self._rack_operation_required_intents(
                ctx,
                allocation_decision,
                pkg_id=pkg_id,
                resume_source_device_code=non_empty_str(rack_operation.get("resume_source_device_code")),
                resume_source_device_role=non_empty_str(rack_operation.get("resume_source_device_role")),
            )

        reel_diameter = parse_smt_context(ctx).reel_diameter or ""
        bin_location, invalid = _validated_bin_location_or_block(
            allocation_decision.bin_location,
            reel_diameter=reel_diameter,
        )
        if invalid is not None or bin_location is None:
            return [invalid or build_payload_invalid_block("料箱调度结果非法")]

        arrived_rack_operation = {
            **rack_operation,
            "status": "ARRIVED",
            "position_code": _rack_position_code_from_operation(rack_operation, payload=payload),
        }
        workline_code = _rack_workline_code_from_operation(ctx, rack_operation)
        if workline_code:
            arrived_rack_operation["workline_code"] = workline_code

        return [
            _rack_arrived_fact_intent(
                ctx,
                payload=payload,
                rack_operation=rack_operation,
                active_bin_rack=active_rack_snapshot,
            ),
            _bin_mounted_fact_intent(
                ctx,
                payload=payload,
                active_bin_rack=active_rack_snapshot,
            ),
            ctx.next.update_context(
                {
                    "active_bin_rack": active_rack_snapshot,
                    "rack_operation": arrived_rack_operation,
                    "waiting_rack_operation_key": None,
                    "pkg_id": pkg_id,
                    "bin_location": bin_location,
                }
            ),
            _build_bin_cell_reservation_intent(
                ctx,
                pkg_code=pkg_id,
                bin_location=bin_location,
                source_event_id=non_empty_str(payload.get("source_event_id")),
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

    def _rack_operation_from_context(self, ctx: PluginContext) -> dict[str, Any] | None:
        context = getattr(ctx.session, "context_json", None)
        if not isinstance(context, Mapping):
            return None
        session_context = cast("Mapping[str, Any]", context)
        rack_operation = session_context.get("rack_operation")
        if isinstance(rack_operation, Mapping):
            return dict(cast("Mapping[str, Any]", rack_operation))
        return None

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
        if not isinstance(context.get("active_bin_rack"), Mapping):
            active_rack_snapshot_provider = ctx.services.active_rack_snapshot_provider
            if active_rack_snapshot_provider is not None:
                active_bin_rack = active_rack_snapshot_provider.active_bin_rack(context=context)
                if inspect.isawaitable(active_bin_rack):
                    active_bin_rack = await active_bin_rack
                if isinstance(active_bin_rack, Mapping):
                    context["active_bin_rack"] = dict(cast("Mapping[str, Any]", active_bin_rack))
        if hasattr(allocator, "plan_allocation"):
            allocation = allocator.plan_allocation(barcode, context=context)
        else:
            allocation = allocator.allocate(barcode)
        if inspect.isawaitable(allocation):
            allocation = await allocation
        return _normalize_bin_scheduling_decision(allocation)


smt_classifier_plugin = SmtClassifierPlugin()


__all__ = ["SmtClassifierPlugin", "smt_classifier_plugin"]
