"""SMT 货架/料箱调度资源领域服务。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

SmtRackBinSchedulingDecisionKind = Literal["ALLOCATED", "RACK_EXCHANGE_REQUIRED", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class SmtFullBoxExchangeRequest:
    """SMT 满箱交换外部请求。"""

    dispatch_key: str
    target_code: str
    payload: Mapping[str, Any]
    timeout_seconds: int = 1800
    source_system: str = "WMS_RCS"


@dataclass(frozen=True, slots=True, init=False)
class SmtRackBinSchedulingDecision:
    """SMT 货架/料箱调度决策。"""

    kind: SmtRackBinSchedulingDecisionKind
    bin_location: Mapping[str, Any] | None
    external_request: SmtFullBoxExchangeRequest | None
    reason_code: str | None
    message: str | None

    def __init__(
        self,
        *,
        kind: SmtRackBinSchedulingDecisionKind | None = None,
        bin_location: Mapping[str, Any] | None = None,
        external_request: SmtFullBoxExchangeRequest | None = None,
        reason_code: str | None = None,
        message: str | None = None,
        full_box_exchange_request: SmtFullBoxExchangeRequest | None = None,
    ) -> None:
        """创建调度决策，并兼容旧 full_box_exchange_request 构造参数。"""

        if external_request is not None and full_box_exchange_request is not None:
            raise ValueError("external_request and full_box_exchange_request cannot both be set")

        request = external_request or full_box_exchange_request
        resolved_kind = kind or self._infer_kind(bin_location=bin_location, external_request=request)
        if resolved_kind == "ALLOCATED" and bin_location is None:
            raise ValueError("ALLOCATED decision requires bin_location")
        if resolved_kind == "RACK_EXCHANGE_REQUIRED" and request is None:
            raise ValueError("RACK_EXCHANGE_REQUIRED decision requires external_request")
        if resolved_kind == "BLOCKED" and not reason_code:
            raise ValueError("BLOCKED decision requires reason_code")

        object.__setattr__(self, "kind", resolved_kind)
        object.__setattr__(self, "bin_location", dict(bin_location) if bin_location is not None else None)
        object.__setattr__(self, "external_request", request)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "message", message)

    @staticmethod
    def _infer_kind(
        *,
        bin_location: Mapping[str, Any] | None,
        external_request: SmtFullBoxExchangeRequest | None,
    ) -> SmtRackBinSchedulingDecisionKind:
        if bin_location is not None and external_request is None:
            return "ALLOCATED"
        if bin_location is None and external_request is not None:
            return "RACK_EXCHANGE_REQUIRED"
        raise ValueError("SmtRackBinSchedulingDecision requires exactly one scheduling result")

    @property
    def full_box_exchange_request(self) -> SmtFullBoxExchangeRequest | None:
        """兼容旧插件读取的满箱交换字段。"""

        return self.external_request


class SmtRackBinSchedulingService:
    """负责 SMT 粗分机出料阶段的货架/料箱调度。

    v1 只提供确定性调度结果，先把原先散落在插件里的占位能力收敛到资源领域服务。
    后续接入真实 RackRelease / RackBinMount 时，应在此服务内替换调度策略。
    """

    BIN_TYPES: ClassVar[tuple[str, ...]] = ("三格箱", "五格箱", "九格箱")
    REQUEST_TYPE: ClassVar[str] = "SMT_RACK_EXCHANGE_AND_SUPPLY"
    EXCHANGE_ACTIONS: ClassVar[tuple[str, ...]] = ("MOVE_OUT_CURRENT_RACK", "SUPPLY_EMPTY_RACK")
    REQUIRED_MATERIAL_FIELDS: ClassVar[tuple[str, ...]] = ("DateCode", "LotCode", "PkgID")
    REQUIRED_LOCATION_FIELDS: ClassVar[tuple[str, ...]] = ("bin_id", "bin_type", "bin_cell_location")

    def allocate(self, barcode: str) -> dict[str, Any]:
        """按物料业务键分配目标料箱位置。

        已废弃：仅保留给旧测试和旧 allocator mapping 兼容路径使用。
        """

        checksum = int(hashlib.md5(barcode.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        return {
            "bin_id": f"BIN_{checksum % 900 + 100}",
            "bin_type": self.BIN_TYPES[checksum % len(self.BIN_TYPES)],
            "bin_cell_location": str(checksum % 9 + 1),
        }

    def plan_allocation(
        self,
        barcode: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SmtRackBinSchedulingDecision:
        """生成出料阶段调度决策。"""

        scheduling_context = dict(context or {})
        if "six_in_one" not in scheduling_context and "active_bin_rack" not in scheduling_context:
            return SmtRackBinSchedulingDecision(bin_location=self.allocate(barcode))

        material = self._material_from_context(scheduling_context)
        missing_material_fields = [field for field in self.REQUIRED_MATERIAL_FIELDS if not material.get(field)]
        if missing_material_fields:
            return SmtRackBinSchedulingDecision(
                kind="BLOCKED",
                reason_code="MISSING_MATERIAL_FIELDS",
                message=f"SMT 料箱调度缺少物料字段: {', '.join(missing_material_fields)}",
            )
        if material["PkgID"] != barcode:
            return SmtRackBinSchedulingDecision(
                kind="BLOCKED",
                reason_code="PKG_ID_MISMATCH",
                message="SMT 料箱调度条码与六合一码 PkgID 不一致",
            )

        active_rack = scheduling_context.get("active_bin_rack")
        if not isinstance(active_rack, Mapping):
            return self._rack_exchange_decision(
                context=scheduling_context,
                material=material,
                active_rack=None,
                reason_code="NO_ACTIVE_RACK",
                message="SMT 料箱调度缺少当前可用料架",
            )

        cells = self._rack_cells(active_rack)
        compatible_cell = self._find_compatible_occupied_cell(cells, material)
        if compatible_cell is not None:
            return SmtRackBinSchedulingDecision(bin_location=self._bin_location(compatible_cell, active_rack))

        empty_cell = self._find_first_empty_cell(cells)
        if empty_cell is not None:
            return SmtRackBinSchedulingDecision(bin_location=self._bin_location(empty_cell, active_rack))

        return self._rack_exchange_decision(
            context=scheduling_context,
            material=material,
            active_rack=active_rack,
            reason_code="NO_COMPATIBLE_OR_EMPTY_CELL",
            message="当前料架无同 DC/LC 兼容格位，也无可用空格",
        )

    def _material_from_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        six_in_one = context.get("six_in_one")
        return dict(six_in_one) if isinstance(six_in_one, Mapping) else {}

    def _active_rack_or_none(self, value: Any) -> Mapping[str, Any] | None:
        return value if isinstance(value, Mapping) else None

    def _rack_cells(self, active_rack: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw_cells = active_rack.get("cells") or active_rack.get("bin_cells") or active_rack.get("cell_snapshots") or []
        if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
            return []
        return [cell for cell in raw_cells if isinstance(cell, Mapping)]

    def _find_compatible_occupied_cell(
        self, cells: Sequence[Mapping[str, Any]], material: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        for cell in cells:
            if not self._is_schedulable_cell(cell) or self._cell_status(cell) != "OCCUPIED":
                continue
            if cell.get("DateCode") == material["DateCode"] and cell.get("LotCode") == material["LotCode"]:
                return cell
        return None

    def _find_first_empty_cell(self, cells: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
        for cell in cells:
            if self._is_schedulable_cell(cell) and self._cell_status(cell) == "EMPTY":
                return cell
        return None

    def _is_schedulable_cell(self, cell: Mapping[str, Any]) -> bool:
        if bool(cell.get("locked")) or bool(cell.get("disabled")):
            return False
        if self._cell_status(cell) in {"LOCKED", "DISABLED"}:
            return False
        return all(cell.get(field) for field in self.REQUIRED_LOCATION_FIELDS)

    def _cell_status(self, cell: Mapping[str, Any]) -> str:
        return str(cell.get("status") or "").upper()

    def _bin_location(self, cell: Mapping[str, Any], active_rack: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "rack_id": cell.get("rack_id") or active_rack.get("rack_id") or active_rack.get("rack_code"),
            "bin_id": cell["bin_id"],
            "bin_type": cell["bin_type"],
            "bin_cell_location": str(cell["bin_cell_location"]),
        }

    def _rack_exchange_decision(
        self,
        *,
        context: Mapping[str, Any],
        material: Mapping[str, Any],
        active_rack: Mapping[str, Any] | None,
        reason_code: str,
        message: str,
    ) -> SmtRackBinSchedulingDecision:
        target_code = self._target_code(context)
        if target_code is None:
            return SmtRackBinSchedulingDecision(
                kind="BLOCKED",
                reason_code="RACK_EXCHANGE_TARGET_MISSING",
                message="SMT 换架请求缺少 WMS/RCS 目标地址配置",
            )

        dispatch_key = self._dispatch_key(context=context, material=material, active_rack=active_rack)
        payload: dict[str, Any] = {
            "request_type": self.REQUEST_TYPE,
            "dispatch_key": dispatch_key,
            "material": dict(material),
            "current_rack_snapshot": dict(active_rack or {}),
            "actions": list(self.EXCHANGE_ACTIONS),
            "resume_callback_type": "WMS_RACK_ARRIVED",
            "reason_code": reason_code,
        }
        trace_id = self._trace_id(context)
        if trace_id is not None:
            payload["trace_id"] = trace_id

        return SmtRackBinSchedulingDecision(
            kind="RACK_EXCHANGE_REQUIRED",
            external_request=SmtFullBoxExchangeRequest(
                dispatch_key=dispatch_key,
                target_code=target_code,
                payload=payload,
            ),
            reason_code=reason_code,
            message=message,
        )

    def _target_code(self, context: Mapping[str, Any]) -> str | None:
        for key in ("wms_rcs_rack_exchange_url", "rack_exchange_target_code", "wms_rcs_target_code"):
            value = context.get(key)
            if value:
                return str(value)

        config = context.get("config")
        if isinstance(config, Mapping):
            for key in ("wms_rcs_rack_exchange_url", "rack_exchange_target_code", "wms_rcs_target_code"):
                value = config.get(key)
                if value:
                    return str(value)

        return None

    def _trace_id(self, context: Mapping[str, Any]) -> str | None:
        for key in ("trace_id", "trace_code"):
            value = context.get(key)
            if value:
                return str(value)

        session = context.get("session")
        if isinstance(session, Mapping):
            for key in ("trace_id", "trace_code"):
                value = session.get(key)
                if value:
                    return str(value)
        return None

    def _dispatch_key(
        self,
        *,
        context: Mapping[str, Any],
        material: Mapping[str, Any],
        active_rack: Mapping[str, Any] | None,
    ) -> str:
        token = self._context_dispatch_token(context)
        if token is None:
            rack_id = ""
            if active_rack is not None:
                rack_id = str(active_rack.get("rack_id") or active_rack.get("rack_code") or "")
            token = f"{material.get('PkgID')}:{rack_id}"
        return f"external:smt_classifier:{token}:RACK_EXCHANGE_AND_SUPPLY"

    def _context_dispatch_token(self, context: Mapping[str, Any]) -> str | None:
        for key in ("dispatch_key", "trace_id", "trace_code", "session_id", "session_code", "workline_session_id"):
            value = context.get(key)
            if value:
                return str(value)

        session = context.get("session")
        if isinstance(session, Mapping):
            for key in ("dispatch_key", "trace_id", "session_id", "id", "code"):
                value = session.get(key)
                if value:
                    return str(value)
        return None


smt_rack_bin_scheduling_service = SmtRackBinSchedulingService()


__all__ = [
    "SmtFullBoxExchangeRequest",
    "SmtRackBinSchedulingDecision",
    "SmtRackBinSchedulingDecisionKind",
    "SmtRackBinSchedulingService",
    "smt_rack_bin_scheduling_service",
]
