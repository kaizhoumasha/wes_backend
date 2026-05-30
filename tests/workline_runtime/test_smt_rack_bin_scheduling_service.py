"""SMT 货架/料箱调度领域服务测试。"""

from types import SimpleNamespace

import pytest

from src.app.resource.services import (
    SmtRackBinSchedulingDecision,
    SmtRackBinSchedulingService,
    smt_rack_bin_scheduling_service,
)
from src.app.wms_integration.models import QueryInventoryRequest
from src.workline_runtime.sandbox_catalog import rough_sorter_scan_completed_payload
from src.workline_runtime.services import SandboxWmsInventoryClient, build_workline_runtime_services

SIX_IN_ONE = {
    "HHPN": "620100L00-011-G",
    "MfrPN": "CC0402JRNPO9BN220",
    "Qty": "7387",
    "DateCode": "122625",
    "LotCode": "8904936031",
    "PkgID": "SVYU00125TP4LCR02_2",
}


def _cell(
    cell_location: str,
    *,
    status: str,
    bin_id: str = "BIN-001",
    bin_type: str = "6格箱",
    rack_slot_code: str = "A",
    rack_slot_location_code: str = "NHW-1CLJ-0001-1A-0",
    date_code: str | None = None,
    lot_code: str | None = None,
) -> dict:
    return {
        "rack_id": "RACK-001",
        "rack_slot_code": rack_slot_code,
        "rack_slot_location_code": rack_slot_location_code,
        "bin_id": bin_id,
        "bin_orientation_code": f"{bin_id}-A",
        "bin_type": bin_type,
        "bin_cell_location": cell_location,
        "status": status,
        "DateCode": date_code,
        "LotCode": lot_code,
    }


def _context(
    *,
    cells: list[dict],
    six_in_one: dict | None = SIX_IN_ONE,
    rack_id: str = "RACK-001",
    reel_diameter: str = "7inch",
) -> dict:
    return {
        "trace_id": "trace-001",
        "six_in_one": six_in_one,
        "reel_diameter": reel_diameter,
        "active_bin_rack": {
            "rack_id": rack_id,
            "rack_code": rack_id,
            "cells": cells,
        },
        "wms_rcs_rack_operation_url": "http://wms-rcs/api/rack-operation",
    }


def _full_rack_cells(rack_id: str = "RACK-001") -> list[dict]:
    cells: list[dict] = []
    for slot_index, slot_code in enumerate(("A", "B", "C", "D"), start=1):
        bin_id = f"BIN-{slot_index:03d}"
        for cell_index in range(1, 7):
            cells.append(
                _cell(
                    str(cell_index),
                    status="OCCUPIED",
                    bin_id=bin_id,
                    bin_type="6格箱",
                    rack_slot_code=slot_code,
                    rack_slot_location_code=f"{rack_id}-1{slot_code}-0",
                    date_code="122624",
                    lot_code=f"LOT-{slot_index:02d}-{cell_index:02d}",
                )
            )
    return cells


def _with_required_rack_bins(cells: list[dict], rack_id: str = "RACK-001") -> list[dict]:
    """补齐单层货架 A/B/C/D 四个料箱，便于测试聚焦目标格位选择。"""

    result = list(cells)
    present_slots = {str(cell.get("rack_slot_code")) for cell in cells if cell.get("rack_slot_code")}
    slot_sides = {"A": "0", "B": "0", "C": "1", "D": "1"}
    for slot_code in ("A", "B", "C", "D"):
        if slot_code in present_slots:
            continue
        result.append(
            _cell(
                "1",
                status="OCCUPIED",
                bin_id=f"BIN-FILLER-{slot_code}",
                rack_slot_code=slot_code,
                rack_slot_location_code=f"{rack_id}-1{slot_code}-{slot_sides[slot_code]}",
                date_code="122624",
                lot_code="FILLER",
            )
        )
    return result


