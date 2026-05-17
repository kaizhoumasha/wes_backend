"""SMT 货架/料箱调度领域服务测试。"""

from src.app.resource.services import (
    SmtRackBinSchedulingDecision,
    SmtRackBinSchedulingService,
    smt_rack_bin_scheduling_service,
)
from src.workline_runtime.services import build_workline_runtime_services

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
        "wms_rcs_rack_supply_url": "http://wms-rcs/api/rack-supply",
        "wms_rcs_rack_exchange_url": "http://wms-rcs/api/rack-exchange",
        "smt_full_box_release_device_code": "SMT-FULL-BOX-EVENT",
    }


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
            cells=[
                _cell("1", status="EMPTY"),
                _cell("2", status="OCCUPIED", date_code="122625", lot_code="8904936031"),
            ]
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
    }
    assert decision.full_box_exchange_request is None
    assert decision.external_request is None


def test_plan_allocation_different_dc_lc_chooses_first_empty_cell() -> None:
    """不同 DC/LC 不能混放，应选择第一个空格。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
                _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
                _cell("2", status="OCCUPIED", date_code="122625", lot_code="DIFFERENT"),
                _cell("3", status="EMPTY"),
                _cell("4", status="EMPTY"),
            ]
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-3"
    assert decision.external_request is None


def test_plan_allocation_7inch_prefers_empty_six_bin_cell_before_three_bin_cell() -> None:
    """7 寸料盘选择空格时，6 格箱优先于 3 格箱的 7 寸格。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
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
            ],
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
    }


def test_plan_allocation_large_reel_uses_three_bin_large_cell_only() -> None:
    """13/15 寸等大尺寸料盘只能进入 3 格箱的大尺寸格 `-7`。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
                _cell("1", status="EMPTY", bin_id="BIN-6-001", bin_type="6格箱"),
                _cell("1", status="EMPTY", bin_id="BIN-3-001", bin_type="3格箱"),
                _cell(
                    "7",
                    status="EMPTY",
                    bin_id="BIN-3-001",
                    bin_type="3格箱",
                    rack_slot_code="C",
                    rack_slot_location_code="NHW-1CLJ-0001-1C-1",
                ),
            ],
            reel_diameter="15inch",
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == {
        "rack_id": "RACK-001",
        "rack_slot_code": "C",
        "rack_slot_location_code": "NHW-1CLJ-0001-1C-1",
        "bin_id": "BIN-3-001",
        "bin_orientation_code": "BIN-3-001-A",
        "bin_type": "3格箱",
        "bin_cell_location": "BIN-3-001-7",
        "bin_cell_index": "7",
    }


def test_plan_allocation_large_reel_requires_exchange_without_three_bin_large_cell() -> None:
    """当前货架没有 3 格箱大尺寸格时，大尺寸料盘触发换架补充。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
                _cell("1", status="EMPTY", bin_id="BIN-6-001", bin_type="6格箱"),
                _cell("2", status="EMPTY", bin_id="BIN-3-001", bin_type="3格箱"),
            ],
            reel_diameter="15inch",
        ),
    )

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.bin_location is None
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is not None


def test_plan_allocation_full_rack_without_compatible_cell_requires_rack_exchange() -> None:
    """满架且无兼容格位时只生成换架外部请求决策。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
                _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
                _cell("2", status="OCCUPIED", date_code="122625", lot_code="DIFFERENT"),
            ]
        ),
    )

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.bin_location is None
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is not None
    assert decision.rack_supply_request.dispatch_key == "external:smt_classifier:trace-001:RACK_SUPPLY"
    assert decision.rack_supply_request.payload["request_type"] == "SMT_RACK_SUPPLY"
    assert decision.rack_supply_request.payload["material"] == SIX_IN_ONE
    assert decision.rack_supply_request.payload["current_rack_snapshot"]["rack_id"] == "RACK-001"
    assert decision.rack_supply_request.payload["actions"] == ["SUPPLY_EMPTY_RACK"]
    assert decision.rack_supply_request.payload["resume_callback_type"] == "WMS_RACK_ARRIVED"
    assert decision.rack_supply_request.payload["dispatch_key"] == decision.rack_supply_request.dispatch_key
    assert decision.rack_supply_request.payload["trace_id"] == "trace-001"
    assert decision.rack_release_event.event_type == "SINGLE_LAYER_RACK_RELEASED"
    assert decision.rack_release_event.data["release_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"


def test_plan_allocation_missing_active_rack_requires_rack_exchange_with_reason() -> None:
    """缺少当前可用料架时返回明确的等待换架原因。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context={
            "six_in_one": SIX_IN_ONE,
            "active_bin_rack": None,
            "wms_rcs_rack_exchange_url": "http://wms-rcs/api/rack-exchange",
        },
    )

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.reason_code == "NO_ACTIVE_RACK"
    assert decision.bin_location is None
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is None


