"""SMT 分拣入库 Session context typed 写入合同。"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from src.app.runtime.capabilities.phase4.contracts.smt_sorting_inbound import SORTING_CONTEXT_SCHEMA_VERSION

_SORTING_CONTEXT_KEY = "sorting"
_SCHEMA_VERSION_KEY = "context_schema_version"


class SortingInboundContextError(ValueError):
    """分拣入库 Session context 不满足自动运行合同。"""


def _dict_copy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast("Mapping[str, Any]", value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _require_text(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SortingInboundContextError(f"{field_name} must be a non-empty string")
    return value


def _require_positive_int(field_name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SortingInboundContextError(f"{field_name} must be a positive integer")
    return value


def _require_json_serializable(field_name: str, value: Any) -> Any:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SortingInboundContextError(f"{field_name} must be JSON serializable") from exc
    return value


class SortingInboundContext:
    """封装 `session.context_json["sorting"]` 的 P0 typed 写入入口。"""

    def __init__(self, session: Any, *, root_context: dict[str, Any], sorting: dict[str, Any]) -> None:
        self._session = session
        self._root_context = root_context
        self.sorting = sorting

    @classmethod
    def initialize(cls, session: Any) -> SortingInboundContext:
        """初始化 sorting 子树，并通过替换 context_json 触发 ORM JSON 变更感知。"""

        root_context = _dict_copy(getattr(session, "context_json", None))
        sorting = _dict_copy(root_context.get(_SORTING_CONTEXT_KEY))
        sorting[_SCHEMA_VERSION_KEY] = SORTING_CONTEXT_SCHEMA_VERSION
        context = cls(session, root_context=root_context, sorting=sorting)
        context._writeback()
        return context

    @classmethod
    def load_for_automatic(cls, session: Any) -> SortingInboundContext:
        """加载自动运行 context；缺失或版本不兼容时拒绝继续自动分拣。"""

        root_context = _dict_copy(getattr(session, "context_json", None))
        sorting = _dict_copy(root_context.get(_SORTING_CONTEXT_KEY))
        version = sorting.get(_SCHEMA_VERSION_KEY)
        if version is None:
            raise SortingInboundContextError("sorting.context_schema_version 缺失，拒绝自动分拣")
        if version != SORTING_CONTEXT_SCHEMA_VERSION:
            raise SortingInboundContextError(
                f"sorting.context_schema_version 不兼容: {version!r}, expected {SORTING_CONTEXT_SCHEMA_VERSION}"
            )
        return cls(session, root_context=root_context, sorting=sorting)

    def open_current_material(
        self,
        *,
        source_bin_code: str,
        source_cell_code: str,
        material_identity_key: str,
        reel_thickness_mm: Decimal | str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """记录源格已出账、等待扫码/分格的当前物料。"""

        self._set_sorting_value(
            "current_material",
            {
                "source_bin_code": _require_text("source_bin_code", source_bin_code),
                "source_cell_code": _require_text("source_cell_code", source_cell_code),
                "material_identity_key": _require_text("material_identity_key", material_identity_key),
                "reel_thickness_mm": _json_safe(reel_thickness_mm),
                "evidence": _json_safe(dict(evidence or {})),
            },
        )

    def update_current_material(self, **patch: Any) -> None:
        """局部更新当前物料，保留 Decimal 字符串证据。"""

        current_material = _dict_copy(self.sorting.get("current_material"))
        if not current_material:
            raise SortingInboundContextError("current_material 缺失，无法更新")
        current_material.update(_json_safe(patch))
        self._set_sorting_value("current_material", current_material)

    def close_current_material(self) -> None:
        """关闭当前物料上下文。"""

        self._pop_sorting_value("current_material")

    def write_source_pick_request(
        self,
        *,
        handoff_demand_id: int,
        handoff_source_item_id: int,
        claim_attempt_no: int,
        event_id: str,
        target_workline_code: str,
        manifest_contract_version: str,
        source_rack_position_code: str,
        target_rack_position_code: str,
        route_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """写入 handoff claim 触发的源站取料请求上下文。"""

        safe_route_evidence = _require_json_serializable(
            "route_evidence",
            _json_safe(dict(route_evidence or {})),
        )
        self._set_sorting_value(
            "source_pick_request",
            {
                "handoff_demand_id": _require_positive_int("handoff_demand_id", handoff_demand_id),
                "handoff_source_item_id": _require_positive_int("handoff_source_item_id", handoff_source_item_id),
                "claim_attempt_no": _require_positive_int("claim_attempt_no", claim_attempt_no),
                "event_id": _require_text("event_id", event_id),
                "target_workline_code": _require_text("target_workline_code", target_workline_code),
                "manifest_contract_version": _require_text("manifest_contract_version", manifest_contract_version),
                "source_rack_position_code": _require_text("source_rack_position_code", source_rack_position_code),
                "target_rack_position_code": _require_text("target_rack_position_code", target_rack_position_code),
                "route_evidence": safe_route_evidence,
            },
        )

    def get_source_pick_request(self) -> dict[str, Any]:
        """读取源站取料请求上下文；缺失时返回空 dict。"""

        return copy.deepcopy(_dict_copy(self.sorting.get("source_pick_request")))

    def write_pending_target_placement(
        self,
        *,
        target_bin_code: str,
        target_cell_code: str,
        material_identity_key: str,
        reel_thickness_mm: Decimal | str,
        allocation_snapshot_version: int | str | None = None,
        capacity_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """写入目标端放盘命令前的 pending placement 快照。"""

        placement: dict[str, Any] = {
            "target_bin_code": _require_text("target_bin_code", target_bin_code),
            "target_cell_code": _require_text("target_cell_code", target_cell_code),
            "material_identity_key": _require_text("material_identity_key", material_identity_key),
            "reel_thickness_mm": _json_safe(reel_thickness_mm),
            "capacity_evidence": _json_safe(dict(capacity_evidence or {})),
        }
        if allocation_snapshot_version is not None:
            placement["allocation_snapshot_version"] = allocation_snapshot_version
        self._set_sorting_value("pending_target_placement", placement)

    def clear_pending_target_placement(self) -> None:
        """目标放盘成功后清理 pending placement。"""

        self._pop_sorting_value("pending_target_placement")

    def record_allocation_rejection(
        self,
        *,
        reason_code: str,
        message: str | None = None,
        capacity_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """记录分格拒绝证据，供换箱或人工处理继续使用。"""

        rejection = {
            "reason_code": _require_text("reason_code", reason_code),
            "capacity_evidence": _json_safe(dict(capacity_evidence or {})),
        }
        if message:
            rejection["message"] = message
        self._set_sorting_value("allocation_rejection", rejection)

    def clear_allocation_rejection(self) -> None:
        """清理上一次分格拒绝证据。"""

        self._pop_sorting_value("allocation_rejection")

    def set_active_target_bin(self, target_bin_code: str) -> None:
        """记录当前目标料箱。"""

        self._set_sorting_value("active_target_bin_code", _require_text("target_bin_code", target_bin_code))

    def set_station_state(self, *, scan_platform: str | None = None, business_phase: str | None = None) -> None:
        """记录 P0 需要的站点占用和业务阶段字段。"""

        sorting = dict(self.sorting)
        if scan_platform is not None:
            stations = _dict_copy(sorting.get("stations"))
            stations["scan_platform"] = _require_text("scan_platform", scan_platform)
            sorting["stations"] = stations
        if business_phase is not None:
            sorting["business_phase"] = _require_text("business_phase", business_phase)
        self.sorting = sorting
        self._writeback()

    def _set_sorting_value(self, key: str, value: Any) -> None:
        sorting = dict(self.sorting)
        sorting[key] = _json_safe(value)
        self.sorting = sorting
        self._writeback()

    def _pop_sorting_value(self, key: str) -> None:
        sorting = dict(self.sorting)
        sorting.pop(key, None)
        self.sorting = sorting
        self._writeback()

    def _writeback(self) -> None:
        root_context = dict(self._root_context)
        root_context[_SORTING_CONTEXT_KEY] = dict(self.sorting)
        self._session.context_json = root_context
        self._root_context = root_context


__all__ = ["SortingInboundContext", "SortingInboundContextError"]