def test_smt_rack_bin_scheduler_allocates_stable_bin_location() -> None:
    """同一 PkgID 应得到稳定料箱调度结果，供粗分机 _allocate_bin 使用。"""

    service = SmtRackBinSchedulingService()

    first = service.allocate("PKG-001")
    second = service.allocate("PKG-001")

    assert first == second
    assert set(first) == {
        "rack_id",
        "rack_slot_code",
        "rack_slot_location_code",
        "bin_id",
        "bin_orientation_code",
        "bin_type",
        "bin_cell_location",
        "bin_cell_index",
    }
    assert first["bin_id"].startswith("BIN-")
    assert first["rack_slot_code"] in {"A", "B", "C", "D"}
    assert first["bin_type"] in {"6格箱", "3格箱"}
    assert first["bin_cell_location"].startswith(f"{first['bin_id']}-")


def test_plan_allocation_same_dc_lc_chooses_occupied_compatible_cell() -> None:
    """同 DC/LC 物料应优先复用已占用兼容格位。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell("1", status="EMPTY"),
                    _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031"),
                ]
            )
        ),
    )

    assert isinstance(decision, SmtRackBinSchedulingDecision)
    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == {
        "rack_id": "RACK-001",
        "rack_slot_code": "A",
        "rack_slot_location_code": "NHW-1CLJ-0001-1A-0",
        "bin_id": "BIN-001",
        "bin_orientation_code": "BIN-001-A",
        "bin_type": "6格箱",
        "bin_cell_location": "BIN-001-2",
        "bin_cell_index": "2",
        "expected_stack_height": 1,
    }
    assert decision.rack_operation_request is None


def test_plan_allocation_same_dc_lc_skips_occupied_cell_with_different_vendor_identity() -> None:
    """active 快照缺厂商字段时，也要用 material_identity_key 避免不同厂商物料叠放。"""

    service = SmtRackBinSchedulingService()
    occupied = _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031")
    occupied.update(
        {
            "HHPN": "620100L00-011-G",
            "material_identity_key": "MAT:620100L00-011-G:DIFFERENT-MFR:122625:8904936031",
        }
    )

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    occupied,
                    _cell("3", status="EMPTY"),
                ]
            )
        ),
    )

    assert isinstance(decision, SmtRackBinSchedulingDecision)
    assert decision.kind == "ALLOCATED"
    assert decision.bin_location is not None
    assert decision.bin_location["bin_cell_index"] == "3"


def test_plan_allocation_same_identity_ignores_stale_vendor_snapshot_field() -> None:
    """canonical identity 已匹配时，旧模板残留的 vendor 字段不能阻断同料叠放。"""

    service = SmtRackBinSchedulingService()
    occupied = _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031")
    occupied.update(
        {
            "HHPN": "620100L00-011-G",
            "MfrPN": "DIFFERENT-MFR-FROM-OLD-TEMPLATE",
            "material_identity_key": "MAT:620100L00-011-G:CC0402JRNPO9BN220:122625:8904936031",
        }
    )

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    occupied,
                    _cell("3", status="EMPTY"),
                ]
            )
        ),
    )

    assert isinstance(decision, SmtRackBinSchedulingDecision)
    assert decision.kind == "ALLOCATED"
    assert decision.bin_location is not None
    assert decision.bin_location["bin_cell_index"] == "2"


def test_plan_allocation_matches_legacy_identity_without_vendor_to_canonical_identity() -> None:
    """旧 active key 缺 vendor 时，同 HHPN/DC/LC 的新 canonical key 仍应复用原格位。"""

    service = SmtRackBinSchedulingService()
    occupied = _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031")
    occupied.update(
        {
            "HHPN": "620100L00-011-G",
            "material_identity_key": "MAT:620100L00-011-G:122625:8904936031",
        }
    )

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    occupied,
                    _cell("3", status="EMPTY"),
                ]
            )
        ),
    )

    assert isinstance(decision, SmtRackBinSchedulingDecision)
    assert decision.kind == "ALLOCATED"
    assert decision.bin_location is not None
    assert decision.bin_location["bin_cell_index"] == "2"


def test_plan_allocation_same_dc_lc_skips_full_compatible_cell() -> None:
    """同 DC/LC 兼容格位剩余深度不足时，应继续寻找空料格。"""

    service = SmtRackBinSchedulingService()
    full_cell = _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031")
    full_cell.update(
        {
            "HHPN": "620100L00-011-G",
            "reel_count": 2,
            "used_depth_mm": 5.0,
            "capacity_depth_mm": 5.0,
            "remaining_depth_mm": 0.0,
        }
    )

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell("1", status="EMPTY"),
                    full_cell,
                    _cell("3", status="EMPTY"),
                ]
            ),
            reel_diameter="7inch",
        )
        | {"reel_thickness": "2.5"},
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-1"


def test_plan_allocation_treats_zero_remaining_depth_as_full_without_capacity_fallback() -> None:
    """remaining_depth_mm=0 时不能因缺少 capacity/used 兜底而继续复用该格位。"""

    service = SmtRackBinSchedulingService()
    full_cell = _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031")
    full_cell.update(
        {
            "HHPN": "620100L00-011-G",
            "reel_count": 2,
            "remaining_depth_mm": 0.0,
        }
    )

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell("1", status="EMPTY"),
                    full_cell,
                ]
            ),
            reel_diameter="7inch",
        )
        | {"reel_thickness": "2.5"},
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-1"


def test_plan_allocation_skips_empty_cell_when_capacity_depth_is_insufficient() -> None:
    """选择空格位时也必须校验料盘厚度，不能把料盘放进深度不足的空格。"""

    service = SmtRackBinSchedulingService()
    shallow_empty_cell = _cell("1", status="EMPTY")
    shallow_empty_cell["capacity_depth_mm"] = 1.0
    deep_empty_cell = _cell("2", status="EMPTY")
    deep_empty_cell["max_depth_mm"] = 5.0

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    shallow_empty_cell,
                    deep_empty_cell,
                ]
            ),
            reel_diameter="7inch",
        )
        | {"reel_thickness": "2.5"},
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-2"
    assert decision.bin_location["capacity_depth_mm"] == 5.0


def test_plan_allocation_carries_max_depth_as_capacity_depth() -> None:
    """上游只提供 max_depth_mm 时，调度结果仍应携带聚合容量字段。"""

    service = SmtRackBinSchedulingService()
    empty_cell = _cell("1", status="EMPTY")
    empty_cell["max_depth_mm"] = 5.0

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=_with_required_rack_bins([empty_cell])),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-1"
    assert decision.bin_location["capacity_depth_mm"] == 5.0


def test_plan_allocation_different_dc_lc_chooses_first_empty_cell() -> None:
    """不同 DC/LC 不能混放，应选择第一个空格。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
                    _cell("2", status="OCCUPIED", date_code="122625", lot_code="DIFFERENT"),
                    _cell("3", status="EMPTY"),
                    _cell("4", status="EMPTY"),
                ]
            )
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-3"
    assert decision.rack_operation_request is None


