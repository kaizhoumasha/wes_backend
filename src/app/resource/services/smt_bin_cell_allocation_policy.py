"""SMT 料格 Decimal 容量分配纯策略。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from src.app.resource.services.material_identity import material_identity_keys_match

SmtBinCellAllocationKind = Literal["ALLOCATED", "REJECTED"]


@dataclass(frozen=True, slots=True)
class SmtBinCellAllocationResult:
    """SMT 料格分配结果。"""

    kind: SmtBinCellAllocationKind
    target_bin_code: str | None = None
    target_cell_index: str | None = None
    source_snapshot_version: str | None = None
    allocation_snapshot_version: str | None = None
    reason_code: str | None = None
    message: str | None = None
    capacity_evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """兼容 Task 1 旧字段，并提供更明确的分配快照版本字段。"""

        if self.source_snapshot_version is None and self.allocation_snapshot_version is not None:
            object.__setattr__(self, "source_snapshot_version", self.allocation_snapshot_version)
        elif self.allocation_snapshot_version is None and self.source_snapshot_version is not None:
            object.__setattr__(self, "allocation_snapshot_version", self.source_snapshot_version)
        elif (
            self.source_snapshot_version is not None
            and self.allocation_snapshot_version is not None
            and self.source_snapshot_version != self.allocation_snapshot_version
        ):
            raise ValueError("source_snapshot_version must equal allocation_snapshot_version")


@dataclass(frozen=True, slots=True)
class _DepthValue:
    value: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class _CellDepth:
    capacity: _DepthValue
    used: _DepthValue

    @property
    def remaining(self) -> Decimal:
        return self.capacity.value - self.used.value


class SmtBinCellAllocationPolicy:
    """按同物料优先、空格兜底规则选择 SMT 目标料格。"""

    OCCUPIED_STATUSES: frozenset[str] = frozenset({"OCCUPIED", "IN_USE"})
    EMPTY_STATUSES: frozenset[str] = frozenset({"EMPTY", "EMPTY_VERIFIED", "AVAILABLE"})
    PLAIN_DECIMAL_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

    def allocate(
        self,
        *,
        active_snapshot: Mapping[str, Any],
        material_identity_key: str,
        reel_thickness_mm: Any,
    ) -> SmtBinCellAllocationResult:
        """基于 active snapshot 选择目标料格或返回结构化拒绝原因。"""

        snapshot_version = self._text_or_none(active_snapshot.get("snapshot_version"))
        reel_thickness = self._positive_decimal(reel_thickness_mm)
        if reel_thickness is None:
            return SmtBinCellAllocationResult(
                kind="REJECTED",
                source_snapshot_version=snapshot_version,
                reason_code="INVALID_REEL_THICKNESS",
                message="料盘厚度缺失或不是正 Decimal",
                capacity_evidence={"reel_thickness_mm": self._source_text(reel_thickness_mm)},
            )

        cells = sorted(self._snapshot_cells(active_snapshot), key=self._cell_sort_key)
        parsed_depths: dict[int, _CellDepth] = {}
        for cell in cells:
            depth = self._cell_depth(cell)
            target_bin_code = self._target_bin_code(cell)
            target_cell_index = self._target_cell_index(cell)
            if depth is None:
                return self._rejected_cell_depth(
                    reason_code="INVALID_CELL_DEPTH",
                    message="料格容量或已用深度缺失，或不是非负 Decimal",
                    snapshot_version=snapshot_version,
                    cell=cell,
                )
            if depth.used.value > depth.capacity.value:
                return SmtBinCellAllocationResult(
                    kind="REJECTED",
                    target_bin_code=target_bin_code,
                    target_cell_index=target_cell_index,
                    source_snapshot_version=snapshot_version,
                    reason_code="PROJECTION_INCONSISTENT",
                    message="料格投影已用深度大于总容量",
                    capacity_evidence=self._capacity_evidence(
                        cell=cell,
                        depth=depth,
                        reel_thickness=reel_thickness,
                        selection_reason="projection-inconsistent",
                    ),
                )
            parsed_depths[id(cell)] = depth

        compatible_cells_checked = 0
        empty_cells_checked = 0
        for cell in cells:
            if not self._is_occupied(cell):
                continue
            if not material_identity_keys_match(
                self._text_or_none(cell.get("material_identity_key")), material_identity_key
            ):
                continue
            compatible_cells_checked += 1
            depth = parsed_depths[id(cell)]
            if depth.remaining >= reel_thickness.value:
                return self._allocated(
                    cell=cell,
                    depth=depth,
                    reel_thickness=reel_thickness,
                    snapshot_version=snapshot_version,
                    selection_reason="compatible-material",
                )

        for cell in cells:
            if not self._is_empty(cell):
                continue
            empty_cells_checked += 1
            depth = parsed_depths[id(cell)]
            if depth.remaining >= reel_thickness.value:
                return self._allocated(
                    cell=cell,
                    depth=depth,
                    reel_thickness=reel_thickness,
                    snapshot_version=snapshot_version,
                    selection_reason="empty-cell",
                )

        return SmtBinCellAllocationResult(
            kind="REJECTED",
            source_snapshot_version=snapshot_version,
            reason_code="NO_CAPACITY",
            message="当前快照无同物料且容量足够的料格，也无容量足够的空格",
            capacity_evidence={
                "reel_thickness_mm": reel_thickness.source,
                "compatible_cells_checked": str(compatible_cells_checked),
                "empty_cells_checked": str(empty_cells_checked),
            },
        )

    def _allocated(
        self,
        *,
        cell: Mapping[str, Any],
        depth: _CellDepth,
        reel_thickness: _DepthValue,
        snapshot_version: str | None,
        selection_reason: str,
    ) -> SmtBinCellAllocationResult:
        return SmtBinCellAllocationResult(
            kind="ALLOCATED",
            target_bin_code=self._target_bin_code(cell),
            target_cell_index=self._target_cell_index(cell),
            source_snapshot_version=snapshot_version,
            capacity_evidence=self._capacity_evidence(
                cell=cell,
                depth=depth,
                reel_thickness=reel_thickness,
                selection_reason=selection_reason,
            ),
        )

    def _rejected_cell_depth(
        self,
        *,
        reason_code: str,
        message: str,
        snapshot_version: str | None,
        cell: Mapping[str, Any],
    ) -> SmtBinCellAllocationResult:
        return SmtBinCellAllocationResult(
            kind="REJECTED",
            target_bin_code=self._target_bin_code(cell),
            target_cell_index=self._target_cell_index(cell),
            source_snapshot_version=snapshot_version,
            reason_code=reason_code,
            message=message,
            capacity_evidence={
                "capacity_depth_mm": self._source_text(cell.get("capacity_depth_mm")),
                "used_depth_mm": self._source_text(cell.get("used_depth_mm")),
            },
        )

    def _capacity_evidence(
        self,
        *,
        cell: Mapping[str, Any],
        depth: _CellDepth,
        reel_thickness: _DepthValue,
        selection_reason: str,
    ) -> dict[str, str]:
        return {
            "selection_reason": selection_reason,
            "cell_status": self._cell_status(cell),
            "reel_thickness_mm": reel_thickness.source,
            "capacity_depth_mm": depth.capacity.source,
            "used_depth_mm": depth.used.source,
            "remaining_depth_mm": self._decimal_text(depth.remaining),
            "projected_used_depth_mm": self._decimal_text(depth.used.value + reel_thickness.value),
        }

    def _cell_depth(self, cell: Mapping[str, Any]) -> _CellDepth | None:
        capacity = self._non_negative_decimal(cell.get("capacity_depth_mm"))
        used = self._non_negative_decimal(cell.get("used_depth_mm"))
        if capacity is None or used is None:
            return None
        return _CellDepth(capacity=capacity, used=used)

    def _positive_decimal(self, value: Any) -> _DepthValue | None:
        parsed = self._decimal_value(value)
        if parsed is None or parsed.value <= 0:
            return None
        return parsed

    def _non_negative_decimal(self, value: Any) -> _DepthValue | None:
        parsed = self._decimal_value(value)
        if parsed is None or parsed.value < 0:
            return None
        return parsed

    def _decimal_value(self, value: Any) -> _DepthValue | None:
        source = self._decimal_source_text(value)
        if source is None:
            return None
        try:
            decimal_value = Decimal(source)
        except InvalidOperation:
            return None
        if not decimal_value.is_finite():
            return None
        return _DepthValue(value=decimal_value, source=source)

    def _snapshot_cells(self, active_snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw_cells = self._authoritative_cells_value(active_snapshot)
        if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
            return []
        return [
            cast("Mapping[str, Any]", cell) for cell in cast("Sequence[Any]", raw_cells) if isinstance(cell, Mapping)
        ]

    def _authoritative_cells_value(self, active_snapshot: Mapping[str, Any]) -> Any:
        for key in ("cells", "bin_cells", "cell_snapshots"):
            if key in active_snapshot:
                return active_snapshot[key]
        return []

    def _cell_sort_key(self, cell: Mapping[str, Any]) -> tuple[int, str, str]:
        return (
            self._allocation_priority(cell),
            self._target_bin_code(cell) or "",
            self._target_cell_index(cell) or self._text_or_none(cell.get("bin_cell_location")) or "",
        )

    def _allocation_priority(self, cell: Mapping[str, Any]) -> int:
        raw_priority = self._text_or_none(cell.get("allocation_priority"))
        if raw_priority is None:
            return 0
        try:
            return int(raw_priority)
        except ValueError:
            return 0

    def _is_occupied(self, cell: Mapping[str, Any]) -> bool:
        return self._cell_status(cell) in self.OCCUPIED_STATUSES

    def _is_empty(self, cell: Mapping[str, Any]) -> bool:
        return self._cell_status(cell) in self.EMPTY_STATUSES

    def _cell_status(self, cell: Mapping[str, Any]) -> str:
        return str(cell.get("status") or "").strip().upper()

    def _target_bin_code(self, cell: Mapping[str, Any]) -> str | None:
        return self._text_or_none(cell.get("bin_code") or cell.get("bin_id"))

    def _target_cell_index(self, cell: Mapping[str, Any]) -> str | None:
        return self._text_or_none(cell.get("bin_cell_index") or cell.get("cell_index"))

    def _text_or_none(self, value: Any) -> str | None:
        text = self._source_text(value)
        return text or None

    def _source_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _decimal_source_text(self, value: Any) -> str | None:
        if isinstance(value, (bool, float)):
            return None
        if isinstance(value, Decimal):
            source = format(value, "f")
        elif isinstance(value, int):
            source = str(value)
        elif isinstance(value, str):
            source = value.strip()
        else:
            return None
        if not source or not self.PLAIN_DECIMAL_PATTERN.fullmatch(source):
            return None
        return source

    def _decimal_text(self, value: Decimal) -> str:
        return format(value, "f")
