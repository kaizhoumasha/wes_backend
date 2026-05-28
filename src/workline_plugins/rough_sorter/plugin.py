"""粗分机工作线插件。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from src.app.wms_integration.models import QueryInventoryRequest, QueryInventoryResponse, WmsInventoryItem
from src.app.wms_integration.services.exceptions import WmsBusinessRejectedError, WmsIntegrationError
from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    ACTION_TARGET_ROLES,
    EVENT_SCAN_COMPLETED,
    NG_REASON_BARCODE_INCOMPLETE,
    NG_REASON_BARCODE_INVALID,
    NG_REASON_BARCODE_RULE_NG,
    NG_REASON_MEASUREMENT_NG,
    NG_REASON_WMS_REJECTED,
    PHASE_COMPLETED,
    PHASE_MEASURING,
    PHASE_MOVING_FORWARD,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
    PHASE_WAITING_RACK,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
    build_measurement_reel_payload,
    build_move_forward_payload,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
    build_put_to_bin_payload,
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

DEFAULT_NG_LOCATION = "NG-01"
DEFAULT_PIPELINE_INPUT_LOCATION = "PIPELINE-IN-01"
DEFAULT_PIPELINE_OUTPUT_LOCATION = "PIPELINE-OUT-01"
MEASUREMENT_NG_ERROR_CODES = frozenset({"INSPECTION_SIZE_NG", "INSPECTION_THICKNESS_NG"})


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
    if reel_diameter is None or reel_thickness is None:
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


class RoughSorterPlugin(WorklinePlugin):
    """粗分机插件。"""

    plugin_key = ROUGH_SORTER_PLUGIN_KEY
    contract_version = ROUGH_SORTER_CONTRACT_VERSION

    manifest = WorklinePluginManifest(
        plugin_key=ROUGH_SORTER_PLUGIN_KEY,
        contract_version=ROUGH_SORTER_CONTRACT_VERSION,
        required_device_roles=(
            DeviceRoleRequirement(role=ROLE_INPUT_ARM, min_count=1),
            DeviceRoleRequirement(role=ROLE_CONVEYOR, min_count=1),
            DeviceRoleRequirement(role=ROLE_OUTPUT_ARM, min_count=1),
        ),
        business_key_resolver=resolve_rough_sorter_business_key,
        result_classifier=classify_rough_sorter_result,
        context_model=RoughSorterContext,
        supported_events=frozenset({EVENT_SCAN_COMPLETED}),
        supported_commands=frozenset(ACTION_TARGET_ROLES),
        command_target_roles=ACTION_TARGET_ROLES,
        ng_reason_catalog=(
            _ng_reason(NG_REASON_BARCODE_INVALID, "条码无效"),
            _ng_reason(NG_REASON_BARCODE_INCOMPLETE, "条码不完整"),
            _ng_reason(NG_REASON_BARCODE_RULE_NG, "条码规则判定 NG"),
            _ng_reason(NG_REASON_MEASUREMENT_NG, "测量业务判定 NG"),
            _ng_reason(NG_REASON_WMS_REJECTED, "WMS 库存校验拒绝"),
        ),
    )

    @staticmethod
    def _scan_source_location(payload_json: dict[str, Any]) -> str:
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

    async def _allocation_context(self, ctx: PluginContext, rough_context: RoughSorterContext) -> dict[str, Any]:
        context: dict[str, Any] = {
            "six_in_one": rough_context.six_in_one,
            "measurement": rough_context.measurement,
            "wms_validation": rough_context.wms_validation,
            "rack_operation": rough_context.rack_operation,
            "config": dict(ctx.config),
            "trace_id": _non_empty_str(getattr(ctx, "trace_id", None)),
        }
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
    def _rack_tasks_from_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
        actions = payload.get("actions")
        if not isinstance(actions, list):
            return []
        action_values = cast("list[Any]", actions)
        tasks: list[dict[str, Any]] = []
        for index, action in enumerate(action_values, start=1):
            if not isinstance(action, str) or not action:
                continue
            task: dict[str, Any] = {
                "sequence_no": index,
                "task_type": action,
                "rack_kind": payload.get("new_rack_kind") or "SINGLE_LAYER",
                "target_position_code": payload.get("work_position_code") or "SINGLE_LAYER_A",
                "target_position_role": payload.get("target_position_role") or "SMT_CLASSIFIER_SINGLE_RACK_WORK",
            }
            rack_code = payload.get("single_layer_rack_code") or payload.get("single_layer_rack_id")
            if rack_code is not None and action == "MOVE_OUT_ACTIVE_RACK":
                task["rack_code"] = rack_code
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

        return [
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
                    source_location=self._command_source_location(ctx, payload_json),
                    ng_location=self._ng_location(ctx),
                    reason_code=reason_code,
                ),
            ),
        ]

    def _measurement_ng_by_payload(
        self,
        ctx: PluginContext,
        payload_json: dict[str, Any],
        *,
        rough_context: RoughSorterContext,
        measurement: dict[str, Any],
    ) -> list[RuntimeIntent]:
        return self._measurement_ng_intents(
            ctx,
            payload_json,
            rough_context=rough_context,
            reason_code=NG_REASON_MEASUREMENT_NG,
            reason_message="粗分机测量判定 NG",
            measurement=measurement,
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
                "粗分机测量成功后必须校验 WMS 库存，但当前运行时未注入 WMS 库存客户端",
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
            context_patch = RoughSorterContext(
                six_in_one=six_in_one_payload,
                business_key=decision.business_key,
                phase=PHASE_MEASURING,
            ).model_dump(mode="json", exclude_none=True)
            return [
                RuntimeIntent.update_context(context_patch),
                RuntimeIntent.command(
                    device_role=ACTION_TARGET_ROLES[ACTION_MEASUREMENT_REEL],
                    action=ACTION_MEASUREMENT_REEL,
                    payload=build_measurement_reel_payload(six_in_one, trace_id=ctx.trace_id or None),
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

        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.mark_ng(
                reason_code=reason_code,
                message=reason_message,
                payload={"six_in_one": six_in_one_payload},
            ),
            RuntimeIntent.command(
                device_role=ROLE_OUTPUT_ARM,
                action=ACTION_MOVE_TO_NG,
                payload=build_move_to_ng_payload(
                    business_key=decision.business_key,
                    source_location=self._scan_source_location(payload_json),
                    ng_location=self._ng_location(ctx),
                    reason_code=reason_code,
                ),
            ),
        ]

    @on_command(ACTION_MEASUREMENT_REEL, result="SUCCESS")
    async def handle_measurement_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理测量成功结果，并在 WMS 库存匹配后进入入线抓取。"""

        payload_json = inbox.payload_json or {}
        data = _payload_data(payload_json)
        data.update(_normalized_data(ctx))
        measurement = _measurement_payload(data)
        if measurement is None:
            return self._block(
                "ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID",
                "粗分机测量成功回调缺少有效 reel_diameter/reel_thickness",
            )

        rough_context = _session_context(ctx)
        if _measurement_is_ng(data):
            return self._measurement_ng_by_payload(
                ctx,
                payload_json,
                rough_context=rough_context,
                measurement=measurement,
            )

        wms_result = await self._query_wms_inventory(ctx, rough_context, payload_json, measurement)
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
            )

        business_key = rough_context.business_key or _non_empty_str(rough_context.six_in_one.get("PkgID"))
        if not business_key:
            return self._block(
                "ROUGH_SORTER_CONTEXT_MISSING",
                "粗分机上下文缺少业务主键，无法下发入线抓取动作",
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
            phase=PHASE_PICK_TO_PIPELINE,
        ).model_dump(mode="json", exclude_none=True)
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_PICK_AND_PUT],
                action=ACTION_PICK_AND_PUT,
                payload=build_pick_and_put_payload(
                    business_key=business_key,
                    source_location=self._command_source_location(ctx, payload_json),
                    target_location=self._pipeline_input_location(ctx),
                ),
            ),
        ]

    @on_command(ACTION_MEASUREMENT_REEL, result="FAILED")
    async def handle_measurement_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理测量失败：业务 NG 入 NG 线，硬件/通信失败进入人工 Hold。"""

        payload_json = inbox.payload_json or {}
        error_detail = _payload_data(payload_json)
        payload_error_detail = payload_json.get("error_detail")
        if isinstance(payload_error_detail, dict):
            error_detail.update(cast("dict[str, Any]", payload_error_detail))
        error_detail.update(_normalized_error_detail(ctx))
        error_code = str(error_detail.get("error_code") or error_detail.get("code") or "").upper()
        error_message = _non_empty_str(error_detail.get("error_message")) or _non_empty_str(error_detail.get("message"))

        if error_code in MEASUREMENT_NG_ERROR_CODES:
            return self._measurement_ng_intents(
                ctx,
                payload_json,
                rough_context=_session_context(ctx),
                reason_code=NG_REASON_MEASUREMENT_NG,
                reason_message=error_message or "粗分机测量判定 NG",
                measurement={"error_detail": error_detail},
            )

        return self._block(
            "ROUGH_SORTER_MEASUREMENT_FAILED",
            error_message or "粗分机测量命令失败，需人工确认设备状态",
            payload={"error_detail": error_detail},
        )

    @on_command(ACTION_PICK_AND_PUT, result="SUCCESS")
    async def handle_pick_and_put_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """处理入料抓取/NG 搬运完成。"""

        payload_json = inbox.payload_json or {}
        rough_context = _session_context(ctx)
        business_key = _business_key_from_context(rough_context)
        if business_key is None:
            return self._block("ROUGH_SORTER_CONTEXT_MISSING", "粗分机上下文缺少业务主键，无法继续搬运流程")

        if rough_context.phase == PHASE_NG_MOVING:
            return [RuntimeIntent.complete({"phase": PHASE_COMPLETED})]

        if rough_context.phase != PHASE_PICK_TO_PIPELINE:
            return self._block(
                "ROUGH_SORTER_PHASE_INVALID",
                f"粗分机 PICK_AND_PUT 成功回调处于非法阶段: {rough_context.phase}",
            )

        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=business_key,
            measurement=rough_context.measurement,
            wms_validation=rough_context.wms_validation,
            phase=PHASE_MOVING_FORWARD,
        ).model_dump(mode="json", exclude_none=True)
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.command(
                device_role=ACTION_TARGET_ROLES[ACTION_MOVE_FORWARD],
                action=ACTION_MOVE_FORWARD,
                payload=build_move_forward_payload(
                    business_key=business_key,
                    source_location=self._command_source_location(ctx, payload_json),
                    target_location=self._pipeline_output_location(ctx),
                ),
            ),
        ]

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

        allocator = getattr(getattr(ctx, "services", None), "bin_allocator", None)
        if allocator is None:
            return self._block("ROUGH_SORTER_ALLOCATOR_UNAVAILABLE", "粗分机出料分配缺少 bin_allocator 服务")

        allocation_context = await self._allocation_context(ctx, rough_context)
        decision = allocator.plan_allocation(business_key, context=allocation_context)
        if inspect.isawaitable(decision):
            decision = await decision
        if decision is None:
            return self._block("ROUGH_SORTER_ALLOCATION_BLOCKED", "粗分机出料分配未返回可执行决策")

        decision_kind = _non_empty_str(getattr(decision, "kind", None))
        if decision_kind == "ALLOCATED":
            return self._allocated_bin_intents(ctx, payload_json, rough_context, business_key, decision)
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
            "pkg_code": business_key,
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
            target_bin_location=target_bin_location,
            phase=PHASE_PUTTING_TO_BIN,
        ).model_dump(mode="json", exclude_none=True)
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.resource_reservation(
                operation="CLAIM_BIN_CELL",
                payload=claim_payload,
                idempotency_key=f"CLAIM_BIN_CELL:{business_key}:{bin_code}:{bin_cell_index}",
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
        operation_payload = self._rack_operation_payload(ctx, raw_payload_map)
        operation_type = _non_empty_str(operation_payload.get("operation_type")) or "REPLACE_CLASSIFIER_WORK_RACK"
        timeout_seconds = int(getattr(rack_operation_request, "timeout_seconds", 1800) or 1800)
        source_device_code = self._command_source_location(ctx, {})
        rack_operation_context: dict[str, Any] = {
            "operation_key": operation_key,
            "operation_type": operation_type,
            "target_code": target_code,
            "status": "REQUESTED",
            "reason_code": _non_empty_str(getattr(decision, "reason_code", None)),
            "message": _non_empty_str(getattr(decision, "message", None)),
        }
        context_patch = RoughSorterContext(
            six_in_one=rough_context.six_in_one,
            business_key=_business_key_from_context(rough_context),
            measurement=rough_context.measurement,
            wms_validation=rough_context.wms_validation,
            rack_operation=rack_operation_context,
            phase=PHASE_WAITING_RACK,
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