def test_plan_allocation_7inch_prefers_empty_six_bin_cell_before_three_bin_cell() -> None:
    """7 寸料盘选择空格时，6 格箱优先于 3 格箱的 7 寸格。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell(
                        "1",
                        status="EMPTY",
                        bin_id="BIN-3-001",
                        bin_type="3格箱",
                        rack_slot_code="A",
                        rack_slot_location_code="NHW-1CLJ-0001-1A-0",
                    ),
                    _cell(
                        "1",
                        status="EMPTY",
                        bin_id="BIN-6-001",
                        bin_type="6格箱",
                        rack_slot_code="B",
                        rack_slot_location_code="NHW-1CLJ-0001-1B-0",
                    ),
                ]
            ),
            reel_diameter="7inch",
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == {
        "rack_id": "RACK-001",
        "rack_slot_code": "B",
        "rack_slot_location_code": "NHW-1CLJ-0001-1B-0",
        "bin_id": "BIN-6-001",
        "bin_orientation_code": "BIN-6-001-A",
        "bin_type": "6格箱",
        "bin_cell_location": "BIN-6-001-1",
        "bin_cell_index": "1",
        "expected_stack_height": 1,
    }


def test_plan_allocation_large_reel_uses_three_bin_large_cell_only() -> None:
    """13/15 寸等大尺寸料盘只能进入 3 格箱的大尺寸格 `-7`。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell(
                        "1",
                        status="EMPTY",
                        bin_id="BIN-6-001",
                        bin_type="6格箱",
                        rack_slot_code="A",
                        rack_slot_location_code="NHW-1CLJ-0001-1A-0",
                    ),
                    _cell(
                        "1",
                        status="EMPTY",
                        bin_id="BIN-3-001",
                        bin_type="3格箱",
                        rack_slot_code="B",
                        rack_slot_location_code="NHW-1CLJ-0001-1B-0",
                    ),
                    _cell(
                        "7",
                        status="EMPTY",
                        bin_id="BIN-3-002",
                        bin_type="3格箱",
                        rack_slot_code="C",
                        rack_slot_location_code="NHW-1CLJ-0001-1C-1",
                    ),
                ]
            ),
            reel_diameter="15inch",
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == {
        "rack_id": "RACK-001",
        "rack_slot_code": "C",
        "rack_slot_location_code": "NHW-1CLJ-0001-1C-1",
        "bin_id": "BIN-3-002",
        "bin_orientation_code": "BIN-3-002-A",
        "bin_type": "3格箱",
        "bin_cell_location": "BIN-3-002-7",
        "bin_cell_index": "7",
        "expected_stack_height": 1,
    }


