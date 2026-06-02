"""SMT 分拣入库 P0 业务 flow 服务。"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from src.workline_plugins.smt_sorting_inbound.constants import PHASE_WAITING_SCAN
from src.workline_plugins.smt_sorting_inbound.context import SortingInboundContext, SortingInboundContextError
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

_SCAN_PLATFORM_EMPTY = "EMPTY"
_SCAN_PLATFORM_OCCUPIED = "OCCUPIED"


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


__all__ = ["SmtSortingInboundFlowService"]
