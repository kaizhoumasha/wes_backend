"""SMT usage 统一解析与阈值策略。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from .smt_inbound_handoff_reason import SmtInboundHandoffReasonCode

SmtUsageBand = Literal["DIRECT_SORTING", "PREFERRED_FULL_BOX_EXCHANGE", "REQUIRE_FULL_BOX_EXCHANGE"]


@dataclass(frozen=True, slots=True)
class SmtUsageResult:
    """SMT usage 解析结果。"""

    valid: bool
    usage: float | None = None
    failure_code: str | None = None
    message: str | None = None


class SmtUsagePolicy:
    """SMT 料箱 usage 解析与满箱交换阈值。"""

    DIRECT_SORTING_THRESHOLD = 0.5
    REQUIRE_FULL_BOX_EXCHANGE_THRESHOLD = 0.8
    RELEASE_USAGE_KEYS = ("usage", "usage_snapshot", "bin_usage")
    OCCUPIED_CELL_STATUSES = frozenset({"OCCUPIED", "IN_USE", "FULL", "FULL_SNAPSHOT", "CLOSED"})

    def resolve_release_bin_usage(self, snapshot: Mapping[str, Any]) -> SmtUsageResult:
        """解析 release bin 快照中的 `0..1` usage。"""

        found = False
        raw_value: Any = None
        for key in self.RELEASE_USAGE_KEYS:
            if key in snapshot:
                found = True
                raw_value = snapshot.get(key)
                break
        if not found:
            return self._invalid("release 快照缺少 usage 字段")
        return self._normalize_usage(raw_value)

    def resolve_rack_bin_usage(
        self,
        cells: Sequence[Mapping[str, Any]],
        *,
        capacity_count: int | None = None,
    ) -> SmtUsageResult:
        """按料格快照计算 rack bin usage，优先使用深度口径。"""

        depth_usage = self._rack_bin_depth_usage(cells)
        if depth_usage is not None:
            return SmtUsageResult(valid=True, usage=depth_usage)

        capacity = capacity_count if capacity_count is not None else len(cells)
        if capacity <= 0:
            return SmtUsageResult(valid=True, usage=0.0)
        return SmtUsageResult(valid=True, usage=min(self._occupied_cell_count(cells) / capacity, 1.0))

    def usage_band(self, usage: float) -> SmtUsageBand:
        """按 SPEC 阈值把 usage 归入下一步业务动作区间。"""

        if usage >= self.REQUIRE_FULL_BOX_EXCHANGE_THRESHOLD:
            return "REQUIRE_FULL_BOX_EXCHANGE"
        if usage >= self.DIRECT_SORTING_THRESHOLD:
            return "PREFERRED_FULL_BOX_EXCHANGE"
        return "DIRECT_SORTING"

    def _normalize_usage(self, value: Any) -> SmtUsageResult:
        if value is None or isinstance(value, bool):
            return self._invalid("usage 不能为空或 bool")
        if isinstance(value, str) and not value.strip():
            return self._invalid("usage 不能为空字符串")
        try:
            usage = float(value)
        except (TypeError, ValueError):
            return self._invalid("usage 必须是 0..1 数值")
        if not math.isfinite(usage) or usage < 0 or usage > 1:
            return self._invalid("usage 必须位于 0..1 范围内")
        return SmtUsageResult(valid=True, usage=usage)

    def _rack_bin_depth_usage(self, cells: Sequence[Mapping[str, Any]]) -> float | None:
        used_total = Decimal("0")
        capacity_total = Decimal("0")
        for cell in cells:
            capacity = self._non_negative_decimal(self._first_present(cell, "capacity_depth_mm", "max_depth_mm"))
            used = self._non_negative_decimal(self._first_present(cell, "used_depth_mm", "stack_depth_mm"))
            if self._cell_status(cell) in self.OCCUPIED_CELL_STATUSES and (capacity is None or used is None):
                return None
            if capacity is None or capacity <= 0:
                continue
            capacity_total += capacity
            used_total += used or Decimal("0")
        if capacity_total <= 0:
            return None
        return float(min(used_total / capacity_total, Decimal("1")))

    def _occupied_cell_count(self, cells: Sequence[Mapping[str, Any]]) -> int:
        return sum(1 for cell in cells if self._cell_status(cell) in self.OCCUPIED_CELL_STATUSES)

    @staticmethod
    def _cell_status(cell: Mapping[str, Any]) -> str:
        return str(cell.get("status") or cell.get("cell_status") or "").strip().upper()

    @staticmethod
    def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _non_negative_decimal(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if decimal < 0 or not decimal.is_finite():
            return None
        return decimal

    @staticmethod
    def _invalid(message: str) -> SmtUsageResult:
        return SmtUsageResult(
            valid=False,
            failure_code=SmtInboundHandoffReasonCode.USAGE_INVALID.value,
            message=message,
        )


SMT_USAGE_POLICY = SmtUsagePolicy()


__all__ = [
    "SMT_USAGE_POLICY",
    "SmtUsageBand",
    "SmtUsagePolicy",
    "SmtUsageResult",
]