def test_plan_allocation_large_reel_requires_operation_without_three_bin_large_cell() -> None:
    """当前货架没有 3 格箱大尺寸格时，大尺寸料盘触发换架 operation。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=_full_rack_cells(), reel_diameter="15inch"),
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.bin_location is None
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.payload["request_type"] == "SMT_RACK_OPERATION"
    assert decision.rack_operation_request.payload["operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert decision.rack_operation_request.payload["actions"] == ["MOVE_OUT_ACTIVE_RACK", "ALLOCATE_AND_MOVE_RACK"]


def test_plan_allocation_full_rack_without_compatible_cell_requires_rack_operation() -> None:
    """满架且无兼容格位时只生成换架 operation 决策。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=_full_rack_cells()),
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.bin_location is None
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.operation_key == "external:smt_rack_bin:trace-001:RACK_OPERATION"
    payload = decision.rack_operation_request.payload
    assert payload["request_type"] == "SMT_RACK_OPERATION"
    assert payload["operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert payload["operation_key"] == decision.rack_operation_request.operation_key
    assert payload["material"] == SIX_IN_ONE
    assert payload["current_rack_snapshot"]["rack_id"] == "RACK-001"
    assert payload["actions"] == ["MOVE_OUT_ACTIVE_RACK", "ALLOCATE_AND_MOVE_RACK"]
    assert payload["trace_id"] == "trace-001"
    assert payload["move_out_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert payload["work_position_code"] == "SINGLE_LAYER_A"
    assert payload["new_rack_kind"] == "SINGLE_LAYER"
    assert payload["move_out_target_position_role"] == "SMT_EMPTY_RACK_AREA"
    assert payload["target_code"] == "WMS_RCS_RACK_OPERATION"
    assert payload["reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert len(payload["active_rack_bin_snapshots"]) == 4


def test_plan_allocation_accepts_legacy_rack_supply_target_alias() -> None:
    """兼容旧配置仍在使用的 wms_rcs_rack_supply_url。"""

    service = SmtRackBinSchedulingService()
    context = _context(cells=_full_rack_cells())
    context.pop("wms_rcs_rack_operation_url")
    context["wms_rcs_rack_supply_url"] = "http://wms-rcs/api/rack-exchange"

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.target_code == "WMS_RCS_RACK_OPERATION"
    assert decision.rack_operation_request.payload["target_code"] == "WMS_RCS_RACK_OPERATION"


def test_plan_allocation_missing_active_rack_requires_rack_operation_with_reason() -> None:
    """缺少当前可用料架时返回明确的等待 operation 原因。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context={
            "six_in_one": SIX_IN_ONE,
            "active_bin_rack": None,
            "wms_rcs_rack_operation_url": "http://wms-rcs/api/rack-operation",
        },
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.reason_code == "NO_ACTIVE_RACK"
    assert decision.bin_location is None
    assert decision.rack_operation_request is not None


def test_plan_allocation_without_active_rack_requests_operation_allocate_only() -> None:
    """初次开工无货架时只请求补新架 operation，不携带当前货架移出动作。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context={
            "trace_id": "trace-supply-001",
            "six_in_one": SIX_IN_ONE,
            "reel_diameter": "7inch",
            "active_bin_rack": None,
            "wms_rcs_rack_operation_url": "http://wms-rcs/api/rack-operation",
        },
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.bin_location is None
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.operation_key == "external:smt_rack_bin:trace-supply-001:RACK_OPERATION"
    assert decision.rack_operation_request.target_code == "WMS_RCS_RACK_OPERATION"
    payload = decision.rack_operation_request.payload
    assert payload["request_type"] == "SMT_RACK_OPERATION"
    assert payload["operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert payload["operation_key"] == decision.rack_operation_request.operation_key
    assert payload["actions"] == ["ALLOCATE_AND_MOVE_RACK"]
    assert payload["reason_code"] == "NO_ACTIVE_RACK"
    assert "active_rack_bin_snapshots" not in payload


def test_plan_allocation_without_active_rack_preserves_target_position_code() -> None:
    """补空架 operation 必须保留当前工作线目标停靠位。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context={
            "trace_id": "trace-supply-002",
            "six_in_one": SIX_IN_ONE,
            "reel_diameter": "7inch",
            "active_bin_rack": None,
            "wms_rcs_rack_operation_url": "http://wms-rcs/api/rack-operation",
            "position_code": "SINGLE_LAYER_B",
        },
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.payload["work_position_code"] == "SINGLE_LAYER_B"


def test_plan_allocation_with_unusable_active_rack_requests_replace_operation() -> None:
    """有当前货架但无可用格位时，SMT 只请求换架 operation 并携带当前快照。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=_full_rack_cells("NHW-1CLJ-0096"),
        rack_id="NHW-1CLJ-0096",
    )
    context["wms_rcs_rack_operation_url"] = "http://wms-rcs/api/rack-operation"

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.bin_location is None
    assert decision.rack_operation_request is not None
    payload = decision.rack_operation_request.payload
    assert payload["request_type"] == "SMT_RACK_OPERATION"
    assert payload["actions"] == ["MOVE_OUT_ACTIVE_RACK", "ALLOCATE_AND_MOVE_RACK"]
    assert payload["single_layer_rack_id"] == "NHW-1CLJ-0096"
    assert payload["single_layer_rack_code"] == "NHW-1CLJ-0096"
    assert payload["move_out_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert len(payload["active_rack_bin_snapshots"]) == 4


def test_plan_allocation_replace_operation_payload_contains_current_rack_bin_snapshots() -> None:
    """SMT 换架 operation 应携带当前 4 个料箱快照，后续任务域据此处理货架离位。"""

    service = SmtRackBinSchedulingService()
    context = _context(cells=_full_rack_cells("NHW-1CLJ-0096"), rack_id="NHW-1CLJ-0096")

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.rack_operation_request is not None
    snapshots = decision.rack_operation_request.payload["active_rack_bin_snapshots"]
    assert len(snapshots) == 4
    assert {snapshot["slot_code"] for snapshot in snapshots} == {"A", "B", "C", "D"}
    assert {snapshot["bin_id"] for snapshot in snapshots} == {"BIN-001", "BIN-002", "BIN-003", "BIN-004"}
    for snapshot in snapshots:
        assert snapshot["status"] == "FULL"
        assert snapshot["bin_execution_status"] == "FULL"
        assert snapshot["usage"] == 1.0
        assert snapshot["usage_snapshot"] == 1.0
        assert "bin_cell_location" not in snapshot

    assert decision.rack_operation_request.payload["request_type"] == "SMT_RACK_OPERATION"
    assert decision.rack_operation_request.payload["single_layer_rack_id"] == "NHW-1CLJ-0096"


def test_replace_operation_payload_treats_occupied_cells_with_unknown_depth_usage_as_non_empty() -> None:
    """占用格位缺少 used/stack 深度时，换架 operation 不能把有料箱按空箱上报。"""

    service = SmtRackBinSchedulingService()
    cells = _full_rack_cells("NHW-1CLJ-0096")
    for cell in cells:
        cell["capacity_depth_mm"] = 5.0

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=cells, rack_id="NHW-1CLJ-0096"),
    )

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.rack_operation_request is not None
    snapshots = decision.rack_operation_request.payload["active_rack_bin_snapshots"]
    assert len(snapshots) == 4
    for snapshot in snapshots:
        assert snapshot["status"] == "FULL"
        assert snapshot["bin_execution_status"] == "FULL"
        assert snapshot["usage"] == 1.0
        assert snapshot["usage_snapshot"] == 1.0


def test_plan_allocation_partial_rack_snapshot_blocks_for_reconciliation() -> None:
    """有当前货架但快照不足 4 个真实料箱时阻断对账，不继续补架。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell(
                "1",
                status="OCCUPIED",
                bin_id="BIN-PARTIAL-001",
                rack_slot_code="A",
                rack_slot_location_code="RACK-PARTIAL-001-1A-0",
                date_code="122624",
                lot_code="8904936031",
            ),
            _cell(
                "2",
                status="OCCUPIED",
                bin_id="BIN-PARTIAL-002",
                rack_slot_code="B",
                rack_slot_location_code="RACK-PARTIAL-001-1B-0",
                date_code="122625",
                lot_code="DIFFERENT",
            ),
        ],
        rack_id="RACK-PARTIAL-001",
    )

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "ACTIVE_RACK_SNAPSHOT_INVALID"
    assert decision.message == "SMT 可用货架快照必须包含 A/B/C/D 4 个料箱"
    assert decision.rack_operation_request is None


def test_plan_allocation_rejects_partial_rack_even_when_empty_cell_exists() -> None:
    """WMS/RCS 回传的可用货架不足 4 个料箱时，即使存在空格也不能分配。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell(
                "1",
                status="EMPTY",
                bin_id="BIN-PARTIAL-001",
                rack_slot_code="A",
                rack_slot_location_code="RACK-PARTIAL-002-1A-0",
            )
        ],
        rack_id="RACK-PARTIAL-002",
    )

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "ACTIVE_RACK_SNAPSHOT_INVALID"
    assert decision.message == "SMT 可用货架快照必须包含 A/B/C/D 4 个料箱"
    assert decision.bin_location is None


