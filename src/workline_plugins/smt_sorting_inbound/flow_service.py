"""SMT 分拣入库 P0 业务 flow 服务。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from src.app.resource.models import RackKind
from src.app.resource.services.material_identity import material_identity_keys_match
from src.app.resource.services.smt_bin_cell_allocation_policy import SmtBinCellAllocationPolicy
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    NG_REASON_LOCAL_SORTING_NG,
    PHASE_COMPLETED,
    PHASE_WAITING_NG_PLACE,
    PHASE_WAITING_SCAN,
    PHASE_WAITING_SOURCE_PICK,
    PHASE_WAITING_TARGET_BIN_SWITCH,
    PHASE_WAITING_TARGET_PLACE,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
)
from src.workline_plugins.smt_sorting_inbound.context import SortingInboundContext, SortingInboundContextError
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

_SCAN_PLATFORM_EMPTY = "EMPTY"
_SCAN_PLATFORM_OCCUPIED = "OCCUPIED"
_TARGET_STATION_CODE = "TARGET_STATION"
_TARGET_STATION_RACK_KIND = RackKind.FIVE_LAYER


def _dict_copy(value: Any) -> dict[str, Any]:
    return dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else {}


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload_data(payload_json: Mapping[str, Any]) -> dict[str, Any]:
    data = payload_json.get("data")
    return dict(cast("Mapping[str, Any]", data)) if isinstance(data, Mapping) else {}


def _payload_text(payload_json: Mapping[str, Any], data: Mapping[str, Any], *field_names: str) -> str | None:
    for field_name in field_names:
        value = _non_empty_str(data.get(field_name))
        if value is not None:
            return value
        value = _non_empty_str(payload_json.get(field_name))
        if value is not None:
            return value
    return None


class SmtSortingInboundFlowService:
    """分拣入库插件 P0 flow 编排。"""

    def __init__(
        self,
        *,
        allocation_policy: Any | None = None,
        active_snapshot_provider: Any | None = None,
    ) -> None:
        self._allocation_policy = allocation_policy or SmtBinCellAllocationPolicy()
        self._active_snapshot_provider = active_snapshot_provider

    async def handle_source_pick_requested(self, _ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """内部 source-pick 请求转为源端机械臂 command intent。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        data = _payload_data(payload_json)
        command_payload = self._source_pick_request_command_payload(payload_json, data, inbox)
        missing_fields = [
            field_name
            for field_name in (
                "handoff_demand_id",
                "handoff_source_item_id",
                "claim_attempt_no",
                "source_pick_inbox_id",
                "source_pick_request_event_id",
                "bin_code",
                "bin_cell_index",
                "material_identity_key",
                "reel_thickness",
            )
            if command_payload.get(field_name) is None
        ]
        if missing_fields:
            return self._block(
                "PLUGIN_CONTRACT_INVALID",
                "SORTING_SOURCE_PICK_REQUESTED payload 缺少生成首盘取盘命令所需字段",
                payload={
                    "missing_fields": missing_fields,
                    "event_id": payload_json.get("event_id"),
                    "inbox_id": getattr(inbox, "id", None),
                },
            )

        return [
            RuntimeIntent.command(
                device_role=ROLE_SORTING_SOURCE_ARM,
                action=COMMAND_SOURCE_PICK,
                payload=command_payload,
            )
        ]

    async def handle_source_pick_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """源端取盘成功后出账源格，并打开当前物料上下文。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
        except SortingInboundContextError as exc:
            return self._block("SORTING_CONTEXT_INVALID", str(exc))

        sorting = sorting_context.sorting
        if _dict_copy(sorting.get("current_material")):
            return self._block("SORTING_CURRENT_MATERIAL_OPEN", "已有未关闭的当前物料，拒绝重复源格出账")

        stations = _dict_copy(sorting.get("stations"))
        scan_platform = _non_empty_str(stations.get("scan_platform"))
        if scan_platform not in {None, _SCAN_PLATFORM_EMPTY}:
            return self._block("SORTING_SCAN_PLATFORM_OCCUPIED", "扫码平台非空，拒绝源端继续取盘")

        data = _payload_data(payload_json)
        source_event_id = self._source_event_id(payload_json, inbox)
        source_payload = self._source_pick_payload(payload_json, data, source_event_id)
        missing_fields = [
            field_name
            for field_name in ("bin_code", "bin_cell_index", "material_identity_key", "reel_thickness")
            if source_payload.get(field_name) is None
        ]
        if missing_fields:
            return self._block(
                "SORTING_SOURCE_PICK_PAYLOAD_INVALID",
                f"源端取盘成功回调缺少字段: {', '.join(missing_fields)}",
                payload={"missing_fields": missing_fields, "source_event_id": source_event_id},
            )

        context_patch = self._source_pick_context_patch(ctx, source_payload)
        fact_payload = {key: value for key, value in source_payload.items() if value is not None}
        idempotency_key = (
            f"MATERIAL_UNMOUNTED:{source_event_id}:{fact_payload.get('pkg_code') or fact_payload['material_identity_key']}:"
            f"{fact_payload['bin_code']}:{fact_payload['bin_cell_index']}"
        )
        return [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload=fact_payload,
                idempotency_key=idempotency_key,
            ),
            RuntimeIntent.update_context(context_patch),
        ]

    async def handle_source_pick_failed(self, _ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """源端取盘失败后，保留设备失败证据并停止自动流转。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        data = _payload_data(payload_json)
        return self._block(
            "SORTING_SOURCE_PICK_FAILED",
            _payload_text(payload_json, data, "error_message", "message") or "源端取盘失败，需人工确认",
            payload={
                "command_code": payload_json.get("command_code"),
                "source_position_code": _payload_text(payload_json, data, "source_position_code", "bin_code"),
                "source_cell_code": _payload_text(payload_json, data, "source_cell_code", "bin_cell_code"),
                "error_detail": data,
            },
        )

    async def handle_working_bin_scan(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """扫码平台完成物料识别后，分配目标料格并写入 pending placement。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
        except SortingInboundContextError as exc:
            return self._block("SORTING_CONTEXT_INVALID", str(exc))

        current_material = _dict_copy(sorting_context.sorting.get("current_material"))
        if not current_material:
            return self._block("SORTING_CURRENT_MATERIAL_MISSING", "扫码完成时缺少当前物料上下文")

        data = _payload_data(payload_json)
        expected_identity_key = _non_empty_str(current_material.get("material_identity_key"))
        actual_identity_key = _payload_text(payload_json, data, "material_identity_key") or expected_identity_key
        if expected_identity_key is None or actual_identity_key is None:
            return self._block("SORTING_MATERIAL_IDENTITY_MISSING", "扫码完成时缺少物料身份键")
        if not material_identity_keys_match(expected_identity_key, actual_identity_key):
            context_patch = self._local_ng_context_patch(
                ctx,
                actual_identity_key=actual_identity_key,
                reason_message="扫码物料身份与源格出账物料身份不一致",
                evidence={
                    "expected_material_identity_key": expected_identity_key,
                    "actual_material_identity_key": actual_identity_key,
                    "scan_event_payload": payload_json,
                },
            )
            return [
                RuntimeIntent.update_context(context_patch),
                RuntimeIntent.mark_ng(
                    reason_code=NG_REASON_LOCAL_SORTING_NG,
                    message="扫码物料身份与源格出账物料身份不一致",
                    payload=context_patch,
                ),
                RuntimeIntent.command(
                    device_role=ROLE_SORTING_TARGET_ARM,
                    action=COMMAND_NG_PLACE,
                    payload=self._ng_place_command_payload(context_patch["sorting"]),
                ),
            ]

        expected_thickness = _non_empty_str(current_material.get("reel_thickness_mm"))
        scan_thickness_was_provided = _payload_has_any(payload_json, data, "reel_thickness", "reel_thickness_mm")
        scan_thickness = _payload_text(payload_json, data, "reel_thickness", "reel_thickness_mm")
        actual_thickness = scan_thickness if scan_thickness_was_provided else expected_thickness
        actual_thickness_text = _positive_decimal_text(actual_thickness)
        if actual_thickness_text is None:
            return self._block(
                "SORTING_REEL_THICKNESS_INVALID",
                "扫码完成时料盘厚度缺失或不是正 Decimal",
                payload={"reel_thickness": actual_thickness},
            )
        actual_thickness = actual_thickness_text

        active_snapshot, target_block = await self._target_station_ready_snapshot(ctx, sorting_context.sorting)
        if target_block is not None:
            return target_block

        allocation = self._allocation_policy.allocate(
            active_snapshot=cast("dict[str, Any]", active_snapshot),
            material_identity_key=actual_identity_key,
            reel_thickness_mm=actual_thickness,
        )
        if getattr(allocation, "kind", None) != "ALLOCATED":
            return self._allocation_rejection_intents(ctx, current_material, actual_thickness, allocation)

        context_patch = self._allocation_context_patch(
            ctx,
            actual_identity_key=actual_identity_key,
            actual_thickness=actual_thickness,
            expected_thickness=expected_thickness,
            allocation=allocation,
        )
        return [
            RuntimeIntent.update_context(context_patch),
            RuntimeIntent.command(
                device_role=ROLE_SORTING_TARGET_ARM,
                action=COMMAND_TARGET_PLACE,
                payload=self._target_place_command_payload(context_patch["sorting"]),
            ),
        ]

    async def handle_target_place_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """目标端放盘成功后，投影入账并释放扫码平台。"""

        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
        except SortingInboundContextError as exc:
            return self._block("SORTING_CONTEXT_INVALID", str(exc))

        pending_target = _dict_copy(sorting_context.sorting.get("pending_target_placement"))
        current_material = _dict_copy(sorting_context.sorting.get("current_material"))
        if not pending_target:
            return self._block("SORTING_PENDING_TARGET_MISSING", "目标放盘成功回调缺少 pending target placement")
        if not current_material:
            return self._block("SORTING_CURRENT_MATERIAL_MISSING", "目标放盘成功回调缺少当前物料上下文")

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        source_event_id = self._source_event_id(payload_json, inbox)
        mounted_payload = self._target_mounted_payload(
            pending_target=pending_target,
            current_material=current_material,
            source_event_id=source_event_id,
        )
        idempotency_key = (
            f"MATERIAL_MOUNTED:{source_event_id}:{mounted_payload.get('pkg_code') or mounted_payload['material_identity_key']}:"
            f"{mounted_payload['bin_code']}:{mounted_payload['bin_cell_index']}"
        )
        return [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={key: value for key, value in mounted_payload.items() if value is not None},
                idempotency_key=idempotency_key,
            ),
            RuntimeIntent.update_context(self._target_success_context_patch(ctx)),
        ]

    async def handle_target_place_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """目标端放盘失败后，按物理位置确定性进入人工处理或对账。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        data = _payload_data(payload_json)
        target_location_known = data.get("target_location_known")
        if target_location_known is False:
            return self._block(
                "SORTING_TARGET_PLACE_LOCATION_UNKNOWN",
                "目标放盘失败且物理位置未知，需进入对账处理",
                payload={"target_location_known": False, "error_detail": data},
            )

        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
            pending_target = _dict_copy(sorting_context.sorting.get("pending_target_placement"))
        except SortingInboundContextError:
            pending_target = {}
        location_known = bool(
            (
                _payload_text(payload_json, data, "target_bin_code")
                and _payload_text(payload_json, data, "target_cell_code")
            )
            or pending_target
        )
        if not location_known:
            return self._block(
                "SORTING_TARGET_PLACE_LOCATION_UNKNOWN",
                "目标放盘失败且缺少可确认位置，需进入对账处理",
                payload={"target_location_known": False, "error_detail": data},
            )

        return self._block(
            "SORTING_TARGET_PLACE_FAILED",
            _payload_text(payload_json, data, "error_message", "message") or "目标放盘失败，需人工确认",
            payload={
                "target_location_known": True,
                "pending_target_placement": pending_target,
                "error_detail": data,
            },
        )

    async def handle_ng_place_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """本地 NG 放置成功后，关闭当前物料并保留 NG 回流上下文。"""

        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
        except SortingInboundContextError as exc:
            return self._block("SORTING_CONTEXT_INVALID", str(exc))
        current_material = _dict_copy(sorting_context.sorting.get("current_material"))
        if not current_material:
            return self._block("SORTING_CURRENT_MATERIAL_MISSING", "NG 放置成功回调缺少当前物料上下文")

        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        scratch_context = SortingInboundContext.load_for_automatic(scratch_session)
        scratch_context.clear_pending_target_placement()
        scratch_context.close_current_material()
        scratch_context.set_station_state(scan_platform=_SCAN_PLATFORM_EMPTY, business_phase=PHASE_WAITING_SOURCE_PICK)
        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        patch = self._ng_root_patch(
            sorting=_dict_copy(scratch_session.context_json.get("sorting")),
            reason_message="本地 NG 放置成功",
            evidence={"ng_command_payload": payload_json, "current_material": current_material},
        )
        return [RuntimeIntent.update_context(patch)]

    async def handle_ng_place_failed(self, _ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """本地 NG 放置失败后，按物理位置确定性进入人工处理或对账。"""

        payload_json = _dict_copy(getattr(inbox, "payload_json", None))
        data = _payload_data(payload_json)
        if data.get("ng_location_known") is False:
            return self._block(
                "SORTING_NG_PLACE_LOCATION_UNKNOWN",
                "NG 放置失败且物理位置未知，需进入对账处理",
                payload={"ng_location_known": False, "error_detail": data},
            )

        location_known = bool(_payload_text(payload_json, data, "ng_location_code", "ng_location"))
        if not location_known:
            return self._block(
                "SORTING_NG_PLACE_LOCATION_UNKNOWN",
                "NG 放置失败且缺少可确认 NG 位置，需进入对账处理",
                payload={"ng_location_known": False, "error_detail": data},
            )

        return self._block(
            "SORTING_NG_PLACE_FAILED",
            _payload_text(payload_json, data, "error_message", "message") or "NG 放置失败，需人工确认",
            payload={"ng_location_known": True, "error_detail": data},
        )

    async def handle_session_complete_requested(self, ctx: PluginContext, _inbox: WorklineInbox) -> list[RuntimeIntent]:
        """Session 完成前确认所有在途物料均已闭环。"""

        try:
            sorting_context = SortingInboundContext.load_for_automatic(getattr(ctx, "session", None))
        except SortingInboundContextError as exc:
            return self._block("SORTING_CONTEXT_INVALID", str(exc))
        if _dict_copy(sorting_context.sorting.get("pending_target_placement")):
            return self._block("SORTING_PENDING_TARGET_OPEN", "目标放盘尚未闭环，拒绝完成 Session")
        if _dict_copy(sorting_context.sorting.get("current_material")):
            return self._block("SORTING_CURRENT_MATERIAL_OPEN", "当前物料尚未关闭，拒绝完成 Session")

        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        scratch_context = SortingInboundContext.load_for_automatic(scratch_session)
        scratch_context.set_station_state(business_phase=PHASE_COMPLETED)
        return [RuntimeIntent.complete({"sorting": _dict_copy(scratch_session.context_json.get("sorting"))})]

    def _source_pick_context_patch(self, ctx: PluginContext, source_payload: dict[str, Any]) -> dict[str, Any]:
        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        sorting_context = SortingInboundContext.load_for_automatic(scratch_session)
        source_cell_code = _non_empty_str(source_payload.get("bin_cell_code")) or str(source_payload["bin_cell_index"])
        sorting_context.open_current_material(
            source_bin_code=str(source_payload["bin_code"]),
            source_cell_code=source_cell_code,
            material_identity_key=str(source_payload["material_identity_key"]),
            reel_thickness_mm=str(source_payload["reel_thickness"]),
            evidence={
                "source_event_id": source_payload["source_event_id"],
                "source_command_code": source_payload["source_event_id"],
                "source_version": source_payload.get("source_version"),
                "pkg_code": source_payload.get("pkg_code"),
                "wms_inventory_id": source_payload.get("wms_inventory_id"),
            },
        )
        sorting_context.set_station_state(scan_platform=_SCAN_PLATFORM_OCCUPIED, business_phase=PHASE_WAITING_SCAN)
        return {"sorting": _dict_copy(scratch_session.context_json.get("sorting"))}

    def _allocation_context_patch(
        self,
        ctx: PluginContext,
        *,
        actual_identity_key: str,
        actual_thickness: str,
        expected_thickness: str | None,
        allocation: Any,
    ) -> dict[str, Any]:
        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        sorting_context = SortingInboundContext.load_for_automatic(scratch_session)
        sorting_context.update_current_material(
            material_identity_key=actual_identity_key,
            reel_thickness_mm=actual_thickness,
            scan_evidence={
                "expected_reel_thickness_mm": expected_thickness,
                "actual_reel_thickness_mm": actual_thickness,
            },
        )
        target_bin_code = _non_empty_str(getattr(allocation, "target_bin_code", None))
        target_cell_index = _non_empty_str(getattr(allocation, "target_cell_index", None))
        if target_bin_code is None or target_cell_index is None:
            raise SortingInboundContextError("allocation missing target bin/cell")
        sorting_context.write_pending_target_placement(
            target_bin_code=target_bin_code,
            target_cell_code=target_cell_index,
            material_identity_key=actual_identity_key,
            reel_thickness_mm=actual_thickness,
            allocation_snapshot_version=getattr(allocation, "allocation_snapshot_version", None),
            capacity_evidence=_dict_copy(getattr(allocation, "capacity_evidence", None)),
        )
        sorting_context.clear_allocation_rejection()
        sorting_context.set_station_state(business_phase=PHASE_WAITING_TARGET_PLACE)
        return {"sorting": _dict_copy(scratch_session.context_json.get("sorting"))}

    def _allocation_rejection_intents(
        self,
        ctx: PluginContext,
        current_material: dict[str, Any],
        actual_thickness: str,
        allocation: Any,
    ) -> list[RuntimeIntent]:
        reason_code = _non_empty_str(getattr(allocation, "reason_code", None)) or "UNKNOWN"
        if reason_code == "NO_CAPACITY":
            root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
            scratch_session = SimpleNamespace(context_json=root_context)
            sorting_context = SortingInboundContext.load_for_automatic(scratch_session)
            sorting_context.update_current_material(reel_thickness_mm=actual_thickness)
            sorting_context.clear_pending_target_placement()
            sorting_context.record_allocation_rejection(
                reason_code=reason_code,
                message=_non_empty_str(getattr(allocation, "message", None)),
                capacity_evidence=_dict_copy(getattr(allocation, "capacity_evidence", None)),
            )
            sorting_context.set_station_state(business_phase=PHASE_WAITING_TARGET_BIN_SWITCH)
            return [RuntimeIntent.update_context({"sorting": _dict_copy(scratch_session.context_json.get("sorting"))})]

        if reason_code == "PROJECTION_INCONSISTENT":
            return self._block(
                "SORTING_TARGET_CELL_RECONCILING",
                "目标料格投影不一致，拒绝自动放盘",
                payload={
                    "current_material": current_material,
                    "allocation_reason_code": reason_code,
                    "capacity_evidence": _dict_copy(getattr(allocation, "capacity_evidence", None)),
                },
            )

        return self._block(
            "SORTING_TARGET_ALLOCATION_REJECTED",
            _non_empty_str(getattr(allocation, "message", None)) or "目标料格分配失败",
            payload={
                "current_material": current_material,
                "allocation_reason_code": reason_code,
                "capacity_evidence": _dict_copy(getattr(allocation, "capacity_evidence", None)),
            },
        )

    def _target_place_command_payload(self, sorting: Mapping[str, Any]) -> dict[str, Any]:
        pending_target = _dict_copy(sorting.get("pending_target_placement"))
        current_material = _dict_copy(sorting.get("current_material"))
        return {
            "target_bin_code": pending_target.get("target_bin_code"),
            "target_cell_code": pending_target.get("target_cell_code"),
            "material_identity_key": pending_target.get("material_identity_key"),
            "pkg_code": current_material.get("pkg_code")
            or _dict_copy(current_material.get("scan_evidence")).get("pkg_code")
            or _dict_copy(current_material.get("evidence")).get("pkg_code"),
            "reel_thickness": pending_target.get("reel_thickness_mm"),
        }

    def _ng_place_command_payload(self, sorting: Mapping[str, Any]) -> dict[str, Any]:
        current_material = _dict_copy(sorting.get("current_material"))
        return {
            "material_identity_key": current_material.get("actual_material_identity_key")
            or current_material.get("material_identity_key"),
            "pkg_code": current_material.get("pkg_code")
            or _dict_copy(current_material.get("scan_evidence")).get("pkg_code")
            or _dict_copy(current_material.get("evidence")).get("pkg_code"),
            "ng_reason_code": NG_REASON_LOCAL_SORTING_NG,
            "ng_location": "NG-01",
        }

    def _local_ng_context_patch(
        self,
        ctx: PluginContext,
        *,
        actual_identity_key: str,
        reason_message: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        sorting_context = SortingInboundContext.load_for_automatic(scratch_session)
        sorting_context.update_current_material(
            actual_material_identity_key=actual_identity_key,
            ng_status="MOVING_TO_NG",
            ng_reason_code=NG_REASON_LOCAL_SORTING_NG,
            ng_evidence=evidence,
        )
        sorting_context.clear_pending_target_placement()
        sorting_context.set_station_state(business_phase=PHASE_WAITING_NG_PLACE)
        return self._ng_root_patch(
            sorting=_dict_copy(scratch_session.context_json.get("sorting")),
            reason_message=reason_message,
            evidence=evidence,
        )

    def _ng_root_patch(
        self, *, sorting: dict[str, Any], reason_message: str, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "sorting": sorting,
            "ng_reason": NG_REASON_LOCAL_SORTING_NG,
            "pick_place_reason": NG_REASON_LOCAL_SORTING_NG,
            "scan_ng_reason_code": NG_REASON_LOCAL_SORTING_NG,
            "scan_ng_reason_message": reason_message,
            "source_payload": evidence,
        }

    def _target_mounted_payload(
        self,
        *,
        pending_target: dict[str, Any],
        current_material: dict[str, Any],
        source_event_id: str,
    ) -> dict[str, Any]:
        capacity_evidence = _dict_copy(pending_target.get("capacity_evidence"))
        return {
            "bin_code": pending_target.get("target_bin_code"),
            "bin_cell_index": pending_target.get("target_cell_code"),
            "bin_cell_code": pending_target.get("target_cell_code"),
            "material_identity_key": pending_target.get("material_identity_key"),
            "pkg_code": current_material.get("pkg_code")
            or _dict_copy(current_material.get("scan_evidence")).get("pkg_code")
            or _dict_copy(current_material.get("evidence")).get("pkg_code"),
            "reel_thickness": pending_target.get("reel_thickness_mm"),
            "cell_capacity_depth_mm": capacity_evidence.get("capacity_depth_mm"),
            "source_version": pending_target.get("allocation_snapshot_version"),
            "source_event_id": source_event_id,
        }

    def _target_success_context_patch(self, ctx: PluginContext) -> dict[str, Any]:
        root_context = _dict_copy(getattr(getattr(ctx, "session", None), "context_json", None))
        scratch_session = SimpleNamespace(context_json=root_context)
        sorting_context = SortingInboundContext.load_for_automatic(scratch_session)
        sorting_context.clear_pending_target_placement()
        sorting_context.close_current_material()
        sorting_context.clear_allocation_rejection()
        sorting_context.set_station_state(scan_platform=_SCAN_PLATFORM_EMPTY, business_phase=PHASE_WAITING_SOURCE_PICK)
        return {"sorting": _dict_copy(scratch_session.context_json.get("sorting"))}

    async def _active_target_snapshot(
        self,
        ctx: PluginContext,
        sorting: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        provider = self._active_snapshot_provider or getattr(
            getattr(ctx, "services", None), "active_rack_snapshot_provider", None
        )
        if provider is not None:
            snapshot = provider.active_bin_rack(
                context={
                    "active_bin_rack": _dict_copy(sorting.get("active_target_bin")),
                    "active_target_bin_code": sorting.get("active_target_bin_code"),
                    "current_material": _dict_copy(sorting.get("current_material")),
                    "station": {"position_code": _TARGET_STATION_CODE},
                    "target_station_code": _TARGET_STATION_CODE,
                }
            )
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
            if isinstance(snapshot, Mapping):
                return dict(cast("Mapping[str, Any]", snapshot))

        return None

    async def _target_station_ready_snapshot(
        self,
        ctx: PluginContext,
        sorting: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, list[RuntimeIntent] | None]:
        try:
            target_station_status = await self._target_station_lease_status(ctx)
        except ValueError as exc:
            return None, self._block(
                "SORTING_TARGET_STATION_LEASE_UNKNOWN",
                "目标 Station lease 配置无效，无法自动分格",
                payload={"position_code": _TARGET_STATION_CODE, "error": str(exc)},
            )
        if target_station_status is None:
            return None, self._block(
                "SORTING_TARGET_STATION_LEASE_UNKNOWN",
                "缺少目标 Station lease 状态，无法自动分格",
                payload={"position_code": _TARGET_STATION_CODE},
            )
        if not getattr(target_station_status, "available", False):
            reason_code = str(getattr(target_station_status, "reason_code", None) or "STATION_BUSY")
            return None, [
                RuntimeIntent.resource_wait(
                    resource_kind="STATION",
                    resource_key=f"station:{_TARGET_STATION_CODE}",
                    reason_code="SORTING_TARGET_STATION_LEASE_BUSY",
                    message="目标 Station 当前不可用，等待资源释放后自动重试",
                    suggested_action="等待目标 Station 释放，或检查当前 active rack/session/dispatch 占用",
                    payload={
                        "position_code": _TARGET_STATION_CODE,
                        "status_reason_code": reason_code,
                        "active_rack_code": getattr(target_station_status, "active_rack_code", None),
                        "active_session_id": getattr(target_station_status, "active_session_id", None),
                        "active_dispatch_key": getattr(target_station_status, "active_dispatch_key", None),
                    },
                )
            ]

        active_snapshot = await self._active_target_snapshot(ctx, sorting)
        if active_snapshot is None:
            return None, self._block("SORTING_TARGET_SNAPSHOT_MISSING", "缺少 active target bin 快照，无法自动分格")
        return active_snapshot, None

    @staticmethod
    async def _target_station_lease_status(ctx: PluginContext) -> Any | None:
        provider = getattr(getattr(ctx, "services", None), "station_lease_status_provider", None)
        if provider is None:
            return None
        status = provider.station_lease_status(
            _TARGET_STATION_CODE,
            rack_kind=_TARGET_STATION_RACK_KIND,
            allow_active_rack_bound=True,
        )
        if inspect.isawaitable(status):
            status = await status
        return status

    def _source_pick_payload(
        self,
        payload_json: Mapping[str, Any],
        data: Mapping[str, Any],
        source_event_id: str,
    ) -> dict[str, Any]:
        return {
            "bin_code": _payload_text(payload_json, data, "bin_code", "source_bin_code"),
            "bin_cell_index": _payload_text(payload_json, data, "bin_cell_index", "source_cell_index"),
            "bin_cell_code": _payload_text(payload_json, data, "bin_cell_code", "source_cell_code"),
            "material_identity_key": _payload_text(payload_json, data, "material_identity_key"),
            "pkg_code": _payload_text(payload_json, data, "pkg_code", "PkgID"),
            "wms_inventory_id": _payload_text(payload_json, data, "wms_inventory_id"),
            "reel_thickness": _payload_text(payload_json, data, "reel_thickness", "reel_thickness_mm"),
            "source_version": _payload_text(payload_json, data, "source_version"),
            "source_event_id": source_event_id,
        }

    def _source_pick_request_command_payload(
        self,
        payload_json: Mapping[str, Any],
        data: Mapping[str, Any],
        inbox: WorklineInbox,
    ) -> dict[str, Any]:
        bin_code = _payload_text(payload_json, data, "bin_code", "source_bin_code")
        bin_cell_index = _positive_int(data.get("bin_cell_index")) or _positive_int(data.get("source_cell_index"))
        bin_cell_code = _payload_text(payload_json, data, "bin_cell_code", "source_cell_code")
        reel_thickness = _payload_text(payload_json, data, "reel_thickness", "reel_thickness_mm")
        return {
            "handoff_demand_id": _positive_int(data.get("handoff_demand_id")),
            "handoff_source_item_id": _positive_int(data.get("handoff_source_item_id")),
            "claim_attempt_no": _positive_int(data.get("claim_attempt_no")),
            "source_pick_inbox_id": _positive_int(getattr(inbox, "id", None)),
            "source_pick_request_event_id": _payload_text(payload_json, data, "event_id"),
            "rack_release_id": _payload_text(payload_json, data, "rack_release_id"),
            "single_layer_rack_code": _payload_text(payload_json, data, "single_layer_rack_code"),
            "bin_code": bin_code,
            "source_bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "bin_cell_code": bin_cell_code,
            "source_cell_code": bin_cell_code or (str(bin_cell_index) if bin_cell_index is not None else None),
            "material_identity_key": _payload_text(payload_json, data, "material_identity_key"),
            "pkg_code": _payload_text(payload_json, data, "pkg_code", "PkgID"),
            "reel_thickness": reel_thickness,
            "reel_thickness_mm": reel_thickness,
            "route_evidence": _dict_copy(data.get("route_evidence")),
        }

    @staticmethod
    def _source_event_id(payload_json: Mapping[str, Any], inbox: WorklineInbox) -> str:
        return (
            _non_empty_str(payload_json.get("command_code"))
            or _non_empty_str(payload_json.get("source_event_id"))
            or f"source-pick:{getattr(inbox, 'id', 'unknown')}"
        )

    @staticmethod
    def _block(reason_code: str, message: str, *, payload: dict[str, Any] | None = None) -> list[RuntimeIntent]:
        return [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code=reason_code,
                message=message,
                suggested_action="人工检查 SMT 分拣入库当前物料、扫码平台和源料格状态",
                payload=payload,
            )
        ]


def _positive_decimal_text(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        return None
    if not decimal_value.is_finite() or decimal_value <= 0:
        return None
    return value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _payload_has_any(payload_json: Mapping[str, Any], data: Mapping[str, Any], *field_names: str) -> bool:
    return any(field_name in data or field_name in payload_json for field_name in field_names)


__all__ = ["SmtSortingInboundFlowService"]
