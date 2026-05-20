"""SMT 单层货架从补架到满箱交换的专项模拟。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.services import SmtRackBinSchedulingService
from src.workline_plugins.smt_classifier import SmtClassifierPlugin
from src.workline_plugins.smt_full_box_exchange import SmtFullBoxExchangePlugin
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult
from src.workline_runtime.runtime_intent import RuntimeIntentKind

WORKLINE_CODE = "WL-CONVEYOR-01"
RACK_ID = "RACK-SL-DEV-001"
RACK_SUPPLY_TARGET = "http://127.0.0.1:8009/api/v1/device/command"
FULL_BOX_EXCHANGE_TARGET = "http://127.0.0.1:8010/api/full-box-exchange"
FULL_BOX_RELEASE_DEVICE = "SMT-FULL-BOX-EVENT"
CELL_CAPACITY = 5
MATERIAL_CODES = [f"MAT-SMT-{index:03d}" for index in range(1, 11)]


@dataclass(frozen=True)
class ReelPlan:
    """一卷料盘进入粗分机后的可调度身份。"""

    pkg_id: str
    material_code: str
    date_code: str
    lot_code: str
    reel_diameter: str

    @property
    def six_in_one(self) -> dict[str, str]:
        return {
            "HHPN": self.material_code,
            "MfrPN": f"VENDOR-{self.material_code[-3:]}",
            "Qty": "500",
            "DateCode": self.date_code,
            "LotCode": self.lot_code,
            "PkgID": self.pkg_id,
        }


def _rack_slot_location(rack_id: str, slot_code: str) -> str:
    side = "1" if slot_code in {"C", "D"} else "0"
    return f"{rack_id}-1{slot_code}-{side}"


def _build_single_layer_rack(rack_id: str = RACK_ID) -> dict[str, Any]:
    """构造 A 面 2 个 6 格箱、B 面 2 个 3 格箱的单层货架快照。"""

    rack_slots = (
        ("A", "A", "6格箱", ("1", "2", "3", "4", "5", "6")),
        ("B", "A", "6格箱", ("1", "2", "3", "4", "5", "6")),
        ("C", "B", "3格箱", ("1", "2", "7")),
        ("D", "B", "3格箱", ("1", "2", "7")),
    )
    cells: list[dict[str, Any]] = []
    for slot_code, rack_side, bin_type, cell_indexes in rack_slots:
        bin_id = f"BIN-{rack_id[-3:]}-{slot_code}"
        for cell_index in cell_indexes:
            cells.append(
                {
                    "rack_id": rack_id,
                    "rack_code": rack_id,
                    "rack_side": rack_side,
                    "rack_slot_code": slot_code,
                    "rack_slot_location_code": _rack_slot_location(rack_id, slot_code),
                    "bin_id": bin_id,
                    "bin_orientation_code": f"{bin_id}-A",
                    "bin_type": bin_type,
                    "bin_cell_location": f"{bin_id}-{cell_index}",
                    "bin_cell_index": cell_index,
                    "status": "EMPTY",
                    "capacity_depth_mm": float(CELL_CAPACITY),
                    "used_depth_mm": 0.0,
                    "remaining_depth_mm": float(CELL_CAPACITY),
                    "reel_count": 0,
                }
            )
    return {"rack_id": rack_id, "rack_code": rack_id, "cells": cells}


def _material_identity_key(six_in_one: dict[str, str]) -> str:
    return f"MAT:{six_in_one['HHPN']}:{six_in_one['MfrPN']}:{six_in_one['DateCode']}:{six_in_one['LotCode']}"


def _build_reel_plans() -> list[ReelPlan]:
    """用约 10 种物料号生成 18 个 DC/LC 组合，填满 18 个料格。"""

    cell_identities: list[ReelPlan] = []
    for index in range(16):
        material_code = MATERIAL_CODES[index % len(MATERIAL_CODES)]
        cell_identities.append(
            ReelPlan(
                pkg_id=f"PKG-7IN-{index + 1:02d}-001",
                material_code=material_code,
                date_code=f"202605{index % 9 + 1:02d}",
                lot_code=f"LOT-7IN-{index + 1:02d}",
                reel_diameter="7inch",
            )
        )
    for index in range(2):
        material_code = MATERIAL_CODES[(index + 8) % len(MATERIAL_CODES)]
        cell_identities.append(
            ReelPlan(
                pkg_id=f"PKG-15IN-{index + 1:02d}-001",
                material_code=material_code,
                date_code=f"202606{index + 1:02d}",
                lot_code=f"LOT-15IN-{index + 1:02d}",
                reel_diameter="15inch",
            )
        )

    plans: list[ReelPlan] = []
    for identity in cell_identities:
        for layer in range(1, CELL_CAPACITY + 1):
            plans.append(
                ReelPlan(
                    pkg_id=identity.pkg_id.replace("-001", f"-{layer:03d}"),
                    material_code=identity.material_code,
                    date_code=identity.date_code,
                    lot_code=identity.lot_code,
                    reel_diameter=identity.reel_diameter,
                )
            )
    return plans


def _base_context(plan: ReelPlan, *, active_rack: dict[str, Any] | None, trace_id: str) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "workline_code": WORKLINE_CODE,
        "position_code": "SINGLE_LAYER_A",
        "six_in_one": plan.six_in_one,
        "reel_diameter": plan.reel_diameter,
        "reel_thickness": 1.0,
        "active_bin_rack": active_rack,
        "wms_rcs_rack_supply_url": RACK_SUPPLY_TARGET,
        "smt_full_box_release_device_code": FULL_BOX_RELEASE_DEVICE,
    }


def _plugin_ctx(
    *,
    context: dict[str, Any],
    trace_id: str,
    current_wait_type: str | None = None,
    config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
        next=PluginNext(),
        config=config or {},
        trace_id=trace_id,
        workline=SimpleNamespace(line_code=WORKLINE_CODE, config=config or {}),
        session=SimpleNamespace(id=1001, context_json=context, current_wait_type=current_wait_type, trace_id=trace_id),
        services=SimpleNamespace(
            bin_allocator=SmtRackBinSchedulingService(),
            active_rack_snapshot_provider=None,
        ),
        source_device_role="CONVEYOR",
        normalized_input=None,
    )


def _move_forward_success(plan: ReelPlan, *, trace_id: str) -> NormalizedCommandResult:
    return NormalizedCommandResult(
        command_code=f"MOVE-{plan.pkg_id}",
        command_type="MOVE_FORWARD",
        device_code="PIPELINE01",
        source_result="SUCCESS",
        normalized_result="SUCCESS",
        trace_id=trace_id,
        data={"PkgID": plan.pkg_id},
    )


def _rack_arrived_payload(*, rack: dict[str, Any], dispatch_key: str, trace_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "callback_type": "WMS_RACK_ARRIVED",
        "dispatch_key": dispatch_key,
        "source_system": "RCS",
        "source_event_id": f"RCS-RACK-ARRIVED-{RACK_ID}",
        "source_version": "1",
        "occurred_at": now,
        "request_id": f"REQ-{RACK_ID}",
        "timestamp": now,
        "signature": "simulation-signature",
        "trace_id": trace_id,
        "workline_code": WORKLINE_CODE,
        "position_code": "SINGLE_LAYER_A",
        "active_bin_rack": rack,
    }


def _apply_allocation(rack: dict[str, Any], plan: ReelPlan, bin_location: dict[str, Any]) -> None:
    target_cell = next(
        cell
        for cell in rack["cells"]
        if cell["bin_id"] == bin_location["bin_id"] and cell["bin_cell_index"] == bin_location["bin_cell_index"]
    )
    six_in_one = plan.six_in_one
    reel_count = int(target_cell.get("reel_count") or 0) + 1
    target_cell.update(
        {
            "status": "FULL" if reel_count >= CELL_CAPACITY else "OCCUPIED",
            "HHPN": six_in_one["HHPN"],
            "MfrPN": six_in_one["MfrPN"],
            "DateCode": six_in_one["DateCode"],
            "LotCode": six_in_one["LotCode"],
            "material_identity_key": _material_identity_key(six_in_one),
            "reel_count": reel_count,
            "used_depth_mm": float(reel_count),
            "remaining_depth_mm": float(max(CELL_CAPACITY - reel_count, 0)),
        }
    )


def _assert_rack_shape(rack: dict[str, Any]) -> None:
    cells = rack["cells"]
    assert len(cells) == 18
    assert sum(1 for cell in cells if cell["rack_slot_code"] in {"A", "B"} and cell["bin_type"] == "6格箱") == 12
    assert sum(1 for cell in cells if cell["rack_slot_code"] in {"C", "D"} and cell["bin_type"] == "3格箱") == 6


def _assert_full_rack(rack: dict[str, Any]) -> None:
    cells = rack["cells"]
    assert {cell["status"] for cell in cells} == {"FULL"}
    assert {int(cell["reel_count"]) for cell in cells} == {CELL_CAPACITY}
    assert sum(int(cell["reel_count"]) for cell in cells) == 90


@pytest.mark.asyncio
async def test_single_layer_rack_flow_from_startup_to_full_box_exchange() -> None:
    """从开机无货架开始，模拟粗分机补架、出料填满和满箱交换闭环。"""

    scheduler = SmtRackBinSchedulingService()
    classifier = SmtClassifierPlugin()
    full_box_plugin = SmtFullBoxExchangePlugin()
    rack = _build_single_layer_rack()
    _assert_rack_shape(rack)

    plans = _build_reel_plans()
    assert len({plan.material_code for plan in plans}) == 10
    first_plan = plans[0]
    trace_id = "trace-smt-single-layer-full-box-001"

    boot_ctx = _plugin_ctx(
        context=_base_context(first_plan, active_rack=None, trace_id=trace_id),
        trace_id=trace_id,
    )
    boot_intents = await classifier.handle_conveyor_success(
        boot_ctx, _move_forward_success(first_plan, trace_id=trace_id)
    )

    assert [intent.kind for intent in boot_intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    rack_supply = boot_intents[0].context_patch["rack_supply"]
    assert rack_supply["reason_code"] == "NO_ACTIVE_RACK"
    assert boot_intents[1].target_code == RACK_SUPPLY_TARGET
    assert boot_intents[1].payload_json["request_type"] == "SMT_RACK_SUPPLY"
    assert boot_intents[1].payload_json["actions"] == ["SUPPLY_EMPTY_RACK"]

    arrived_context = {**boot_ctx.session.context_json, **boot_intents[0].context_patch}
    arrived_intents = await classifier.on_external_http(
        _plugin_ctx(context=arrived_context, trace_id=trace_id, current_wait_type="EXTERNAL_HTTP"),
        SimpleNamespace(
            payload_json=_rack_arrived_payload(rack=rack, dispatch_key=rack_supply["dispatch_key"], trace_id=trace_id)
        ),
    )

    assert [intent.kind for intent in arrived_intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.COMMAND,
    ]
    first_bin_location = arrived_intents[2].context_patch["bin_location"]
    assert arrived_intents[-1].device_role == "OUTPUT_ARM"
    assert arrived_intents[-1].action == "PICK_AND_PUT"
    _apply_allocation(rack, first_plan, first_bin_location)

    allocation_log: list[dict[str, Any]] = [{"pkg_id": first_plan.pkg_id, "bin_location": first_bin_location}]
    for plan in plans[1:]:
        decision = scheduler.plan_allocation(
            plan.pkg_id,
            context=_base_context(plan, active_rack=rack, trace_id=f"trace-{plan.pkg_id}"),
        )
        assert decision.kind == "ALLOCATED"
        assert decision.bin_location is not None
        bin_location = dict(decision.bin_location)
        _apply_allocation(rack, plan, bin_location)
        allocation_log.append({"pkg_id": plan.pkg_id, "bin_location": bin_location})

    _assert_full_rack(rack)
    assert len(allocation_log) == 90

    overflow_plan = ReelPlan(
        pkg_id="PKG-OVERFLOW-001",
        material_code=MATERIAL_CODES[0],
        date_code="20260701",
        lot_code="LOT-OVERFLOW",
        reel_diameter="7inch",
    )
    overflow_decision = scheduler.plan_allocation(
        overflow_plan.pkg_id,
        context=_base_context(overflow_plan, active_rack=rack, trace_id="trace-overflow"),
    )

    assert overflow_decision.kind == "RACK_SUPPLY_REQUIRED"
    assert overflow_decision.rack_supply_request is not None
    assert overflow_decision.rack_release_event is not None
    assert overflow_decision.rack_release_event.event_type == "SINGLE_LAYER_RACK_RELEASED"
    assert overflow_decision.rack_release_event.data["single_layer_rack_id"] == RACK_ID
    assert overflow_decision.rack_release_event.data["release_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
    assert len(overflow_decision.rack_release_event.data["bin_snapshots"]) == 4
    assert {item["status"] for item in overflow_decision.rack_release_event.data["bin_snapshots"]} == {"FULL"}

    exchange_ctx = _plugin_ctx(
        context={},
        trace_id="trace-overflow",
        current_wait_type="DEVICE_EVENT",
        config={
            "external_endpoints": {"wms_rcs_full_box_exchange_url": FULL_BOX_EXCHANGE_TARGET},
            "exchange_area_code": "SMT_FULL_BOX_EXCHANGE_A",
            "callback_url": "http://localhost:8001/api/v1/callback/external",
            "timeouts": {"external_exchange_seconds": 1800},
        },
    )
    release_event = overflow_decision.rack_release_event
    exchange_intents = await full_box_plugin.on_device_event(
        exchange_ctx,
        SimpleNamespace(
            id=1,
            trace_id=release_event.causation_id,
            payload_json={
                "message_type": "DEVICE_EVENT",
                "event_type": release_event.event_type,
                "canonical_event_type": release_event.canonical_event_type,
                "data": dict(release_event.data),
            },
        ),
    )

    assert [intent.kind for intent in exchange_intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    exchange_context = exchange_intents[0].context_patch
    exchange_request = exchange_intents[1]
    assert exchange_context["exchange_required"] is True
    assert exchange_context["qualified_bin_count"] == 4
    assert exchange_request.target_code == FULL_BOX_EXCHANGE_TARGET
    assert exchange_request.payload_json["request_type"] == "SMT_FULL_BOX_EXCHANGE"
    assert len(exchange_request.payload_json["exchange_bins"]) == 4

    complete_intents = await full_box_plugin.on_external_http(
        _plugin_ctx(
            context=exchange_context,
            trace_id="trace-overflow",
            current_wait_type="EXTERNAL_HTTP",
        ),
        SimpleNamespace(
            payload_json={
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-overflow",
                "rack_release_id": exchange_context["rack_release_id"],
                "dispatch_key": exchange_request.dispatch_key,
                "exchange_request_code": exchange_request.dispatch_key,
                "exchange_status": "BUSINESS_COMPLETED",
                "wms_confirmation": {
                    "wms_document_id": "WMS-FULL-BOX-DEV-001",
                    "inventory_version": "INV-FULL-BOX-DEV-001",
                },
            },
        ),
    )

    assert [intent.kind for intent in complete_intents] == [RuntimeIntentKind.COMPLETE]
    assert complete_intents[0].context_patch["full_box_exchange"]["exchange_status"] == "BUSINESS_COMPLETED"