def test_plan_allocation_rejects_arrived_operation_rack_with_occupied_cell() -> None:
    """WMS/RCS operation 到位的可用货架必须全为空料格。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell(
                "1",
                status="OCCUPIED",
                bin_id="BIN-OPERATION-A",
                rack_slot_code="A",
                rack_slot_location_code="RACK-SUPPLY-001-1A-0",
                date_code="122624",
                lot_code="DIFFERENT",
            ),
            _cell(
                "1",
                status="EMPTY",
                bin_id="BIN-OPERATION-B",
                rack_slot_code="B",
                rack_slot_location_code="RACK-SUPPLY-001-1B-0",
            ),
            _cell(
                "1",
                status="EMPTY",
                bin_id="BIN-OPERATION-C",
                rack_slot_code="C",
                rack_slot_location_code="RACK-SUPPLY-001-1C-1",
            ),
            _cell(
                "1",
                status="EMPTY",
                bin_id="BIN-OPERATION-D",
                rack_slot_code="D",
                rack_slot_location_code="RACK-SUPPLY-001-1D-1",
            ),
        ],
        rack_id="RACK-OPERATION-001",
    )
    context["rack_operation"] = {
        "status": "SUCCEEDED",
        "operation_key": "external:smt_rack_bin:trace-001:RACK_OPERATION",
    }

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "ACTIVE_RACK_NOT_EMPTY"
    assert decision.message == "SMT 可用货架料箱必须全为空料格"
    assert decision.bin_location is None


def test_plan_allocation_missing_rack_operation_target_uses_default_endpoint_code() -> None:
    """SANDBOX 未携带显式 WMS/RCS 地址时，仍使用系统默认逻辑端点创建 operation。"""

    service = SmtRackBinSchedulingService()
    context = _context(cells=_full_rack_cells())
    context.pop("wms_rcs_rack_operation_url")

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.target_code == "WMS_RCS_RACK_OPERATION"
    assert decision.rack_operation_request.payload["target_code"] == "WMS_RCS_RACK_OPERATION"


def test_plan_allocation_pending_rack_operation_blocks_duplicate_request() -> None:
    """已有等待中的 rack operation 时不重复创建新的换架请求。"""

    service = SmtRackBinSchedulingService()
    context = _context(cells=_full_rack_cells())
    context["waiting_rack_operation_key"] = "external:smt_rack_bin:trace-001:RACK_OPERATION"
    context["rack_operation"] = {
        "operation_key": "external:smt_rack_bin:trace-001:RACK_OPERATION",
        "status": "PENDING",
    }

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "RACK_OPERATION_PENDING"
    assert decision.rack_operation_request is None


def test_plan_allocation_ignores_locked_disabled_and_incomplete_cells() -> None:
    """锁定、禁用、缺少关键字段的格位不参与 DC/LC 调度。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=_with_required_rack_bins(
                [
                    _cell("1", status="OCCUPIED", date_code="122625", lot_code="8904936031") | {"locked": True},
                    _cell("2", status="EMPTY") | {"disabled": True},
                    {"status": "EMPTY", "bin_id": "BIN-001"},
                    _cell("4", status="EMPTY"),
                ]
            )
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-4"
    assert decision.rack_operation_request is None


