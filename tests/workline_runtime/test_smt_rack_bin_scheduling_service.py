"""SMT 货架/料箱调度领域服务测试。"""

from src.app.workline.domain.services import SmtRackBinSchedulingDecision, SmtRackBinSchedulingService
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
    date_code: str | None = None,
    lot_code: str | None = None,
) -> dict:
    return {
        "rack_id": "RACK-001",
        "bin_id": "BIN-001",
        "bin_type": "九格箱",
        "bin_cell_location": cell_location,
        "status": status,
        "DateCode": date_code,
        "LotCode": lot_code,
    }


def _context(*, cells: list[dict], six_in_one: dict | None = SIX_IN_ONE, rack_id: str = "RACK-001") -> dict:
    return {
        "trace_id": "trace-001",
        "six_in_one": six_in_one,
        "active_bin_rack": {
            "rack_id": rack_id,
            "rack_code": rack_id,
            "cells": cells,
        },
        "wms_rcs_rack_exchange_url": "http://wms-rcs/api/rack-exchange",
    }


def test_smt_rack_bin_scheduler_allocates_stable_bin_location() -> None:
    """同一 PkgID 应得到稳定料箱调度结果，供粗分机 _allocate_bin 使用。"""

    service = SmtRackBinSchedulingService()

    first = service.allocate("PKG-001")
    second = service.allocate("PKG-001")

    assert first == second
    assert set(first) == {"bin_id", "bin_type", "bin_cell_location"}
    assert first["bin_id"].startswith("BIN_")
    assert first["bin_cell_location"].isdigit()


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
        "bin_id": "BIN-001",
        "bin_type": "九格箱",
        "bin_cell_location": "2",
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
    assert decision.bin_location["bin_cell_location"] == "3"
    assert decision.external_request is None


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

    assert decision.kind == "RACK_EXCHANGE_REQUIRED"
    assert decision.bin_location is None
    assert decision.reason_code == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert decision.external_request is not None
    assert decision.external_request.dispatch_key == "external:smt_classifier:trace-001:RACK_EXCHANGE_AND_SUPPLY"
    assert decision.external_request.payload["request_type"] == "SMT_RACK_EXCHANGE_AND_SUPPLY"
    assert decision.external_request.payload["material"] == SIX_IN_ONE
    assert decision.external_request.payload["current_rack_snapshot"]["rack_id"] == "RACK-001"
    assert decision.external_request.payload["actions"] == ["MOVE_OUT_CURRENT_RACK", "SUPPLY_EMPTY_RACK"]
    assert decision.external_request.payload["resume_callback_type"] == "WMS_RACK_ARRIVED"
    assert decision.external_request.payload["dispatch_key"] == decision.external_request.dispatch_key
    assert decision.external_request.payload["trace_id"] == "trace-001"


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

    assert decision.kind == "RACK_EXCHANGE_REQUIRED"
    assert decision.reason_code == "NO_ACTIVE_RACK"
    assert decision.bin_location is None
    assert decision.external_request is not None


def test_plan_allocation_missing_rack_exchange_target_blocks_configuration() -> None:
    """缺少 WMS/RCS 目标地址时阻断物料，避免把请求类型当作 HTTP URL 派发。"""

    service = SmtRackBinSchedulingService()
    context = _context(
        cells=[
            _cell("1", status="OCCUPIED", date_code="122624", lot_code="8904936031"),
        ]
    )
    context.pop("wms_rcs_rack_exchange_url")

    decision = service.plan_allocation("SVYU00125TP4LCR02_2", context=context)

    assert decision.kind == "BLOCKED"
    assert decision.reason_code == "RACK_EXCHANGE_TARGET_MISSING"
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
    assert decision.bin_location["bin_cell_location"] == "4"
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

    assert decision.kind == "RACK_EXCHANGE_REQUIRED"
    assert decision.external_request is not None
    assert decision.external_request.dispatch_key == "external:smt_classifier:trace-001:RACK_EXCHANGE_AND_SUPPLY"
    assert decision.external_request.target_code == "http://wms-rcs/api/rack-exchange"


def test_runtime_services_injects_default_smt_rack_bin_scheduler() -> None:
    """worker 构建运行时服务时应默认注入具体调度领域服务，而不是让插件走占位逻辑。"""

    services = build_workline_runtime_services()

    assert isinstance(services.bin_allocator, SmtRackBinSchedulingService)
    assert services.bin_allocator.allocate("PKG-002") == SmtRackBinSchedulingService().allocate("PKG-002")
