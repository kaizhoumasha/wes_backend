"""SMT 货架/料箱调度资源领域服务。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

from src.app.resource.services.material_identity import material_identity_keys_match

SmtRackBinSchedulingDecisionKind = Literal[
    "ALLOCATED",
    "RACK_OPERATION_REQUIRED",
    "BLOCKED",
]
SmtReelSizeKind = Literal["SEVEN_INCH", "LARGE"]
WMS_RCS_RACK_OPERATION_ENDPOINT = "WMS_RCS_RACK_OPERATION"


@dataclass(frozen=True, slots=True)
class SmtRackOperationRequest:
    """SMT 货架 operation 外部请求。"""

    operation_key: str
    target_code: str
    payload: Mapping[str, Any]
    timeout_seconds: int = 1800
    source_system: str = "WMS_RCS"


@dataclass(frozen=True, slots=True, init=False)
class SmtRackBinSchedulingDecision:
    """SMT 货架/料箱调度决策。"""

    kind: SmtRackBinSchedulingDecisionKind
    bin_location: Mapping[str, Any] | None
    rack_operation_request: SmtRackOperationRequest | None
    reason_code: str | None
    message: str | None

    def __init__(
        self,
        *,
        kind: SmtRackBinSchedulingDecisionKind | None = None,
        bin_location: Mapping[str, Any] | None = None,
        rack_operation_request: SmtRackOperationRequest | None = None,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> None:
        """创建调度决策。"""

        resolved_kind = kind or self._infer_kind(
            bin_location=bin_location,
            rack_operation_request=rack_operation_request,
        )
        if resolved_kind == "ALLOCATED" and bin_location is None:
            raise ValueError("ALLOCATED decision requires bin_location")
        if resolved_kind == "RACK_OPERATION_REQUIRED" and rack_operation_request is None:
            raise ValueError("RACK_OPERATION_REQUIRED decision requires rack_operation_request")
        if resolved_kind == "BLOCKED" and not reason_code:
            raise ValueError("BLOCKED decision requires reason_code")

        object.__setattr__(self, "kind", resolved_kind)
        object.__setattr__(self, "bin_location", dict(bin_location) if bin_location is not None else None)
        object.__setattr__(self, "rack_operation_request", rack_operation_request)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "message", message)

    @staticmethod
    def _infer_kind(
        *,
        bin_location: Mapping[str, Any] | None,
        rack_operation_request: SmtRackOperationRequest | None,
    ) -> SmtRackBinSchedulingDecisionKind:
        if bin_location is not None and rack_operation_request is None:
            return "ALLOCATED"
        if bin_location is None and rack_operation_request is not None:
            return "RACK_OPERATION_REQUIRED"
        raise ValueError("SmtRackBinSchedulingDecision requires exactly one scheduling result")


class SmtRackBinSchedulingService:
    """负责 SMT 粗分机出料阶段的货架/料箱调度。

    v1 只提供确定性调度结果，先把原先散落在插件里的占位能力收敛到资源领域服务。
    后续接入真实 RackRelease / RackBinMount 时，应在此服务内替换调度策略。
    """

    SIX_CELL_BIN_TYPE: ClassVar[str] = "6格箱"
    THREE_CELL_BIN_TYPE: ClassVar[str] = "3格箱"
    BIN_TYPES: ClassVar[tuple[str, ...]] = (SIX_CELL_BIN_TYPE, THREE_CELL_BIN_TYPE)
    BIN_TYPE_ALIASES: ClassVar[dict[str, str]] = {
        "6格箱": SIX_CELL_BIN_TYPE,
        "六格箱": SIX_CELL_BIN_TYPE,
        "TYPE_A": SIX_CELL_BIN_TYPE,
        "A": SIX_CELL_BIN_TYPE,
        "3格箱": THREE_CELL_BIN_TYPE,
        "三格箱": THREE_CELL_BIN_TYPE,
        "TYPE_B": THREE_CELL_BIN_TYPE,
        "B": THREE_CELL_BIN_TYPE,
    }
    SEVEN_INCH_CELL_SUFFIXES: ClassVar[frozenset[str]] = frozenset({"1", "2", "3", "4", "5", "6"})
    THREE_CELL_SEVEN_INCH_SUFFIXES: ClassVar[frozenset[str]] = frozenset({"1", "2"})
    LARGE_CELL_SUFFIXES: ClassVar[frozenset[str]] = frozenset({"7"})
    RACK_SLOT_CODES: ClassVar[tuple[str, ...]] = ("A", "B", "C", "D")
    RACK_SLOT_SIDE_BY_CODE: ClassVar[dict[str, str]] = {"A": "0", "B": "0", "C": "1", "D": "1"}
    RELEASE_BIN_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {
            "EMPTY_VERIFIED",
            "IN_USE",
            "LOCKED",
            "FULL_SNAPSHOT",
            "EXCEPTION",
            "DISABLED",
            "UNKNOWN",
            "CLOSED",
            "FULL",
        }
    )
    RELEASE_BIN_STATUS_ALIASES: ClassVar[dict[str, str]] = {
        "EMPTY": "EMPTY_VERIFIED",
        "AVAILABLE": "EMPTY_VERIFIED",
        "OCCUPIED": "IN_USE",
        "USED": "IN_USE",
        "ABNORMAL": "EXCEPTION",
        "ERROR": "EXCEPTION",
    }
    EMPTY_CELL_STATUSES: ClassVar[frozenset[str]] = frozenset({"EMPTY", "EMPTY_VERIFIED", "AVAILABLE"})
    PENDING_RACK_OPERATION_STATUSES: ClassVar[frozenset[str]] = frozenset({"PENDING", "REQUESTED", "IN_PROGRESS"})
    RACK_OPERATION_REQUEST_TYPE: ClassVar[str] = "SMT_RACK_OPERATION"
    RACK_OPERATION_TYPE: ClassVar[str] = "REPLACE_CLASSIFIER_WORK_RACK"
    ALLOCATE_RACK_ACTIONS: ClassVar[tuple[str, ...]] = ("ALLOCATE_AND_MOVE_RACK",)
    REPLACE_RACK_ACTIONS: ClassVar[tuple[str, ...]] = ("MOVE_OUT_ACTIVE_RACK", "ALLOCATE_AND_MOVE_RACK")
    DEFAULT_WORK_POSITION_CODE: ClassVar[str] = "SINGLE_LAYER_A"
    DEFAULT_NEW_RACK_KIND: ClassVar[str] = "SINGLE_LAYER"
    DEFAULT_MOVE_OUT_TARGET_POSITION_ROLE: ClassVar[str] = "SMT_EMPTY_RACK_AREA"
    REQUIRED_MATERIAL_FIELDS: ClassVar[tuple[str, ...]] = ("DateCode", "LotCode", "PkgID")
    REQUIRED_LOCATION_FIELDS: ClassVar[tuple[str, ...]] = ("bin_id", "bin_type", "bin_cell_location")

    def allocate(self, barcode: str) -> dict[str, Any]:
        """按物料业务键分配目标料箱位置。

        已废弃：仅保留给旧测试和旧 allocator mapping 兼容路径使用。
        """

        checksum = int(hashlib.md5(barcode.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        bin_id = f"BIN-{checksum % 900 + 100}"
        suffix = str(checksum % 6 + 1)
        rack_id = f"NHW-1CLJ-{checksum % 9000 + 1:04d}"
        rack_slot_code = self.RACK_SLOT_CODES[checksum % len(self.RACK_SLOT_CODES)]
        return {
            "rack_id": rack_id,
            "rack_slot_code": rack_slot_code,
            "rack_slot_location_code": self._canonical_rack_slot_location_code(rack_id, rack_slot_code),
            "bin_id": bin_id,
            "bin_orientation_code": f"{bin_id}-A",
            "bin_type": self.SIX_CELL_BIN_TYPE,
            "bin_cell_location": self._canonical_cell_location(bin_id, suffix),
            "bin_cell_index": suffix,
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
        material_block = self._material_block_decision(material, barcode=barcode)
        if material_block is not None:
            return material_block
        pending_operation_decision = self._pending_rack_operation_decision(scheduling_context)
        if pending_operation_decision is not None:
            return pending_operation_decision

        active_rack = scheduling_context.get("active_bin_rack")
        if not isinstance(active_rack, Mapping):
            return self._rack_operation_decision(
                context=scheduling_context,
                material=material,
                active_rack=None,
                reason_code="NO_ACTIVE_RACK",
                message="SMT 料箱调度缺少当前可用料架",
            )
        active_rack_map = cast("Mapping[str, Any]", active_rack)
        blocking_decision: SmtRackBinSchedulingDecision | None = None
        rack_bin_snapshots = self._rack_bin_snapshots(active_rack_map)
        if len(rack_bin_snapshots) != len(self.RACK_SLOT_CODES):
            blocking_decision = self._invalid_active_rack_snapshot_decision()
        elif self._requires_empty_active_rack(scheduling_context) and not self._active_rack_is_empty(
            active_rack_map, rack_bin_snapshots
        ):
            blocking_decision = self._non_empty_active_rack_decision()

        reel_size_kind = self._reel_size_kind(scheduling_context)
        if blocking_decision is None and reel_size_kind is None:
            blocking_decision = SmtRackBinSchedulingDecision(
                kind="BLOCKED",
                reason_code="MISSING_OR_UNSUPPORTED_REEL_DIAMETER",
                message="SMT 料箱调度缺少或不支持料盘尺寸，无法匹配 6/3 格箱料格",
            )
        if blocking_decision is not None:
            return blocking_decision
        reel_size_kind = cast("SmtReelSizeKind", reel_size_kind)

        cells = self._rack_cells(active_rack_map)
        reel_thickness = self._reel_thickness(scheduling_context)
        compatible_cell = self._find_compatible_occupied_cell(
            cells,
            material,
            reel_size_kind,
            reel_thickness=reel_thickness,
        )
        if compatible_cell is not None:
            return SmtRackBinSchedulingDecision(bin_location=self._bin_location(compatible_cell, active_rack_map))

        empty_cell = self._find_first_empty_cell(cells, reel_size_kind, reel_thickness=reel_thickness)
        if empty_cell is not None:
            return SmtRackBinSchedulingDecision(bin_location=self._bin_location(empty_cell, active_rack_map))

        return self._rack_operation_decision(
            context=scheduling_context,
            material=material,
            active_rack=active_rack_map,
            reason_code="NO_COMPATIBLE_OR_EMPTY_CELL",
            message="当前料架无同 DC/LC 兼容格位，也无可用空格",
        )

    def _material_from_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        six_in_one = context.get("six_in_one")
        return dict(cast("Mapping[str, Any]", six_in_one)) if isinstance(six_in_one, Mapping) else {}

    def _material_block_decision(
        self,
        material: Mapping[str, Any],
        *,
        barcode: str,
    ) -> SmtRackBinSchedulingDecision | None:
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
        return None

    def _active_rack_or_none(self, value: Any) -> Mapping[str, Any] | None:
        return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None

    def _rack_cells(self, active_rack: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw_cells: Any = (
            active_rack.get("cells") or active_rack.get("bin_cells") or active_rack.get("cell_snapshots") or []
        )
        if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
            return []
        return [
            cast("Mapping[str, Any]", cell) for cell in cast("Sequence[Any]", raw_cells) if isinstance(cell, Mapping)
        ]

    def _find_compatible_occupied_cell(
        self,
        cells: Sequence[Mapping[str, Any]],
        material: Mapping[str, Any],
        reel_size_kind: SmtReelSizeKind,
        *,
        reel_thickness: float | None,
    ) -> Mapping[str, Any] | None:
        for cell in cells:
            if not self._is_schedulable_cell(cell) or self._cell_status(cell) != "OCCUPIED":
                continue
            if not self._is_cell_compatible_with_reel(cell, reel_size_kind):
                continue
            if not self._cell_has_capacity_for_reel(cell, reel_thickness):
                continue
            if self._cell_matches_material(cell, material):
                return cell
        return None

    def _cell_matches_material(self, cell: Mapping[str, Any], material: Mapping[str, Any]) -> bool:
        if cell.get("DateCode") != material["DateCode"] or cell.get("LotCode") != material["LotCode"]:
            return False
        cell_identity = self._text_or_none(cell.get("material_identity_key"))
        incoming_identity = self._material_identity_key(material)
        if cell_identity is not None and incoming_identity is not None:
            return material_identity_keys_match(cell_identity, incoming_identity)
        cell_material = self._text_or_none(cell.get("HHPN") or cell.get("material_code"))
        incoming_material = self._text_or_none(material.get("HHPN") or material.get("material_code"))
        if cell_material is not None and incoming_material is not None and cell_material != incoming_material:
            return False
        cell_vendor = self._text_or_none(cell.get("MfrPN") or cell.get("vendor_code"))
        incoming_vendor = self._text_or_none(material.get("MfrPN") or material.get("vendor_code"))
        return not (cell_vendor is not None and incoming_vendor is not None and cell_vendor != incoming_vendor)

    def _material_identity_key(self, material: Mapping[str, Any]) -> str | None:
        material_code = self._text_or_none(material.get("HHPN") or material.get("material_code"))
        vendor_code = self._text_or_none(material.get("MfrPN") or material.get("vendor_code"))
        date_code = self._text_or_none(material.get("DateCode") or material.get("date_code"))
        lot_code = self._text_or_none(material.get("LotCode") or material.get("lot_code"))
        if material_code or date_code or lot_code:
            return f"MAT:{material_code or ''}:{vendor_code or ''}:{date_code or ''}:{lot_code or ''}"
        return None

    def _cell_has_capacity_for_reel(self, cell: Mapping[str, Any], reel_thickness: float | None) -> bool:
        cell_status = self._cell_status(cell)
        if cell_status in {"FULL", "FULL_SNAPSHOT", "CLOSED"}:
            return False

        remaining_depth = self._positive_float(
            self._first_present(cell, "remaining_depth_mm", "remaining_depth", "available_depth_mm")
        )
        if remaining_depth is not None:
            return self._depth_can_accept_reel(remaining_depth, reel_thickness)

        capacity_depth = self._positive_float(self._first_present(cell, "capacity_depth_mm", "max_depth_mm"))
        used_depth = self._positive_float(self._first_present(cell, "used_depth_mm", "stack_depth_mm"))
        if capacity_depth is not None:
            if used_depth is None:
                if cell_status in self.EMPTY_CELL_STATUSES:
                    used_depth = 0.0
                elif reel_thickness is None:
                    return capacity_depth > 0
                else:
                    return False
            remaining_depth = max(capacity_depth - used_depth, 0.0)
            return self._depth_can_accept_reel(remaining_depth, reel_thickness)

        return True

    def _depth_can_accept_reel(self, remaining_depth: float, reel_thickness: float | None) -> bool:
        if reel_thickness is None:
            return remaining_depth > 0
        return remaining_depth + 1e-9 >= reel_thickness

    def _find_first_empty_cell(
        self,
        cells: Sequence[Mapping[str, Any]],
        reel_size_kind: SmtReelSizeKind,
        *,
        reel_thickness: float | None = None,
    ) -> Mapping[str, Any] | None:
        for preferred_bin_type, preferred_suffixes in self._empty_cell_priority(reel_size_kind):
            for cell in cells:
                if not self._is_schedulable_cell(cell) or self._cell_status(cell) != "EMPTY":
                    continue
                if not self._cell_has_capacity_for_reel(cell, reel_thickness):
                    continue
                normalized = self.normalize_bin_location(cell)
                if normalized is None:
                    continue
                if normalized["bin_type"] == preferred_bin_type and self._cell_suffix(normalized) in preferred_suffixes:
                    return cell
        return None

    def _is_schedulable_cell(self, cell: Mapping[str, Any]) -> bool:
        if bool(cell.get("locked")) or bool(cell.get("disabled")):
            return False
        if self._cell_status(cell) in {"LOCKED", "DISABLED"}:
            return False
        return self.normalize_bin_location(cell) is not None

    def _is_cell_compatible_with_reel(self, cell: Mapping[str, Any], reel_size_kind: SmtReelSizeKind) -> bool:
        normalized = self.normalize_bin_location(cell)
        if normalized is None:
            return False

        bin_type = normalized["bin_type"]
        suffix = self._cell_suffix(normalized)
        if suffix is None:
            return False

        if reel_size_kind == "SEVEN_INCH":
            if bin_type == self.SIX_CELL_BIN_TYPE:
                return suffix in self.SEVEN_INCH_CELL_SUFFIXES
            return bin_type == self.THREE_CELL_BIN_TYPE and suffix in self.THREE_CELL_SEVEN_INCH_SUFFIXES

        return bin_type == self.THREE_CELL_BIN_TYPE and suffix in self.LARGE_CELL_SUFFIXES

    def _empty_cell_priority(self, reel_size_kind: SmtReelSizeKind) -> tuple[tuple[str, frozenset[str]], ...]:
        if reel_size_kind == "SEVEN_INCH":
            return (
                (self.SIX_CELL_BIN_TYPE, self.SEVEN_INCH_CELL_SUFFIXES),
                (self.THREE_CELL_BIN_TYPE, self.THREE_CELL_SEVEN_INCH_SUFFIXES),
            )
        return ((self.THREE_CELL_BIN_TYPE, self.LARGE_CELL_SUFFIXES),)

    def _cell_status(self, cell: Mapping[str, Any]) -> str:
        return str(cell.get("status") or "").upper()

    def normalize_bin_location(self, bin_location: Mapping[str, Any]) -> dict[str, Any] | None:
        """按 SMT 6/3 格箱物理规则规范化料格编码。"""

        if not all(bin_location.get(field) for field in self.REQUIRED_LOCATION_FIELDS):
            return None

        bin_id = str(bin_location["bin_id"]).strip()
        bin_type = self._canonical_bin_type(bin_location.get("bin_type"))
        suffix = self._cell_suffix(bin_location)
        if not bin_id or bin_type is None or suffix is None:
            return None
        if not self._is_valid_suffix_for_bin_type(bin_type, suffix):
            return None

        normalized = dict(bin_location)
        rack_id = self._text_or_none(normalized.get("rack_id")) or self._text_or_none(normalized.get("rack_code"))
        rack_slot_code = self._canonical_rack_slot_code(normalized.get("rack_slot_code") or normalized.get("slot_code"))
        rack_slot_location_code = self._text_or_none(
            normalized.get("rack_slot_location_code")
            or normalized.get("slot_location_code")
            or normalized.get("rack_slot_barcode")
            or normalized.get("location_code")
        )
        bin_orientation_code = self._text_or_none(
            normalized.get("bin_orientation_code")
            or normalized.get("bin_direction_code")
            or normalized.get("orientation_code")
        )

        if rack_id is not None:
            normalized["rack_id"] = rack_id
        if rack_slot_code is not None:
            normalized["rack_slot_code"] = rack_slot_code
            if rack_slot_location_code is None and rack_id is not None:
                rack_slot_location_code = self._canonical_rack_slot_location_code(rack_id, rack_slot_code)
        if rack_slot_location_code is not None:
            normalized["rack_slot_location_code"] = rack_slot_location_code
        normalized["bin_id"] = bin_id
        normalized["bin_orientation_code"] = bin_orientation_code or f"{bin_id}-A"
        normalized["bin_type"] = bin_type
        normalized["bin_cell_location"] = self._canonical_cell_location(bin_id, suffix)
        normalized["bin_cell_index"] = suffix
        return normalized

    def is_bin_location_compatible_with_reel(self, bin_location: Mapping[str, Any], reel_diameter: Any) -> bool:
        """判断已分配料格是否匹配当前料盘尺寸。"""

        normalized = self.normalize_bin_location(bin_location)
        reel_size_kind = self._reel_size_kind({"reel_diameter": reel_diameter})
        return (
            normalized is not None
            and reel_size_kind is not None
            and self._is_cell_compatible_with_reel(normalized, reel_size_kind)
        )

    def _canonical_bin_type(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        compact_text = text.replace(" ", "")
        return self.BIN_TYPE_ALIASES.get(compact_text) or self.BIN_TYPE_ALIASES.get(compact_text.upper())

    def _cell_suffix(self, cell: Mapping[str, Any]) -> str | None:
        raw_value = cell.get("bin_cell_location")
        if raw_value is None:
            return None
        text = str(raw_value).strip()
        if not text:
            return None
        match = re.search(r"[-_](\d+)$", text)
        suffix = match.group(1) if match is not None else text
        return suffix if suffix.isdigit() else None

    def _is_valid_suffix_for_bin_type(self, bin_type: str, suffix: str) -> bool:
        if bin_type == self.SIX_CELL_BIN_TYPE:
            return suffix in self.SEVEN_INCH_CELL_SUFFIXES
        if bin_type == self.THREE_CELL_BIN_TYPE:
            return suffix in self.THREE_CELL_SEVEN_INCH_SUFFIXES or suffix in self.LARGE_CELL_SUFFIXES
        return False

    def _canonical_cell_location(self, bin_id: str, suffix: str) -> str:
        return f"{bin_id}-{suffix}"

    def _canonical_rack_slot_code(self, value: Any) -> str | None:
        text = self._text_or_none(value)
        if text is None:
            return None
        upper_text = text.upper()
        if upper_text in self.RACK_SLOT_CODES:
            return upper_text
        if upper_text in {"A01", "A1", "S1", "1"}:
            return "A"
        if upper_text in {"B01", "B1", "S2", "2"}:
            return "B"
        if upper_text in {"C01", "C1", "S3", "3"}:
            return "C"
        if upper_text in {"D01", "D1", "S4", "4"}:
            return "D"
        return None

    def _canonical_rack_slot_location_code(self, rack_id: str, rack_slot_code: str) -> str:
        side = self.RACK_SLOT_SIDE_BY_CODE.get(rack_slot_code, "0")
        return f"{rack_id}-1{rack_slot_code}-{side}"

    def _text_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _first_present(self, values: Mapping[str, Any], *keys: str) -> Any | None:
        for key in keys:
            value = values.get(key)
            if value is not None:
                return value
        return None

    def _positive_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _reel_thickness(self, context: Mapping[str, Any]) -> float | None:
        return self._positive_float(context.get("reel_thickness") or context.get("thickness_mm"))

    def _reel_size_kind(self, context: Mapping[str, Any]) -> SmtReelSizeKind | None:
        value = context.get("reel_diameter") or context.get("reel_size") or context.get("diameter")
        if value is None:
            return None

        text = str(value).strip().lower()
        reel_size_kind: SmtReelSizeKind | None = None
        if "13" in text or "15" in text:
            reel_size_kind = "LARGE"
        elif "7" in text and not re.search(r"\d{3,}", text):
            reel_size_kind = "SEVEN_INCH"
        else:
            match = re.search(r"\d+(?:\.\d+)?", text)
            if match is not None:
                diameter = float(match.group())
                if diameter <= 8.5:
                    reel_size_kind = "SEVEN_INCH"
                elif 10 <= diameter <= 20:
                    reel_size_kind = "LARGE"
                elif diameter <= 220:
                    reel_size_kind = "SEVEN_INCH"
                else:
                    reel_size_kind = "LARGE"
        return reel_size_kind

    def _bin_location(self, cell: Mapping[str, Any], active_rack: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_bin_location(cell)
        if normalized is None:
            raise ValueError("invalid SMT bin cell location")

        rack_id = normalized.get("rack_id") or active_rack.get("rack_id") or active_rack.get("rack_code")
        result: dict[str, Any] = {
            "rack_id": rack_id,
            "rack_slot_code": normalized.get("rack_slot_code"),
            "rack_slot_location_code": normalized.get("rack_slot_location_code"),
            "bin_id": normalized["bin_id"],
            "bin_orientation_code": normalized["bin_orientation_code"],
            "bin_type": normalized["bin_type"],
            "bin_cell_location": normalized["bin_cell_location"],
            "bin_cell_index": normalized["bin_cell_index"],
        }
        for key in (
            "material_identity_key",
            "reel_count",
            "used_depth_mm",
            "remaining_depth_mm",
        ):
            if key in normalized:
                result[key] = normalized[key]
        capacity_depth = self._positive_float(normalized.get("capacity_depth_mm"))
        if capacity_depth is None:
            capacity_depth = self._positive_float(normalized.get("max_depth_mm"))
        if capacity_depth is not None:
            result["capacity_depth_mm"] = capacity_depth
        reel_count = self._positive_float(normalized.get("reel_count"))
        result["expected_stack_height"] = int(reel_count or 0) + 1
        return result

    def _rack_operation_decision(
        self,
        *,
        context: Mapping[str, Any],
        material: Mapping[str, Any],
        active_rack: Mapping[str, Any] | None,
        reason_code: str,
        message: str,
    ) -> SmtRackBinSchedulingDecision:
        if active_rack is not None and len(self._rack_bin_snapshots(active_rack)) != len(self.RACK_SLOT_CODES):
            return self._invalid_active_rack_snapshot_decision()

        target_code = self._target_code(context)
        if target_code is None:
            return SmtRackBinSchedulingDecision(
                kind="BLOCKED",
                reason_code="RACK_OPERATION_TARGET_MISSING",
                message="SMT 货架 operation 请求缺少 WMS/RCS 目标地址配置",
            )

        operation_key = self._operation_key(context=context, material=material, active_rack=active_rack)
        work_position_code = self._work_position_code(context)
        payload: dict[str, Any] = {
            "request_type": self.RACK_OPERATION_REQUEST_TYPE,
            "operation_type": self.RACK_OPERATION_TYPE,
            "operation_key": operation_key,
            "target_code": target_code,
            "work_position_code": work_position_code,
            "new_rack_kind": self._new_rack_kind(context),
            "move_out_target_position_role": self._move_out_target_position_role(context),
            "material": dict(material),
            "current_rack_snapshot": dict(active_rack or {}),
            "actions": list(self.ALLOCATE_RACK_ACTIONS),
            "reason_code": reason_code,
        }
        if active_rack is not None:
            rack_id = self._active_rack_id(active_rack)
            bin_snapshots = self._rack_bin_snapshots(active_rack)
            token = self._context_dispatch_token(context) or f"{material.get('PkgID')}:{rack_id}"
            payload.update(
                {
                    "actions": list(self.REPLACE_RACK_ACTIONS),
                    "single_layer_rack_id": rack_id,
                    "single_layer_rack_code": rack_id,
                    "source_classifier_line_code": self._workline_code(context),
                    "source_task_batch_id": token,
                    "move_out_reason_code": reason_code,
                    "active_rack_bin_snapshots": bin_snapshots,
                }
            )
        trace_id = self._trace_id(context)
        if trace_id is not None:
            payload["trace_id"] = trace_id

        return SmtRackBinSchedulingDecision(
            kind="RACK_OPERATION_REQUIRED",
            rack_operation_request=SmtRackOperationRequest(
                operation_key=operation_key,
                target_code=target_code,
                payload=payload,
            ),
            reason_code=reason_code,
            message=message,
        )

    def _invalid_active_rack_snapshot_decision(self) -> SmtRackBinSchedulingDecision:
        return SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code="ACTIVE_RACK_SNAPSHOT_INVALID",
            message="SMT 可用货架快照必须包含 A/B/C/D 4 个料箱",
        )

    def _non_empty_active_rack_decision(self) -> SmtRackBinSchedulingDecision:
        return SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code="ACTIVE_RACK_NOT_EMPTY",
            message="SMT 可用货架料箱必须全为空料格",
        )

    def _requires_empty_active_rack(self, context: Mapping[str, Any]) -> bool:
        rack_operation = context.get("rack_operation")
        if not isinstance(rack_operation, Mapping):
            return False
        status = self._text_or_none(rack_operation.get("status"))
        return status is not None and status.upper() in {"SUCCEEDED", "ARRIVED"}

    def _pending_rack_operation_decision(self, context: Mapping[str, Any]) -> SmtRackBinSchedulingDecision | None:
        waiting_operation_key = self._text_or_none(context.get("waiting_rack_operation_key"))
        rack_operation = context.get("rack_operation")
        if not isinstance(rack_operation, Mapping):
            return None
        status = self._text_or_none(rack_operation.get("status"))
        operation_key = self._text_or_none(rack_operation.get("operation_key"))
        if status is None or status.upper() not in self.PENDING_RACK_OPERATION_STATUSES:
            return None
        if waiting_operation_key is not None and operation_key is not None and waiting_operation_key != operation_key:
            return None
        return SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code="RACK_OPERATION_PENDING",
            message="SMT 货架 operation 仍在等待中，不重复创建换架请求",
        )

    def _active_rack_is_empty(
        self,
        active_rack: Mapping[str, Any],
        bin_snapshots: Sequence[Mapping[str, Any]],
    ) -> bool:
        if any(not self._rack_bin_snapshot_is_empty(snapshot) for snapshot in bin_snapshots):
            return False
        return all(self._rack_cell_is_empty(cell) for cell in self._rack_cells(active_rack))

    def _rack_bin_snapshot_is_empty(self, snapshot: Mapping[str, Any]) -> bool:
        usage = self._release_bin_usage(snapshot)
        status = self._release_bin_status(snapshot.get("status") or snapshot.get("bin_execution_status"), usage=usage)
        return usage == 0 and status == "EMPTY_VERIFIED"

    def _rack_cell_is_empty(self, cell: Mapping[str, Any]) -> bool:
        return self._cell_status(cell) in self.EMPTY_CELL_STATUSES

    def _target_code(self, context: Mapping[str, Any]) -> str | None:
        endpoint_code_keys = (
            "rack_operation_target_code",
            "wms_rcs_target_code",
        )
        legacy_url_keys = (
            "wms_rcs_rack_operation_url",
            "wms_rcs_rack_supply_url",
        )
        value = self._first_text(context, endpoint_code_keys)
        if value is not None:
            return value

        config = context.get("config")
        if isinstance(config, Mapping):
            value = self._first_text(cast("Mapping[str, Any]", config), endpoint_code_keys)
            if value is not None:
                return value

        if self._first_text(context, legacy_url_keys) is not None:
            return WMS_RCS_RACK_OPERATION_ENDPOINT
        if (
            isinstance(config, Mapping)
            and self._first_text(cast("Mapping[str, Any]", config), legacy_url_keys) is not None
        ):
            return WMS_RCS_RACK_OPERATION_ENDPOINT
        # SANDBOX 可能不携带物理 WMS/RCS URL，实际派发由端点注册表解析逻辑端点。
        return WMS_RCS_RACK_OPERATION_ENDPOINT

    def _rack_bin_snapshots(self, active_rack: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_bins = active_rack.get("bins") or active_rack.get("bin_snapshots")
        if isinstance(raw_bins, Sequence) and not isinstance(raw_bins, (str, bytes)):
            snapshots = [
                normalized
                for item in cast("Sequence[Any]", raw_bins)
                if isinstance(item, Mapping)
                for normalized in [self._normalize_rack_bin_snapshot(cast("Mapping[str, Any]", item))]
                if normalized is not None
            ]
            four_slot_snapshots = self._four_slot_rack_bin_snapshots(snapshots)
            if four_slot_snapshots:
                return four_slot_snapshots

        return self._rack_bin_snapshots_from_cells(active_rack)

    def _normalize_rack_bin_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
        slot_code = self._canonical_rack_slot_code(snapshot.get("slot_code") or snapshot.get("rack_slot_code"))
        bin_id = self._text_or_none(snapshot.get("bin_id") or snapshot.get("bin_code"))
        usage = self._release_bin_usage(snapshot)
        status = self._release_bin_status(snapshot.get("status") or snapshot.get("bin_execution_status"), usage=usage)
        if slot_code is None or bin_id is None or usage is None or status is None:
            return None

        normalized = dict(snapshot)
        normalized["slot_code"] = slot_code
        normalized["bin_id"] = bin_id
        normalized["bin_code"] = bin_id
        normalized["status"] = status
        normalized["bin_execution_status"] = status
        normalized["usage"] = usage
        normalized["usage_snapshot"] = usage
        return normalized

    def _rack_bin_snapshots_from_cells(self, active_rack: Mapping[str, Any]) -> list[dict[str, Any]]:
        cells_by_slot: dict[str, list[dict[str, Any]]] = {}
        bin_ids_by_slot: dict[str, set[str]] = {}
        for cell in self._rack_cells(active_rack):
            normalized = self.normalize_bin_location(cell)
            if normalized is None:
                continue
            slot_code = self._canonical_rack_slot_code(normalized.get("rack_slot_code"))
            if slot_code is None:
                continue
            cells_by_slot.setdefault(slot_code, []).append(normalized)
            bin_ids_by_slot.setdefault(slot_code, set()).add(str(normalized["bin_id"]))

        if not cells_by_slot:
            return []
        if any(len(bin_ids) != 1 for bin_ids in bin_ids_by_slot.values()):
            return []

        snapshots: list[dict[str, Any]] = []
        for slot_code in self.RACK_SLOT_CODES:
            if slot_code not in cells_by_slot:
                continue
            cells = cells_by_slot[slot_code]
            snapshots.append(self._rack_bin_snapshot_from_cells(slot_code, cells))
        return self._four_slot_rack_bin_snapshots(snapshots)

    def _rack_bin_snapshot_from_cells(self, slot_code: str, cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        first_cell = cells[0]
        bin_id = str(first_cell["bin_id"])
        bin_type = self._text_or_none(first_cell.get("bin_type"))
        usage = self._rack_bin_usage(cells)
        status = self._rack_bin_status(cells, usage)
        return {
            "slot_code": slot_code,
            "bin_id": bin_id,
            "bin_code": bin_id,
            "bin_type": bin_type,
            "bin_type_code": bin_type,
            "status": status,
            "bin_execution_status": status,
            "usage": usage,
            "usage_snapshot": usage,
            "rack_slot_location_code": first_cell.get("rack_slot_location_code"),
            "bin_orientation_code": first_cell.get("bin_orientation_code"),
            "cell_count": self._rack_bin_capacity(cells),
            "occupied_cell_count": self._occupied_cell_count(cells),
        }

    def _four_slot_rack_bin_snapshots(self, snapshots: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        by_slot: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            slot_code = str(snapshot["slot_code"])
            if slot_code in by_slot:
                return []
            by_slot[slot_code] = snapshot

        if not by_slot:
            return []
        if set(by_slot) != set(self.RACK_SLOT_CODES):
            return []
        return [by_slot[slot_code] for slot_code in self.RACK_SLOT_CODES]

    def _release_bin_usage(self, snapshot: Mapping[str, Any]) -> float | None:
        value = snapshot.get("usage") if "usage" in snapshot else snapshot.get("usage_snapshot")
        if value is None:
            return None
        try:
            usage = float(value)
        except (TypeError, ValueError):
            return None
        if usage < 0 or usage > 1:
            return None
        return usage

    def _release_bin_status(self, value: Any, *, usage: float | None) -> str | None:
        text = self._text_or_none(value)
        status = self.RELEASE_BIN_STATUS_ALIASES.get(text.upper(), text.upper()) if text is not None else None
        if status is None and usage is not None:
            if usage >= 1:
                status = "FULL"
            elif usage > 0:
                status = "IN_USE"
            else:
                status = "EMPTY_VERIFIED"
        return status if status in self.RELEASE_BIN_STATUSES else None

    def _rack_bin_usage(self, cells: Sequence[Mapping[str, Any]]) -> float:
        depth_usage = self._rack_bin_depth_usage(cells)
        if depth_usage is not None:
            return depth_usage
        capacity = self._rack_bin_capacity(cells)
        if capacity <= 0:
            return 0.0
        return min(self._occupied_cell_count(cells) / capacity, 1.0)

    def _rack_bin_depth_usage(self, cells: Sequence[Mapping[str, Any]]) -> float | None:
        used_total = 0.0
        capacity_total = 0.0
        occupied_statuses = {"OCCUPIED", "IN_USE", "FULL", "FULL_SNAPSHOT", "CLOSED"}
        for cell in cells:
            capacity = self._positive_float(self._first_present(cell, "capacity_depth_mm", "max_depth_mm"))
            used = self._positive_float(self._first_present(cell, "used_depth_mm", "stack_depth_mm"))
            if self._cell_status(cell) in occupied_statuses and (capacity is None or used is None):
                return None
            if capacity is None or capacity <= 0:
                continue
            capacity_total += capacity
            used_total += used or 0.0
        if capacity_total <= 0:
            return None
        return min(used_total / capacity_total, 1.0)

    def _rack_bin_capacity(self, cells: Sequence[Mapping[str, Any]]) -> int:
        if not cells:
            return 0
        bin_type = self._canonical_bin_type(cells[0].get("bin_type"))
        if bin_type == self.SIX_CELL_BIN_TYPE:
            return len(self.SEVEN_INCH_CELL_SUFFIXES)
        if bin_type == self.THREE_CELL_BIN_TYPE:
            return len(self.THREE_CELL_SEVEN_INCH_SUFFIXES | self.LARGE_CELL_SUFFIXES)
        return len(cells)

    def _occupied_cell_count(self, cells: Sequence[Mapping[str, Any]]) -> int:
        occupied_statuses = {"OCCUPIED", "IN_USE", "FULL", "FULL_SNAPSHOT", "CLOSED"}
        return sum(1 for cell in cells if self._cell_status(cell) in occupied_statuses)

    def _rack_bin_status(self, cells: Sequence[Mapping[str, Any]], usage: float) -> str:
        statuses = {self._cell_status(cell) for cell in cells}
        if statuses & {"EXCEPTION", "ERROR", "ABNORMAL"}:
            return "EXCEPTION"
        if statuses and statuses <= {"DISABLED"}:
            return "DISABLED"
        if statuses and statuses <= {"LOCKED"}:
            return "LOCKED"
        if usage >= 1:
            return "FULL"
        if usage > 0:
            return "IN_USE"
        return "EMPTY_VERIFIED"

    def _active_rack_id(self, active_rack: Mapping[str, Any]) -> str:
        return str(active_rack.get("rack_id") or active_rack.get("rack_code") or "")

    def _workline_code(self, context: Mapping[str, Any]) -> str | None:
        value = self._first_text(context, ("workline_code", "line_code", "source_classifier_line_code"))
        if value is not None:
            return value

        workline = context.get("workline")
        return (
            self._first_text(cast("Mapping[str, Any]", workline), ("line_code", "workline_code", "code"))
            if isinstance(workline, Mapping)
            else None
        )

    def _work_position_code(self, context: Mapping[str, Any]) -> str:
        rack_operation = context.get("rack_operation")
        if isinstance(rack_operation, Mapping):
            value = self._first_text(
                cast("Mapping[str, Any]", rack_operation),
                ("work_position_code", "target_position_code", "position_code"),
            )
            if value is not None:
                return value

        return self._first_text(context, ("work_position_code", "target_position_code", "position_code")) or (
            self.DEFAULT_WORK_POSITION_CODE
        )

    def _new_rack_kind(self, context: Mapping[str, Any]) -> str:
        return self._first_text(context, ("new_rack_kind", "rack_kind")) or self.DEFAULT_NEW_RACK_KIND

    def _move_out_target_position_role(self, context: Mapping[str, Any]) -> str:
        return (
            self._first_text(context, ("move_out_target_position_role", "target_position_role"))
            or self.DEFAULT_MOVE_OUT_TARGET_POSITION_ROLE
        )

    def _trace_id(self, context: Mapping[str, Any]) -> str | None:
        keys = ("trace_id", "trace_code")
        value = self._first_text(context, keys)
        if value is not None:
            return value

        session = context.get("session")
        return self._first_text(cast("Mapping[str, Any]", session), keys) if isinstance(session, Mapping) else None

    @staticmethod
    def _first_text(values: Mapping[str, Any], keys: Sequence[str]) -> str | None:
        for key in keys:
            value = values.get(key)
            if value:
                return str(value)
        return None

    def _operation_key(
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
        return f"external:smt_rack_bin:{token}:RACK_OPERATION"

    def _context_dispatch_token(self, context: Mapping[str, Any]) -> str | None:
        value = self._first_text(
            context,
            (
                "operation_key",
                "trace_id",
                "trace_code",
                "session_id",
                "session_code",
                "workline_session_id",
            ),
        )
        if value is not None:
            return value

        session = context.get("session")
        return (
            self._first_text(
                cast("Mapping[str, Any]", session), ("operation_key", "trace_id", "session_id", "id", "code")
            )
            if isinstance(session, Mapping)
            else None
        )


smt_rack_bin_scheduling_service = SmtRackBinSchedulingService()


__all__ = [
    "SmtRackBinSchedulingDecision",
    "SmtRackBinSchedulingDecisionKind",
    "SmtRackBinSchedulingService",
    "SmtRackOperationRequest",
    "smt_rack_bin_scheduling_service",
]