def test_plan_allocation_without_active_rack_requests_supply_only() -> None:
    """初次开工无货架时只请求新货架补充，不触发满箱交换释放事件。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context={
            "trace_id": "trace-supply-001",
            "six_in_one": SIX_IN_ONE,
            "reel_diameter": "7inch",
            "active_bin_rack": None,
            "wms_rcs_rack_supply_url": "http://wms-rcs/api/rack-supply",
        },
    )

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.bin_location is None
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is None
    assert decision.rack_supply_request.dispatch_key == "external:smt_classifier:trace-supply-001:RACK_SUPPLY"
    assert decision.rack_supply_request.target_code == "http://wms-rcs/api/rack-supply"
    assert decision.rack_supply_request.payload["request_type"] == "SMT_RACK_SUPPLY"
    assert decision.rack_supply_request.payload["actions"] == ["SUPPLY_EMPTY_RACK"]
    assert decision.rack_supply_request.payload["reason_code"] == "NO_ACTIVE_RACK"


def test_plan_allocation_with_unusable_active_rack_requests_supply_and_release_event() -> None:
    """有当前货架但无可用格位时，SMT 请求新货架并通知满箱交换插件处理旧货架。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
            _cell("2", status="OCCUPIED", date_code="122625", lot_code="DIFFERENT"),
        ],
        rack_id="NHW-1CLJ-0096",
    )
    context["wms_rcs_rack_supply_url"] = "http://wms-rcs/api/rack-supply"
    context["smt_full_box_release_device_code"] = "SMT-FULL-BOX-EVENT"

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.bin_location is None
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is not None
    assert decision.rack_supply_request.payload["request_type"] == "SMT_RACK_SUPPLY"
    assert decision.rack_supply_request.payload["actions"] == ["SUPPLY_EMPTY_RACK"]
    assert decision.rack_release_event.device_code == "SMT-FULL-BOX-EVENT"
    assert decision.rack_release_event.event_type == "SINGLE_LAYER_RACK_RELEASED"
    assert decision.rack_release_event.data["single_layer_rack_id"] == "NHW-1CLJ-0096"
    assert decision.rack_release_event.data["single_layer_rack_code"] == "NHW-1CLJ-0096"
    assert decision.rack_release_event.data["release_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.rack_release_event.data["bin_snapshots"] == context["active_bin_rack"]["cells"]


def test_plan_allocation_missing_rack_exchange_target_blocks_configuration() -> None:
    """缺少 WMS/RCS 目标地址时阻断物料，避免把请求类型当作 HTTP URL 派发。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
        ]
    )
    context.pop("wms_rcs_rack_exchange_url")
    context.pop("wms_rcs_rack_supply_url")

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "RACK_SUPPLY_TARGET_MISSING"
    assert decision.external_request is None


def test_plan_allocation_ignores_locked_disabled_and_incomplete_cells() -> None:
    """锁定、禁用、缺少关键字段的格位不参与 DC/LC 调度。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(
            cells=[
                _cell("1", status="OCCUPIED", date_code="122625", lot_code="8904936031") | {"locked": True},
                _cell("2", status="EMPTY") | {"disabled": True},
                {"status": "EMPTY", "bin_id": "BIN-001"},
                _cell("4", status="EMPTY"),
            ]
        ),
    )

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location["bin_cell_location"] == "BIN-001-4"
    assert decision.external_request is None


def test_plan_allocation_legacy_context_without_rack_snapshot_uses_allocate_compatibility() -> None:
    """旧插件路径缺少真实调度快照时，仍返回当前 plugin 可处理的兼容分配结果。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation("PKG-001", context={"workline_code": "SMT-01"})

    assert decision.kind == "ALLOCATED"
    assert decision.bin_location == service.allocate("PKG-001")
    assert decision.external_request is None


def test_plan_allocation_missing_material_fields_blocks_without_external_request() -> None:
    """真实调度上下文缺少物料关键字段时阻断物料，不生成换架请求。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "SVYU00125TP4LCR02_2",
        context=_context(cells=[_cell("1", status="EMPTY")], six_in_one={"PkgID": "SVYU00125TP4LCR02_2"}),
    )

    assert decision.kind == "BLOCKED"
    assert decision.bin_location is None
    assert decision.reason_code == "MISSING_MATERIAL_FIELDS"
    assert decision.message == "SMT 料箱调度缺少物料字段: DateCode, LotCode"
    assert decision.external_request is None
    assert decision.full_box_exchange_request is None


def test_plan_allocation_pkg_id_mismatch_blocks_without_external_request() -> None:
    """PkgID 与 barcode 不一致时阻断物料，不生成虚拟格位或换架请求。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation(
        "DIFFERENT-PKG",
        context=_context(cells=[_cell("1", status="EMPTY")]),
    )

    assert decision.kind == "BLOCKED"
    assert decision.bin_location is None
    assert decision.reason_code == "PKG_ID_MISMATCH"
    assert decision.external_request is None


def test_plan_allocation_rack_exchange_uses_external_dispatch_and_target_contract() -> None:
    """换架请求使用外部调度 key，并从上下文读取 WMS/RCS 目标地址。"""

    service = SmtRackBinSchedulingService()

    context = _context(
        cells=[
            _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
        ]
    )
    context["wms_rcs_rack_exchange_url"] = "http://wms-rcs/api/rack-exchange"

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "RACK_SUPPLY_REQUIRED"
    assert decision.rack_supply_request is not None
    assert decision.rack_release_event is not None
    assert decision.rack_supply_request.dispatch_key == "external:smt_classifier:trace-001:RACK_SUPPLY"
    assert decision.rack_supply_request.target_code == "http://wms-rcs/api/rack-supply"


def test_runtime_services_injects_default_smt_rack_bin_scheduler() -> None:
    """worker 构建运行时服务时应默认注入具体调度领域服务，而不是让插件走占位逻辑。"""

    services = build_workline_runtime_services()

    assert isinstance(services.bin_allocator, SmtRackBinSchedulingService)
    assert services.bin_allocator is smt_rack_bin_scheduling_service
    assert services.bin_allocator.allocate("PKG-002") == SmtRackBinSchedulingService().allocate("PKG-002")
