"""粗分机工作线插件。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from src.app.wms_integration.models import QueryInventoryRequest, QueryInventoryResponse, WmsInventoryItem
from src.app.wms_integration.services.exceptions import WmsBusinessRejectedError, WmsIntegrationError
from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_TARGET_ROLES,
    EVENT_SCAN_COMPLETED,
    NG_REASON_BARCODE_INCOMPLETE,
    NG_REASON_BARCODE_INVALID,
    NG_REASON_BARCODE_RULE_NG,
    NG_REASON_MEASUREMENT_NG,
    NG_REASON_WMS_REJECTED,
    PHASE_MEASURING,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
    build_measurement_reel_payload,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
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


rough_sorter_plugin = RoughSorterPlugin()

__all__ = ["RoughSorterPlugin", "rough_sorter_plugin"]