def test_plan_allocation_context_without_rack_snapshot_uses_deterministic_allocator() -> None:
    """缺少真实调度快照时，返回可继续运行的确定性分配结果。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation("PKG-001", context={"workline_code": "SMT-01"})

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == service.allocate("PKG-001")
    assert decision.rack_operation_request is None


def test_plan_allocation_missing_material_fields_blocks_without_rack_operation_request() -> None:
    """真实调度上下文缺少物料关键字段时阻断物料，不生成换架 operation。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=[_cell("1", status="EMPTY")], six_in_one={"PkgID": "SVYU00125TP4LCR02_2"}),
    )

    assert decision.kind == "BLOCKED"
    assert decision.bin_location is None
    assert decision.reason_code == "MISSING_MATERIAL_FIELDS"
    assert decision.message == "SMT 料箱调度缺少物料字段: DateCode, LotCode"
    assert decision.rack_operation_request is None


def test_plan_allocation_pkg_id_mismatch_blocks_without_rack_operation_request() -> None:
    """PkgID 与 barcode 不一致时阻断物料，不生成虚拟格位或换架 operation。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "DIFFERENT-PKG",
        context=_context(cells=[_cell("1", status="EMPTY")]),
    )

    assert decision.kind == "BLOCKED"
    assert decision.bin_location is None
    assert decision.reason_code == "PKG_ID_MISMATCH"
    assert decision.rack_operation_request is None


def test_plan_allocation_rack_operation_uses_external_operation_key_and_target_contract() -> None:
    """换架 operation 使用外部 operation key，并从上下文读取 WMS/RCS 目标地址。"""

    service = SmtRackBinSchedulingService()

    context = _context(cells=_full_rack_cells())
    context["wms_rcs_rack_operation_url"] = "http://wms-rcs/api/rack-operation"

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_OPERATION_REQUIRED"
    assert decision.rack_operation_request is not None
    assert decision.rack_operation_request.operation_key == "external:smt_rack_bin:trace-001:RACK_OPERATION"
    assert decision.rack_operation_request.target_code == "WMS_RCS_RACK_OPERATION"


def test_runtime_services_injects_default_smt_rack_bin_scheduler() -> None:
    """worker 构建运行时服务时应默认注入具体调度领域服务，而不是让插件走占位逻辑。"""

    services = build_workline_runtime_services()

    assert isinstance(services.bin_allocator, SmtRackBinSchedulingService)
    assert services.bin_allocator is smt_rack_bin_scheduling_service
    assert services.bin_allocator.allocate("PKG-002") == SmtRackBinSchedulingService().allocate("PKG-002")


def test_runtime_services_inject_sandbox_wms_client_for_simulation_workline() -> None:
    """SIMULATION 工作线不能在插件阶段同步访问真实 WMS，只能访问 sandbox WMS client。"""

    services = build_workline_runtime_services(
        db=object(),
        workline=SimpleNamespace(run_mode="SIMULATION"),
    )

    assert isinstance(services.wms_inventory_client, SandboxWmsInventoryClient)


def test_runtime_services_inject_sandbox_wms_client_for_simulation_session() -> None:
    """Session 级 SIMULATION 覆盖工作线模式时，也只能注入 sandbox WMS client。"""

    services = build_workline_runtime_services(
        db=object(),
        workline=SimpleNamespace(run_mode="AUTO"),
        session=SimpleNamespace(run_mode="SIMULATION"),
    )

    assert isinstance(services.wms_inventory_client, SandboxWmsInventoryClient)


@pytest.mark.asyncio
async def test_sandbox_wms_client_returns_matching_inventory_for_simulation_workline() -> None:
    """SIMULATION 工作线注入 sandbox WMS client，避免粗分机 happy path 访问真实 WMS。"""

    payload_data = rough_sorter_scan_completed_payload()["data"]
    services = build_workline_runtime_services(
        db=object(),
        workline=SimpleNamespace(run_mode="SIMULATION"),
    )

    assert services.wms_inventory_client is not None

    response = await services.wms_inventory_client.query_inventory(
        QueryInventoryRequest(
            request_id="rough-sorter:inventory:PKG-001",
            trace_id="trace-sandbox-wms-001",
            sku=payload_data["HHPN"],
            lot_no=payload_data["LotCode"],
        )
    )

    assert len(response.items) == 1
    assert response.items[0].sku == payload_data["HHPN"]
    assert response.items[0].lot_no == payload_data["LotCode"]


@pytest.mark.asyncio
async def test_sandbox_wms_client_returns_empty_inventory_for_unknown_material() -> None:
    services = build_workline_runtime_services(
        db=object(),
        workline=SimpleNamespace(run_mode="SIMULATION"),
    )

    assert services.wms_inventory_client is not None

    response = await services.wms_inventory_client.query_inventory(
        QueryInventoryRequest(
            request_id="rough-sorter:inventory:UNKNOWN",
            trace_id="trace-sandbox-wms-unknown",
            sku="UNKNOWN",
            lot_no="LOT-A",
        )
    )

    assert response.reason_code == "SANDBOX_WMS_INVENTORY"
    assert response.items == []
