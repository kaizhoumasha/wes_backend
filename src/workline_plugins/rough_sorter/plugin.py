"""粗分机工作线插件。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.app.wms_integration.models import QueryInventoryRequest, QueryInventoryResponse, WmsInventoryItem
from src.app.wms_integration.services.exceptions import WmsBusinessRejectedError, WmsIntegrationError
from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service
from src.app.workline.models.material_unit import MaterialUnitStatus
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    ACTION_TARGET_ROLES,
    EVENT_ROUGH_SORTER_STORAGE_RETRY,
    EVENT_SCAN_COMPLETED,
    NG_REASON_BARCODE_INCOMPLETE,
    NG_REASON_BARCODE_INVALID,
    NG_REASON_BARCODE_RULE_NG,
    NG_REASON_MEASUREMENT_NG,
    NG_REASON_WMS_REJECTED,
    PHASE_COMPLETED,
    PHASE_MOVING_FORWARD,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
    ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE,
    build_move_forward_payload,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
    build_put_to_bin_payload,
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)
from src.workline_runtime.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.plugin_manifest import ResourceBoundary, WorklinePluginManifest
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

DEFAULT_NG_LOCATION = "NG-01"
DEFAULT_PIPELINE_INPUT_LOCATION = "PIPELINE-IN-01"
DEFAULT_PIPELINE_OUTPUT_LOCATION = "PIPELINE-OUT-01"
POSITION_SCAN_POINT = "ROUGH_SORTER_SCAN_POINT"
POSITION_WORK_SINGLE_LAYER = "SINGLE_LAYER_A"


def _ng_reason(canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=ROUGH_SORTER_PLUGIN_KEY,
        contract_version=ROUGH_SORTER_CONTRACT_VERSION,
        maps_from=(canonical_code,),
    )


def _non_empty_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _non_empty_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        item = _non_empty_str(value)
        return [item] if item is not None else []
    if not isinstance(value, list):
        return []
    return [item for raw_item in cast("list[Any]", value) if (item := _non_empty_str(raw_item)) is not None]


def _payload_data(payload_json: dict[str, Any]) -> dict[str, Any]:
    data = payload_json.get("data")
    return cast("dict[str, Any]", data.copy()) if isinstance(data, dict) else {}


def _normalized_data(ctx: PluginContext) -> dict[str, Any]:
    data = getattr(getattr(ctx, "normalized_input", None), "data", None)
    return cast("dict[str, Any]", data.copy()) if isinstance(data, dict) else {}


def _normalized_error_detail(ctx: PluginContext) -> dict[str, Any]:
    error_detail = getattr(getattr(ctx, "normalized_input", None), "error_detail", None)
    return cast("dict[str, Any]", error_detail.copy()) if isinstance(error_detail, dict) else {}


def _session_context(ctx: PluginContext) -> RoughSorterContext:
    raw_context = getattr(getattr(ctx, "session", None), "context_json", None)
    return RoughSorterContext.model_validate(raw_context if isinstance(raw_context, dict) else {})


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _measurement_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    reel_diameter = _parse_decimal(data.get("reel_diameter"))
    reel_thickness = _parse_decimal(data.get("reel_thickness"))
    if (
        reel_diameter is None
        or reel_thickness is None
        or not reel_diameter.is_finite()
        or not reel_thickness.is_finite()
        or reel_diameter <= 0
        or reel_thickness <= 0
    ):
        return None
    measurement = dict(data)
    measurement["reel_diameter"] = str(reel_diameter)
    measurement["reel_thickness"] = str(reel_thickness)
    return measurement


def _measurement_is_ng(data: dict[str, Any]) -> bool:
    return any(
        str(data.get(field_name) or "").upper() == "NG"
        for field_name in ("measurement_result", "inspection_result", "size_judgement", "thickness_judgement")
    )


def _wms_item_matches(item: WmsInventoryItem, *, sku: str, lot_no: str) -> bool:
    return item.sku == sku and item.lot_no == lot_no


def _business_key_from_context(rough_context: RoughSorterContext) -> str | None:
    return rough_context.business_key or _non_empty_str(rough_context.six_in_one.get("PkgID"))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else {}


def _rough_sorter_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    return (
        _ng_reason(NG_REASON_BARCODE_INVALID, "条码无效"),
        _ng_reason(NG_REASON_BARCODE_INCOMPLETE, "条码不完整"),
        _ng_reason(NG_REASON_BARCODE_RULE_NG, "条码规则判定 NG"),
        _ng_reason(NG_REASON_MEASUREMENT_NG, "测量业务判定 NG"),
        _ng_reason(NG_REASON_WMS_REJECTED, "WMS 库存校验拒绝"),
    )


class RoughSorterPlugin(WorklinePlugin):
    """粗分机插件。"""

    plugin_key = ROUGH_SORTER_PLUGIN_KEY
    contract_version = ROUGH_SORTER_CONTRACT_VERSION

    manifest = WorklinePluginManifest.from_yaml_file(Path(__file__).with_name("manifest.yaml"))

    def resolve_business_key(self, payload_json: dict[str, Any]) -> str | None:
        return resolve_rough_sorter_business_key(payload_json)

    def classify_result(self, payload_json: dict[str, Any]) -> str | None:
        return classify_rough_sorter_result(payload_json)

    def get_context_model(self) -> type[RoughSorterContext]:
        return RoughSorterContext

    def list_ng_reasons(self) -> tuple[NgReasonDefinition, ...]:
        return _rough_sorter_ng_reasons()

    def resolve_material_identity(self, input_value: MaterialIdentityInput) -> MaterialIdentity:
        session_context = input_value.session_context or {}
        source_payload = input_value.source_payload or {}
        command_payload = input_value.command_payload or {}
        six_in_one = _dict_or_empty(session_context.get("six_in_one"))
        payload_key = self.resolve_business_key(dict(cast("Mapping[str, Any]", source_payload)))
        business_key = (
            payload_key
            or _non_empty_str(session_context.get("business_key"))
            or _non_empty_str(command_payload.get("business_key"))
            or _non_empty_str(six_in_one.get("PkgID"))
        )
        if business_key is None:
            return MaterialIdentity(
                resolution_status=MaterialIdentityResolutionStatus.MISSING,
                raw_evidence_hash=material_identity_input_to_hash(input_value),
            )
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
            idempotency_key=business_key,
            business_key=business_key,
            display={key: value for key, value in six_in_one.items() if value is not None},
            raw_evidence_hash=material_identity_input_to_hash(input_value),
        )

    @staticmethod
    def _scan_source_location(payload_json: dict[str, Any]) -> str:
        payload_location = _non_empty_str(_payload_data(payload_json).get("location"))
        if payload_location:
            return payload_location
        device_code = payload_json.get("device_code")
        return device_code if isinstance(device_code, str) and device_code else "UNKNOWN"

    @staticmethod
    def _command_source_location(ctx: PluginContext, payload_json: dict[str, Any]) -> str:
        device_code = _non_empty_str(payload_json.get("device_code"))
        if device_code:
            return device_code
        normalized_device_code = _non_empty_str(getattr(getattr(ctx, "normalized_input", None), "device_code", None))
        return normalized_device_code or "UNKNOWN"

    @staticmethod
    def _ng_location(ctx: PluginContext) -> str:
        config = ctx.config
        ng_location = config.get("ng_location")
        if isinstance(ng_location, str) and ng_location:
            return ng_location
        return DEFAULT_NG_LOCATION

    @staticmethod
    def _pipeline_input_location(ctx: PluginContext) -> str:
        config = ctx.config
        pipeline_input_location = config.get("pipeline_input_location")
        if isinstance(pipeline_input_location, str) and pipeline_input_location:
            return pipeline_input_location
        return DEFAULT_PIPELINE_INPUT_LOCATION

    @staticmethod
    def _pipeline_output_location(ctx: PluginContext) -> str:
        config = ctx.config
        pipeline_output_location = config.get("pipeline_output_location")
        if isinstance(pipeline_output_location, str) and pipeline_output_location:
            return pipeline_output_location
        return DEFAULT_PIPELINE_OUTPUT_LOCATION

    @staticmethod
    def _block(reason_code: str, message: str, *, payload: dict[str, Any] | None = None) -> list[RuntimeIntent]:
        return [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code=reason_code,
                message=message,
                suggested_action="人工检查粗分机当前物料与依赖状态",
                payload=payload,
            )
        ]

    @staticmethod
    def _handling_failed_block(ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        payload_json = inbox.payload_json or {}
        error_detail = _payload_data(payload_json)
        payload_error_detail = payload_json.get("error_detail")
        if isinstance(payload_error_detail, dict):
            error_detail.update(cast("dict[str, Any]", payload_error_detail))
        error_detail.update(_normalized_error_detail(ctx))
        error_message = _non_empty_str(error_detail.get("error_message")) or _non_empty_str(error_detail.get("message"))
        return RoughSorterPlugin._block(
            "ROUGH_SORTER_HANDLING_COMMAND_FAILED",
            error_message or "粗分机搬运命令失败，需人工确认设备状态",
            payload={"error_detail": error_detail},
        )

    async def _active_bin_rack(self, ctx: PluginContext, allocation_context: dict[str, Any]) -> dict[str, Any] | None:
        provider = getattr(getattr(ctx, "services", None), "active_rack_snapshot_provider", None)
        if provider is None:
            return None
        snapshot = provider.active_bin_rack(context=allocation_context)
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        return _dict_or_empty(snapshot) or None

    @classmethod
    def _classifier_work_boundary(cls) -> ResourceBoundary | None:
        return next(
            (
                boundary
                for boundary in cls.manifest.resource_boundaries
                if boundary.business_demand_type == "ROUGH_SORTER_BIN_ALLOCATION"
                and boundary.snapshot_kind == "ACTIVE_CLASSIFIER_BIN_RACK"
            ),
            None,
        )

    async def _allocation_context(self, ctx: PluginContext, rough_context: RoughSorterContext) -> dict[str, Any]:
        context: dict[str, Any] = {
            "six_in_one": rough_context.six_in_one,
            "measurement": rough_context.measurement,
            "wms_validation": rough_context.wms_validation,
            "rack_operation": rough_context.rack_operation,
            "config": dict(ctx.config),
            "trace_id": _non_empty_str(getattr(ctx, "trace_id", None)),
        }
        boundary = self._classifier_work_boundary()
        if boundary is not None:
            context.setdefault("work_position_code", boundary.rack_position_code)
            context.setdefault("target_position_code", boundary.rack_position_code)
            context.setdefault("rack_kind", boundary.rack_kind)
        if rough_context.active_bin_rack is not None:
            context["active_bin_rack"] = rough_context.active_bin_rack
        for measurement_key in ("reel_diameter", "reel_thickness"):
            measurement_value = _non_empty_str(rough_context.measurement.get(measurement_key))
            if measurement_value is not None:
                context[measurement_key] = measurement_value
        active_bin_rack = await self._active_bin_rack(ctx, context)
        if active_bin_rack is not None:
            context["active_bin_rack"] = active_bin_rack
        return context

    @staticmethod
    def _bin_location_parts(bin_location: Mapping[str, Any]) -> tuple[str, str, str | None, str | None]:
        bin_code = _non_empty_str(bin_location.get("bin_code")) or _non_empty_str(bin_location.get("bin_id"))
        bin_cell_index = _non_empty_str(bin_location.get("bin_cell_index")) or _non_empty_str(
            bin_location.get("cell_index")
        )
        bin_cell_code = _non_empty_str(bin_location.get("bin_cell_code")) or _non_empty_str(
            bin_location.get("bin_cell_location")
        )
        material_identity_key = _non_empty_str(bin_location.get("material_identity_key"))
        if not bin_code or not bin_cell_index:
            raise ValueError("bin_location requires bin_code/bin_id and bin_cell_index")
        return bin_code, bin_cell_index, bin_cell_code, material_identity_key

    @staticmethod
    def _material_identity_key(six_in_one: Mapping[str, Any]) -> str | None:
        material_code = _non_empty_str(six_in_one.get("HHPN"))
        vendor_code = _non_empty_str(six_in_one.get("MfrPN"))
        date_code = _non_empty_str(six_in_one.get("DateCode"))
        lot_code = _non_empty_str(six_in_one.get("LotCode"))
        if material_code or date_code or lot_code:
            return f"MAT:{material_code or ''}:{vendor_code or ''}:{date_code or ''}:{lot_code or ''}"
        return None

    @staticmethod
    def _rack_tasks_from_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
        actions = payload.get("actions")
        if not isinstance(actions, list):
            return []
        action_values = cast("list[Any]", actions)
        tasks: list[dict[str, Any]] = []
        for index, action in enumerate(action_values, start=1):
            if not isinstance(action, str) or not action:
                continue
            work_position_code = _non_empty_str(payload.get("work_position_code"))
            if work_position_code is None:
                raise ValueError("rack operation actions require work_position_code")
            task: dict[str, Any] = {
                "sequence_no": index,
                "task_type": action,
                "rack_kind": payload.get("new_rack_kind") or "SINGLE_LAYER",
            }
            if action != "MOVE_OUT_ACTIVE_RACK":
                task["target_position_code"] = work_position_code
                task["target_position_role"] = payload.get("target_position_role") or "SMT_CLASSIFIER_SINGLE_RACK_WORK"
            rack_code = payload.get("single_layer_rack_code") or payload.get("single_layer_rack_id")
            if rack_code is not None and action == "MOVE_OUT_ACTIVE_RACK":
                task["rack_code"] = rack_code
                task["source_position_code"] = work_position_code
                task["target_position_role"] = payload.get("move_out_target_position_role") or "SMT_EMPTY_RACK_AREA"
            tasks.append(task)
        return tasks

    def _rack_operation_payload(self, ctx: PluginContext, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized_payload = dict(payload)
        if "trace_id" not in normalized_payload and ctx.trace_id:
            normalized_payload["trace_id"] = ctx.trace_id
        if not isinstance(normalized_payload.get("rack_tasks"), list) and not isinstance(
            normalized_payload.get("task_specs"), list
        ):
            rack_tasks = self._rack_tasks_from_actions(normalized_payload)
            if rack_tasks:
                normalized_payload["rack_tasks"] = rack_tasks
        return normalized_payload

    @staticmethod
    def _operation_key(payload_json: Mapping[str, Any], rough_context: RoughSorterContext) -> str | None:
        return _non_empty_str(payload_json.get("operation_key")) or _non_empty_str(
            rough_context.rack_operation.get("operation_key")
        )

    @staticmethod
    def _session_id(ctx: PluginContext) -> str | None:
        session_id = getattr(getattr(ctx, "session", None), "id", None)
        return str(session_id) if session_id is not None else None

    @staticmethod
    def _resume_source_device_code(ctx: PluginContext, rough_context: RoughSorterContext) -> str | None:
        raw_context = getattr(getattr(ctx, "session", None), "context_json", None)
        session_context = cast("dict[str, Any]", raw_context) if isinstance(raw_context, dict) else {}
        rack_operation = session_context.get("rack_operation")
        rack_operation_map: Mapping[str, Any] = (
            cast("Mapping[str, Any]", rack_operation) if isinstance(rack_operation, Mapping) else {}
        )
        return (
            _non_empty_str(rack_operation_map.get("resume_source_device_code"))
            or _non_empty_str(session_context.get("resume_source_device_code"))
            or _non_empty_str(rough_context.rack_operation.get("resume_source_device_code"))
        )

    @staticmethod
    def _rack_arrived_payload(payload_json: Mapping[str, Any], rough_context: RoughSorterContext) -> dict[str, Any]:
        target = payload_json.get("target")
        target_map: Mapping[str, Any] = cast("Mapping[str, Any]", target) if isinstance(target, Mapping) else {}
        active_bin_rack = payload_json.get("active_bin_rack")
        active_bin_rack_map: Mapping[str, Any] = (
            cast("Mapping[str, Any]", active_bin_rack) if isinstance(active_bin_rack, Mapping) else {}
        )
        position_code = (
            _non_empty_str(payload_json.get("position_code"))
            or _non_empty_str(payload_json.get("target_position_code"))
            or _non_empty_str(target_map.get("position_code"))
            or _non_empty_str(rough_context.rack_operation.get("position_code"))
            or _non_empty_str(rough_context.rack_operation.get("target_position_code"))
            or _non_empty_str(rough_context.rack_operation.get("work_position_code"))
        )
        rack_code = (
            _non_empty_str(payload_json.get("rack_code"))
            or _non_empty_str(payload_json.get("rack_id"))
            or _non_empty_str(active_bin_rack_map.get("rack_code"))
            or _non_empty_str(active_bin_rack_map.get("rack_id"))
            or _non_empty_str(rough_context.rack_operation.get("rack_code"))
        )
        rack_kind = (
            _non_empty_str(payload_json.get("rack_kind"))
            or _non_empty_str(payload_json.get("rack_type"))
            or _non_empty_str(active_bin_rack_map.get("rack_kind"))
            or _non_empty_str(active_bin_rack_map.get("rack_type"))
            or _non_empty_str(rough_context.rack_operation.get("rack_kind"))
            or _non_empty_str(rough_context.rack_operation.get("new_rack_kind"))
        )
        released_rack_codes = _non_empty_str_list(payload_json.get("released_rack_codes")) or _non_empty_str_list(
            rough_context.rack_operation.get("released_rack_codes")
        )
        fact_payload: dict[str, Any] = {
            "operation_key": _non_empty_str(payload_json.get("operation_key"))
            or _non_empty_str(rough_context.rack_operation.get("operation_key")),
            "callback_type": _non_empty_str(payload_json.get("callback_type")),
            "dispatch_key": _non_empty_str(payload_json.get("dispatch_key")),
            "source_event_id": _non_empty_str(payload_json.get("source_event_id")),
            "source_system": _non_empty_str(payload_json.get("source_system")),
            "source_version": _non_empty_str(payload_json.get("source_version")),
            "occurred_at": payload_json.get("occurred_at"),
            "workline_code": _non_empty_str(payload_json.get("workline_code")),
            "rack_code": rack_code,
            "rack_kind": rack_kind,
            "position_code": position_code,
            "released_rack_codes": released_rack_codes or None,
        }
        if isinstance(active_bin_rack, Mapping):
            fact_payload["active_bin_rack"] = dict(cast("Mapping[str, Any]", active_bin_rack))
        return {key: value for key, value in fact_payload.items() if value is not None}

    @staticmethod
    def _bin_mounted_intents(payload_json: Mapping[str, Any], operation_key: str) -> list[RuntimeIntent]:
        bin_mounts = payload_json.get("bin_mounts")
        if not isinstance(bin_mounts, list):
            return []
        rack_code = _non_empty_str(payload_json.get("rack_code"))
        normalized_mounts: list[dict[str, str]] = []
        for mount in cast("list[Any]", bin_mounts):
            if not isinstance(mount, Mapping):
                continue
            mount_payload = dict(cast("Mapping[str, Any]", mount))
            rack_code = (
                rack_code
                or _non_empty_str(mount_payload.get("rack_code"))
                or _non_empty_str(mount_payload.get("rack_id"))
            )
            rack_slot_code = _non_empty_str(mount_payload.get("rack_slot_code")) or _non_empty_str(
                mount_payload.get("slot_code")
            )
            bin_code = _non_empty_str(mount_payload.get("bin_code")) or _non_empty_str(mount_payload.get("bin_id"))
            if rack_slot_code is None or bin_code is None:
                continue
            normalized_mounts.append({"rack_slot_code": rack_slot_code, "bin_code": bin_code})
        if rack_code is None or not normalized_mounts:
            return []
        payload: dict[str, Any] = {
            "operation_key": operation_key,
            "callback_type": _non_empty_str(payload_json.get("callback_type")),
            "dispatch_key": _non_empty_str(payload_json.get("dispatch_key")),
            "source_event_id": _non_empty_str(payload_json.get("source_event_id")),
            "source_system": _non_empty_str(payload_json.get("source_system")),
            "source_version": _non_empty_str(payload_json.get("source_version")),
            "occurred_at": payload_json.get("occurred_at"),
            "rack_code": rack_code,
            "bin_mounts": normalized_mounts,
        }
        return [
            RuntimeIntent.resource_fact(
                fact_type="BIN_MOUNTED",
                payload={key: value for key, value in payload.items() if value is not None},
                idempotency_key=f"BIN_MOUNTED:{operation_key}:{rack_code}",
            )
        ]

    def _storage_retry_data(
        self,
        payload_json: Mapping[str, Any],
        rough_context: RoughSorterContext,
        *,
        operation_key: str,
        retry_event_id: str,
    ) -> dict[str, Any]:
        business_key = _business_key_from_context(rough_context)
        retry_data: dict[str, Any] = {
            "PkgID": business_key,
            "business_key": business_key,
            "six_in_one": rough_context.six_in_one,
            "measurement": rough_context.measurement,
            "wms_validation": rough_context.wms_validation,
            "rack_operation": {
                **rough_context.rack_operation,
                "operation_key": operation_key,
                "status": "ARRIVED",
            },
            "operation_key": operation_key,
            "callback_type": _non_empty_str(payload_json.get("callback_type")),
            "idempotency_key": retry_event_id,
        }
        active_bin_rack = payload_json.get("active_bin_rack")
        if isinstance(active_bin_rack, Mapping):
            retry_data["active_bin_rack"] = dict(cast("Mapping[str, Any]", active_bin_rack))
        return {key: value for key, value in retry_data.items() if value is not None}

    def _measurement_ng_intents(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        *,
        rough_context: RoughSorterContext,
        reason_code: str,
        reason_message: str,
        measurement: dict[str, Any] | None = None,
        wms_validation: dict[str, Any] | None = None,
        source_location: str | None = None,
    ) -> list[RuntimeIntent]:
        business_key = rough_context.business_key or _non_empty_str(rough_context.six_in_one.get("PkgID"))
        if not business_key:
            return self._block(
                "ROUGH_SORTER_CONTEXT_MISSING",
                "粗分机上下文缺少业务主键，无法下发 NG 搬运动作",
            )

        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=business_key,
            measurement=measurement or rough_context.measurement,
            wms_validation=wms_validation or rough_context.wms_validation,
            ng_reason={
                "reason_code": reason_code,
                "reason_message": reason_message,
            },
            phase=PHASE_NG_MOVING,
        ).model_dump(mode="json", exclude_none=True)

        intents: list[RuntimeIntent] = [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.mark_ng(
                reason_code=reason_code,
                message=reason_message,
                payload={
                    "six_in_one": rough_context.six_in_one,
                    "measurement": measurement or {},
                    "wms_validation": wms_validation or {},
                },
            ),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_MOVE_TO_NG],
                action=ACTION_MOVE_TO_NG,
                payload=build_move_to_ng_payload(
                    business_key=business_key,
                    source_location=source_location or self._command_source_location(ctx, payload_json),
                    ng_location=self._ng_location(ctx),
                    reason_code=reason_code,
                ),
            ),
        ]
        current_material_unit_id = getattr(ctx.session, "current_material_unit_id", None)
        if current_material_unit_id is not None:
            intents.insert(
                0,
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=int(current_material_unit_id),
                    status=MaterialUnitStatus.NG.value,
                ),
            )
        return intents

    def _measurement_ng_by_payload(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        *,
        rough_context: RoughSorterContext,
        measurement: dict[str, Any],
        source_location: str | None = None,
    ) -> list[RuntimeIntent]:
        return self._measurement_ng_intents(
            ctx,
            payload_json,
            rough_context=rough_context,
            reason_code=NG_REASON_MEASUREMENT_NG,
            reason_message="粗分机测量判定 NG",
            measurement=measurement,
            source_location=source_location,
        )

    @staticmethod
    def _wms_query_request(ctx: PluginContext, rough_context: RoughSorterContext) -> QueryInventoryRequest | None:
        sku = _non_empty_str(rough_context.six_in_one.get("HHPN"))
        lot_no = _non_empty_str(rough_context.six_in_one.get("LotCode"))
        pkg_id = _non_empty_str(rough_context.six_in_one.get("PkgID"))
        business_key = rough_context.business_key or pkg_id
        if not sku or not lot_no or not business_key:
            return None
        return QueryInventoryRequest(
            request_id=f"rough-sorter:inventory:{business_key}",
            trace_id=_non_empty_str(getattr(ctx, "trace_id", None)),
            sku=sku,
            lot_no=lot_no,
            warehouse_code=_non_empty_str(ctx.config.get("warehouse_code")),
            owner_code=_non_empty_str(ctx.config.get("owner_code")),
        )

    async def _query_wms_inventory(
        self,
        ctx: PluginContext,
        rough_context: RoughSorterContext,
        payload_json: dict[str, Any],
        measurement: dict[str, Any],
        *,
        source_location: str | None = None,
    ) -> tuple[QueryInventoryRequest, list[WmsInventoryItem]] | list[RuntimeIntent]:
        request = self._wms_query_request(ctx, rough_context)
        if request is None:
            return self._block(
                "ROUGH_SORTER_CONTEXT_MISSING",
                "粗分机上下文缺少 HHPN/LotCode/PkgID，无法执行 WMS 库存校验",
            )

        wms_client = getattr(getattr(ctx, "services", None), "wms_inventory_client", None)
        if wms_client is None:
            return self._block(
                "WMS_UNAVAILABLE",
                "粗分机入料成功并取得测量值后必须校验 WMS 库存，但当前运行时未注入 WMS 库存客户端",
            )

        try:
            response = await wms_client.query_inventory(request)
        except WmsBusinessRejectedError as exc:
            return self._measurement_ng_intents(
                ctx,
                payload_json,
                rough_context=rough_context,
                reason_code=NG_REASON_WMS_REJECTED,
                reason_message=exc.message,
                measurement=measurement,
                wms_validation={
                    "matched": False,
                    "request_id": request.request_id,
                    "reason_code": exc.reason_code,
                    "message": exc.message,
                    "evidence_key": exc.evidence_key,
                    "target_code": exc.target_code,
                },
                source_location=source_location,
            )
        except WmsIntegrationError as exc:
            return self._block(
                exc.reason_code or "WMS_UNAVAILABLE",
                exc.message,
                payload={
                    "request_id": request.request_id,
                    "evidence_key": exc.evidence_key,
                    "retryable": exc.retryable,
                    "target_code": exc.target_code,
                },
            )

        if not isinstance(response, QueryInventoryResponse):
            return self._block(
                "WMS_RESPONSE_INVALID",
                "WMS 库存查询返回无法解析，粗分机暂停当前物料",
                payload={"request_id": request.request_id},
            )

        return request, list(response.items)

    @on_event(EVENT_SCAN_COMPLETED)
    async def handle_scan_completed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理粗分机扫码入口事件。"""

        payload_json = inbox.payload_json or {}
        six_in_one = normalize_six_in_one_payload(payload_json)
        decision = barcode_decision_service.evaluate(six_in_one)
        six_in_one_payload = {
            field_name: value
            for field_name, value in six_in_one.model_dump().items()
            if field_name in six_in_one.BUSINESS_FIELD_NAMES and value is not None
        }

        if decision.decision == BarcodeDecisionType.OK:
            pkg_code = _non_empty_str(six_in_one_payload.get("PkgID"))
            if pkg_code is None:
                return self._block(
                    "ROUGH_SORTER_CONTEXT_MISSING",
                    "粗分机扫码成功但缺少 PkgID，无法建立料盘实体",
                )
            material_identity_key = self._material_identity_key(six_in_one_payload)
            if material_identity_key is None:
                return self._block(
                    "ROUGH_SORTER_CONTEXT_MISSING",
                    "粗分机扫码成功但缺少物料身份键，无法建立料盘实体",
                )
            context_patch = RoughSorterContext(
                six_in_one=six_in_one_payload,
                business_key=decision.business_key,
                phase=PHASE_PICK_TO_PIPELINE,
            ).model_dump(mode="json", exclude_none=True)
            return [
                RuntimeIntent.create_material_unit(
                    pkg_code=pkg_code,
                    material_identity_key=material_identity_key,
                    six_in_one=six_in_one_payload,
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                ),
                RuntimeIntent.update_context(context_patch),
                RuntimeIntent.command(
                    device_role=ACTION_TARGET_ROLES[ACTION_PICK_AND_PUT],
                    action=ACTION_PICK_AND_PUT,
                    payload=build_pick_and_put_payload(
                        business_key=decision.business_key,
                        source_location=self._scan_source_location(payload_json),
                        target_location=self._pipeline_input_location(ctx),
                        six_in_one=six_in_one,
                        trace_id=ctx.trace_id or None,
                    ),
                ),
            ]

        reason_code = decision.reason_code or "BARCODE_INVALID"
        reason_message = decision.reason_message or "扫码业务判定 NG"
        context_patch = RoughSorterContext(
            six_in_one=six_in_one_payload,
            business_key=decision.business_key,
            ng_reason={
                "reason_code": reason_code,
                "reason_message": reason_message,
            },
            phase=PHASE_NG_MOVING,
        ).model_dump(mode="json", exclude_none=True)

        intents: list[RuntimeIntent] = []
        pkg_code = _non_empty_str(six_in_one_payload.get("PkgID"))
        material_identity_key = self._material_identity_key(six_in_one_payload)
        if material_identity_key is not None and pkg_code is not None:
            intents.append(
                RuntimeIntent.create_material_unit(
                    pkg_code=pkg_code,
                    material_identity_key=material_identity_key,
                    six_in_one=six_in_one_payload,
                    status=MaterialUnitStatus.NG.value,
                )
            )
        intents.extend(
            [
                RuntimeIntent.update_context(context_patch),
                RuntimeIntent.mark_ng(
                    reason_code=reason_code,
                    message=reason_message,
                    payload={"six_in_one": six_in_one_payload},
                ),
                RuntimeIntent.command(
                    device_role=ACTION_TARGET_ROLES[ACTION_MOVE_TO_NG],
                    action=ACTION_MOVE_TO_NG,
                    payload=build_move_to_ng_payload(
                        business_key=decision.business_key,
                        source_location=self._scan_source_location(payload_json),
                        ng_location=self._ng_location(ctx),
                        reason_code=reason_code,
                    ),
                ),
            ]
        )
        return intents

    @on_event(EVENT_ROUGH_SORTER_STORAGE_RETRY)
    async def handle_storage_retry(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理货架到位后的内部重试事件，重新执行出料分配。"""

        payload_json = inbox.payload_json or {}
        payload_data = _payload_data(payload_json)
        rough_context = _session_context(ctx)
        if rough_context.phase != ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE:
            return self._block(
                "ROUGH_SORTER_PHASE_INVALID",
                f"粗分机 storage retry 处于非法阶段: {rough_context.phase}",
            )
        business_key = _business_key_from_context(rough_context)
        if business_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少业务主键，无法重试出料分配")

        rack_operation = dict(rough_context.rack_operation)
        retry_rack_operation = payload_data.get("rack_operation")
        if isinstance(retry_rack_operation, Mapping):
            rack_operation.update(cast("Mapping[str, Any]", retry_rack_operation))
        active_bin_rack = (
            dict(cast("Mapping[str, Any]", payload_data["active_bin_rack"]))
            if isinstance(payload_data.get("active_bin_rack"), Mapping)
            else rough_context.active_bin_rack
        )
        retry_context = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=business_key,
            measurement=rough_context.measurement,
            wms_validation=rough_context.wms_validation,
            active_bin_rack=active_bin_rack,
            target_bin_location=rough_context.target_bin_location,
            rack_operation=rack_operation,
            ng_reason=rough_context.ng_reason,
            phase=rough_context.phase,
        )
        return await self._storage_allocation_intents(ctx, payload_json, retry_context, business_key)

    async def on_external_http(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理 WMS/RCS 货架到位回调，先落资源事实，再创建内部重试事件。"""

        payload_json = inbox.payload_json or {}
        callback_type = _non_empty_str(payload_json.get("callback_type")) or _non_empty_str(
            payload_json.get("message_type")
        )
        if callback_type not in {"WMS_RACK_ARRIVED", "RCS_RACK_ARRIVED"}:
            return []

        rough_context = _session_context(ctx)
        operation_key = self._operation_key(payload_json, rough_context)
        session_id = self._session_id(ctx)
        business_key = _business_key_from_context(rough_context)
        if operation_key is None or session_id is None or business_key is None:
            return self._block(
                "ROUGH_SORTER_CONTEXT_MISSING",
                "粗分机货架到位回调缺少 operation_key/session_id/business_key，无法创建稳定重试事件",
            )

        callback_event_id = _non_empty_str(payload_json.get("source_event_id")) or getattr(inbox, "event_id", None)
        retry_event_id = f"rough-sorter-storage-retry:{operation_key}:{session_id}"
        source_device_code = self._resume_source_device_code(ctx, rough_context) or _non_empty_str(
            getattr(ctx, "source_device_code", None)
        )
        source_device_code = source_device_code or _non_empty_str(
            getattr(getattr(ctx, "session", None), "source_device_code", None)
        )
        source_device_code = source_device_code or _non_empty_str(
            rough_context.rack_operation.get("source_device_code")
        )
        source_device_code = source_device_code or _non_empty_str(
            getattr(getattr(ctx, "normalized_input", None), "device_code", None)
        )
        source_device_code = source_device_code or _non_empty_str(payload_json.get("device_code")) or "UNKNOWN"

        rack_arrived_payload = self._rack_arrived_payload(payload_json, rough_context)
        missing_projection_fields = [
            field
            for field in ("rack_code", "rack_kind", "position_code")
            if _non_empty_str(rack_arrived_payload.get(field)) is None
        ]
        if missing_projection_fields:
            return self._block(
                "ROUGH_SORTER_RACK_ARRIVED_FACT_INCOMPLETE",
                f"粗分机货架到位回调缺少资源投影必需字段: {', '.join(missing_projection_fields)}",
            )

        bin_mounted_intents = self._bin_mounted_intents(payload_json, operation_key)
        intents = [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload=rack_arrived_payload,
                idempotency_key=f"RACK_ARRIVED:{operation_key}",
            ),
            *bin_mounted_intents,
        ]

        return [
            *intents,
            RuntimeIntent.device_event(
                device_code=source_device_code,
                event_type=EVENT_ROUGH_SORTER_STORAGE_RETRY,
                data=self._storage_retry_data(
                    payload_json,
                    rough_context,
                    operation_key=operation_key,
                    retry_event_id=retry_event_id,
                ),
                event_id=retry_event_id,
                causation_id=callback_event_id,
                canonical_event_type=EVENT_ROUGH_SORTER_STORAGE_RETRY,
            ),
        ]

    async def _post_pick_and_put_success_intents(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        *,
        rough_context: RoughSorterContext,
        business_key: str,
    ) -> list[RuntimeIntent]:
        """处理入料成功后的测量值校验、WMS 准入与流水线推进。"""

        data = _payload_data(payload_json)
        data.update(_normalized_data(ctx))
        measurement = _measurement_payload(data)
        pipeline_input_location = self._pipeline_input_location(ctx)
        if measurement is None:
            return self._measurement_ng_intents(
                ctx,
                payload_json,
                rough_context=rough_context,
                reason_code=NG_REASON_MEASUREMENT_NG,
                reason_message="粗分机测量值缺失或无效",
                measurement=dict(data),
                source_location=pipeline_input_location,
            )

        if _measurement_is_ng(data):
            return self._measurement_ng_by_payload(
                ctx,
                payload_json,
                rough_context=rough_context,
                measurement=measurement,
                source_location=pipeline_input_location,
            )

        wms_result = await self._query_wms_inventory(
            ctx,
            rough_context,
            payload_json,
            measurement,
            source_location=pipeline_input_location,
        )
        if isinstance(wms_result, list):
            return wms_result
        request, items = wms_result
        if request.lot_no is None:
            return self._block(
                "ROUGH_SORTER_CONTEXT_MISSING",
                "粗分机上下文缺少 LotCode，无法执行 WMS 库存匹配",
            )
        matched_item = next(
            (item for item in items if _wms_item_matches(item, sku=request.sku, lot_no=request.lot_no)),
            None,
        )
        if matched_item is None:
            return self._measurement_ng_intents(
                ctx,
                payload_json,
                rough_context=rough_context,
                reason_code=NG_REASON_WMS_REJECTED,
                reason_message="WMS 未找到与粗分机物料编码和批次匹配的库存",
                measurement=measurement,
                wms_validation={
                    "matched": False,
                    "request_id": request.request_id,
                    "sku": request.sku,
                    "lot_no": request.lot_no,
                    "item_count": len(items),
                },
                source_location=pipeline_input_location,
            )

        wms_validation: dict[str, Any] = {
            "matched": True,
            "request_id": request.request_id,
            "sku": matched_item.sku,
            "lot_no": matched_item.lot_no,
        }
        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=business_key,
            measurement=measurement,
            wms_validation=wms_validation,
            phase=PHASE_MOVING_FORWARD,
        ).model_dump(mode="json", exclude_none=True)
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_MOVE_FORWARD],
                action=ACTION_MOVE_FORWARD,
                payload=build_move_forward_payload(
                    business_key=business_key,
                    source_location=pipeline_input_location,
                    target_location=self._pipeline_output_location(ctx),
                ),
            ),
        ]

    @on_command(ACTION_PICK_AND_PUT, result="SUCCESS")
    async def handle_pick_and_put_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理入料抓取完成，并在测量值与 WMS 准入通过后驱动流水线。"""

        payload_json = inbox.payload_json or {}
        rough_context = _session_context(ctx)
        business_key = _business_key_from_context(rough_context)
        if business_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少业务主键，无法继续搬运流程")

        if rough_context.phase == PHASE_NG_MOVING:
            intents: list[RuntimeIntent] = []
            current_material_unit_id = getattr(getattr(ctx, "session", None), "current_material_unit_id", None)
            if current_material_unit_id is not None:
                intents.append(
                    RuntimeIntent.update_material_unit_status(
                        material_unit_id=int(current_material_unit_id),
                        status=MaterialUnitStatus.NG.value,
                        clear_session_reference=True,
                    )
                )
            intents.append(RuntimeIntent.complete({"phase": PHASE_COMPLETED}))
            return intents

        if rough_context.phase != PHASE_PICK_TO_PIPELINE:
            return self._block(
                "ROUGH_SORTER_PHASE_INVALID",
                f"粗分机 PICK_AND_PUT 成功回调处于非法阶段: {rough_context.phase}",
            )

        return await self._post_pick_and_put_success_intents(
            ctx,
            payload_json,
            rough_context=rough_context,
            business_key=business_key,
        )

    @on_command(ACTION_MOVE_TO_NG, result="SUCCESS")
    async def handle_move_to_ng_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理 NG 搬运完成。"""

        return await self.handle_pick_and_put_success(ctx, inbox)

    @on_command(ACTION_MOVE_FORWARD, result="SUCCESS")
    async def handle_move_forward_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理流水线前进完成，并执行出料料格分配。"""

        payload_json = inbox.payload_json or {}
        rough_context = _session_context(ctx)
        business_key = _business_key_from_context(rough_context)
        if business_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少业务主键，无法执行出料分配")
        if rough_context.phase != PHASE_MOVING_FORWARD:
            return self._block(
                "ROUGH_SORTER_PHASE_INVALID",
                f"粗分机 MOVE_FORWARD 成功回调处于非法阶段: {rough_context.phase}",
            )

        return await self._storage_allocation_intents(ctx, payload_json, rough_context, business_key)

    @on_command(ACTION_PUT_TO_BIN, result="SUCCESS")
    async def handle_put_to_bin_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理出料机械臂放入料箱成功，落资源终态并完成 Session。"""

        payload_json = inbox.payload_json or {}
        rough_context = _session_context(ctx)
        business_key = _business_key_from_context(rough_context)
        if business_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少业务主键，无法完成出料入箱")
        if rough_context.phase != PHASE_PUTTING_TO_BIN:
            return self._block(
                "ROUGH_SORTER_PHASE_INVALID",
                f"粗分机 PUT_TO_BIN 成功回调处于非法阶段: {rough_context.phase}",
            )
        if not isinstance(rough_context.target_bin_location, Mapping):
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少目标料箱格位，无法完成出料入箱")

        bin_location = cast("Mapping[str, Any]", rough_context.target_bin_location)
        try:
            bin_code, bin_cell_index, bin_cell_code, material_identity_key = self._bin_location_parts(bin_location)
        except ValueError as exc:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", str(exc))
        material_identity_key = material_identity_key or self._material_identity_key(rough_context.six_in_one)
        if material_identity_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少物料身份键，无法记录入箱事实")
        current_material_unit_id = getattr(ctx.session, "current_material_unit_id", None)

        source_event_id = _non_empty_str(payload_json.get("command_code")) or f"PUT_TO_BIN:{business_key}"
        consume_payload = {
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "source_event_id": source_event_id,
        }
        resource_pkg_code = _non_empty_str(rough_context.six_in_one.get("PkgID")) or business_key
        mounted_payload: dict[str, Any] = {
            "bin_code": bin_code,
            "bin_cell_code": bin_cell_code,
            "bin_cell_index": bin_cell_index,
            "material_identity_key": material_identity_key,
            "pkg_code": resource_pkg_code,
            "material_code": _non_empty_str(rough_context.six_in_one.get("HHPN")),
            "lot_code": _non_empty_str(rough_context.six_in_one.get("LotCode")),
            "date_code": _non_empty_str(rough_context.six_in_one.get("DateCode")),
            "wms_inventory_id": _non_empty_str(rough_context.wms_validation.get("wms_inventory_id"))
            or _non_empty_str(rough_context.wms_validation.get("inventory_id")),
            "source_event_id": source_event_id,
            "reel_diameter": _non_empty_str(rough_context.measurement.get("reel_diameter")),
            "reel_thickness": _non_empty_str(rough_context.measurement.get("reel_thickness")),
            "cell_capacity_depth_mm": bin_location.get("cell_capacity_depth_mm")
            or bin_location.get("capacity_depth_mm"),
        }
        intents = [
            RuntimeIntent.resource_reservation(
                operation="CONSUME_BIN_CELL",
                payload=consume_payload,
                idempotency_key=f"CONSUME_BIN_CELL:{business_key}:{bin_code}:{bin_cell_index}",
            ),
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={key: value for key, value in mounted_payload.items() if value is not None},
                idempotency_key=f"MATERIAL_MOUNTED:{resource_pkg_code}:{bin_code}:{bin_cell_index}",
            ),
        ]
        if current_material_unit_id is None:
            # 兼容部署前已在 PUT_TO_BIN 等待回调的 Session：旧流程没有先创建料盘根实体，
            # 入箱成功后补建 STORED 实体，避免物理已入箱但 Session 被阻断。
            # 此路径预期 pkg_code 不会与活跃 Session 冲突；若冲突，_apply_create_material_unit
            # 的所有权检查会抛 ValueError 使整个回调失败（fail-loud），不静默继续。
            # pkg_code 必须用真实 PkgID（与扫码路径一致），不得回退 business_key 哈希——
            # 否则 SMT 后续用真实 PkgID claim 会创建第二行，造成同一物理盘双实体。
            legacy_pkg_code = _non_empty_str(rough_context.six_in_one.get("PkgID"))
            if legacy_pkg_code is None:
                return self._block(
                    "ROUGH_SORTER_CONTEXT_MISSING",
                    "粗分机补建料盘实体缺少 PkgID，无法用 business_key 哈希作为 pkg_code",
                )
            intents.append(
                RuntimeIntent.create_material_unit(
                    pkg_code=legacy_pkg_code,
                    material_identity_key=material_identity_key,
                    six_in_one=dict(rough_context.six_in_one),
                    status=MaterialUnitStatus.STORED.value,
                    current_location=f"{bin_code}:{bin_cell_index}",
                )
            )
        else:
            intents.append(
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=int(current_material_unit_id),
                    status=MaterialUnitStatus.STORED.value,
                    current_location=f"{bin_code}:{bin_cell_index}",
                )
            )
        intents.append(RuntimeIntent.complete({"phase": PHASE_COMPLETED}))
        return intents

    @on_command(ACTION_PUT_TO_BIN, result="FAILED")
    async def handle_put_to_bin_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理出料放置失败：保留预约并进入 Hold，等待人工确认物理状态。"""

        return self._handling_failed_block(ctx, inbox)

    async def _storage_allocation_intents(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        rough_context: RoughSorterContext,
        business_key: str,
    ) -> list[RuntimeIntent]:
        allocator = getattr(getattr(ctx, "services", None), "bin_allocator", None)
        if allocator is None:
            return self._block("ROUGH_SORTER_ALLOCATOR_UNAVAILABLE", "粗分机出料分配缺少 bin_allocator 服务")

        allocation_context = await self._allocation_context(ctx, rough_context)
        active_bin_rack = allocation_context.get("active_bin_rack")
        allocation_rough_context = rough_context
        if isinstance(active_bin_rack, Mapping) and rough_context.active_bin_rack is None:
            allocation_rough_context = rough_context.model_copy(
                update={"active_bin_rack": dict(cast("Mapping[str, Any]", active_bin_rack))}
            )
        allocation_barcode = _non_empty_str(rough_context.six_in_one.get("PkgID")) or business_key
        decision = allocator.plan_allocation(allocation_barcode, context=allocation_context)
        if inspect.isawaitable(decision):
            decision = await decision
        if decision is None:
            return self._block("ROUGH_SORTER_ALLOCATION_BLOCKED", "粗分机出料分配未返回可执行决策")

        decision_kind = _non_empty_str(getattr(decision, "kind", None))
        if decision_kind == "ALLOCATED":
            return self._allocated_bin_intents(
                ctx,
                payload_json,
                allocation_rough_context,
                business_key,
                allocation_barcode,
                decision,
            )
        if decision_kind == "RACK_OPERATION_REQUIRED":
            return self._rack_operation_required_intents(ctx, payload_json, rough_context, business_key, decision)
        if decision_kind == "BLOCKED":
            return self._block(
                _non_empty_str(getattr(decision, "reason_code", None)) or "ROUGH_SORTER_ALLOCATION_BLOCKED",
                _non_empty_str(getattr(decision, "message", None)) or "粗分机出料分配被资源域阻塞",
            )
        return self._block(
            "ROUGH_SORTER_ALLOCATION_DECISION_INVALID",
            f"粗分机出料分配返回未知决策: {decision_kind}",
        )

    def _allocated_bin_intents(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        rough_context: RoughSorterContext,
        business_key: str,
        resource_pkg_code: str,
        decision: Any,
    ) -> list[RuntimeIntent]:
        bin_location = getattr(decision, "bin_location", None)
        if not isinstance(bin_location, Mapping):
            return self._block("ROUGH_SORTER_ALLOCATION_DECISION_INVALID", "ALLOCATED 决策缺少 bin_location")
        bin_location_map = cast("Mapping[str, Any]", bin_location)
        try:
            bin_code, bin_cell_index, bin_cell_code, material_identity_key = self._bin_location_parts(bin_location_map)
        except ValueError as exc:
            return self._block("ROUGH_SORTER_ALLOCATION_DECISION_INVALID", str(exc))

        claim_payload: dict[str, Any] = {
            "pkg_code": resource_pkg_code,
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
        }
        if bin_cell_code is not None:
            claim_payload["bin_cell_code"] = bin_cell_code
        if material_identity_key is not None:
            claim_payload["material_identity_key"] = material_identity_key

        target_bin_location: dict[str, Any] = dict(bin_location_map)
        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=business_key,
            measurement=rough_context.measurement,
            wms_validation=rough_context.wms_validation,
            active_bin_rack=rough_context.active_bin_rack,
            target_bin_location=target_bin_location,
            phase=PHASE_PUTTING_TO_BIN,
        ).model_dump(mode="json", exclude_none=True)
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.resource_reservation(
                operation="CLAIM_BIN_CELL",
                payload=claim_payload,
                idempotency_key=f"CLAIM_BIN_CELL:{resource_pkg_code}:{bin_code}:{bin_cell_index}",
            ),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_PUT_TO_BIN],
                action=ACTION_PUT_TO_BIN,
                payload=build_put_to_bin_payload(
                    business_key=business_key,
                    source_location=self._command_source_location(ctx, payload_json),
                    bin_location=bin_cell_code or f"{bin_code}:{bin_cell_index}",
                ),
            ),
        ]

    def _rack_operation_required_intents(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        rough_context: RoughSorterContext,
        business_key: str,
        decision: Any,
    ) -> list[RuntimeIntent]:
        del payload_json, business_key
        rack_operation_request = getattr(decision, "rack_operation_request", None)
        if rack_operation_request is None:
            return self._block("ROUGH_SORTER_ALLOCATION_DECISION_INVALID", "RACK_OPERATION_REQUIRED 缺少请求合同")

        operation_key = _non_empty_str(getattr(rack_operation_request, "operation_key", None))
        target_code = _non_empty_str(getattr(rack_operation_request, "target_code", None))
        raw_payload = getattr(rack_operation_request, "payload", None)
        if operation_key is None or target_code is None or not isinstance(raw_payload, Mapping):
            return self._block("ROUGH_SORTER_ALLOCATION_DECISION_INVALID", "货架 operation 请求缺少关键字段")

        raw_payload_map = cast("Mapping[str, Any]", raw_payload)
        try:
            operation_payload = self._rack_operation_payload(ctx, raw_payload_map)
        except ValueError as exc:
            return self._block("ROUGH_SORTER_ALLOCATION_DECISION_INVALID", str(exc))
        operation_type = _non_empty_str(operation_payload.get("operation_type")) or "REPLACE_CLASSIFIER_WORK_RACK"
        timeout_seconds = int(getattr(rack_operation_request, "timeout_seconds", 1800) or 1800)
        source_device_code = self._command_source_location(ctx, {})
        target_position_code = (
            _non_empty_str(operation_payload.get("target_position_code"))
            or _non_empty_str(operation_payload.get("work_position_code"))
            or _non_empty_str(operation_payload.get("position_code"))
        )
        rack_kind = _non_empty_str(operation_payload.get("new_rack_kind")) or _non_empty_str(
            operation_payload.get("rack_kind")
        )
        rack_tasks = operation_payload.get("rack_tasks")
        if isinstance(rack_tasks, list):
            for task in cast("list[Any]", rack_tasks):
                if not isinstance(task, Mapping):
                    continue
                task_map = cast("Mapping[str, Any]", task)
                task_type = _non_empty_str(task_map.get("task_type"))
                if task_type != "ALLOCATE_AND_MOVE_RACK":
                    continue
                target_position_code = target_position_code or _non_empty_str(task_map.get("target_position_code"))
                rack_kind = rack_kind or _non_empty_str(task_map.get("rack_kind"))
        rack_operation_context: dict[str, Any] = {
            "operation_key": operation_key,
            "operation_type": operation_type,
            "target_code": target_code,
            "status": "REQUESTED",
            "reason_code": _non_empty_str(getattr(decision, "reason_code", None)),
            "message": _non_empty_str(getattr(decision, "message", None)),
        }
        if target_position_code is not None:
            rack_operation_context["target_position_code"] = target_position_code
            rack_operation_context["work_position_code"] = target_position_code
        if rack_kind is not None:
            rack_operation_context["rack_kind"] = rack_kind
        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=_business_key_from_context(rough_context),
            measurement=rough_context.measurement,
            wms_validation=rough_context.wms_validation,
            rack_operation=rack_operation_context,
            phase=ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE,
        ).model_dump(mode="json", exclude_none=True)
        context_patch["resume_source_device_code"] = source_device_code
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.rack_operation_request(
                operation_type=operation_type,
                operation_key=operation_key,
                target_code=target_code,
                payload=operation_payload,
                timeout_seconds=timeout_seconds,
            ),
        ]

    @on_command(ACTION_PICK_AND_PUT, result="FAILED")
    async def handle_pick_and_put_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """搬运失败默认进入 Hold，不做业务 NG 判定。"""

        return self._handling_failed_block(ctx, inbox)

    @on_command(ACTION_MOVE_TO_NG, result="FAILED")
    async def handle_move_to_ng_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """NG 搬运失败默认进入 Hold，不做业务 NG 判定。"""

        return self._handling_failed_block(ctx, inbox)

    @on_command(ACTION_MOVE_FORWARD, result="FAILED")
    async def handle_move_forward_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """流水线前进失败默认进入 Hold，不做业务 NG 判定。"""

        return self._handling_failed_block(ctx, inbox)


rough_sorter_plugin = RoughSorterPlugin()

__all__ = ["RoughSorterPlugin", "rough_sorter_plugin"]
