"""Material-flow `notify_pkg_binding` typed EFFECT 消费合同。"""

from __future__ import annotations

from src.app.runtime.capabilities.material_flow.sorter_inbound_preview_service import SorterInboundPreviewService
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import SorterInboundRuntimeService
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.wms_integration.ports.notify_pkg_binding_operation import OPERATION_IDENTITY


def _payload(*, request_id: str = "REQ-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "correlation_id": "CORR-001",
        "provider_code": "WMS",
        "object_key": "PKG-001",
        "bin_code": "BIN-001",
        "bin_cell_index": "1",
        "target_cell_code": "CELL-001",
        "pkg_code": "PKG-001",
        "pallet_id": "PALLET-001",
        "station_code": "STATION-001",
        "material_code": "MAT-001",
        "quantity": "1.250",
        "warehouse_code": "WH-001",
        "source_version": "runtime-location:v7",
        "plugin_binding_id": 23,
        "plugin_binding_version": 5,
        "workline_id": 7,
        "session_id": 11,
        "trace_id": "trace-notify-pkg-binding",
    }


def test_sorter_runtime_emits_one_typed_notify_pkg_binding_system_capability() -> None:
    plan = SorterInboundRuntimeService().build_rough_sorter_inbound_plan(_payload())

    binding_intents = [
        intent
        for intent in plan.intents
        if intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
        and intent.capability_key == "wms.fulfillment.notify_pkg_binding"
    ]

    assert len(binding_intents) == 1
    intent = binding_intents[0]
    assert intent.contract_version == "v1"
    assert intent.operation_key == "WMS:PKG-001:PALLET-001"
    assert intent.dispatch_key == "wms-notify-pkg-binding:WMS:PKG-001:PALLET-001"
    assert intent.payload_json == {
        "dispatch_key": "wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
        "provider_code": "WMS",
        "package_id": "PKG-001",
        "pallet_id": "PALLET-001",
        "station_code": "STATION-001",
        "workline_id": 7,
        "session_id": 11,
        "trace_id": "trace-notify-pkg-binding",
    }
    assert intent.precondition_json == {
        "package_id": "PKG-001",
        "pallet_id": "PALLET-001",
        "local_physical_fact_recorded": True,
    }
    assert intent.fact_version == "runtime-location:v7"
    assert intent.binding_snapshot == {"binding_id": 23, "binding_version": 5}
    assert all(value.kind is not RuntimeIntentKind.EXTERNAL_REQUEST for value in plan.intents)


def test_notify_pkg_binding_dispatch_identity_is_stable_across_request_replay() -> None:
    service = SorterInboundRuntimeService()
    first = service.build_rough_sorter_inbound_plan(_payload(request_id="REQ-001"))
    second = service.build_rough_sorter_inbound_plan(_payload(request_id="REQ-REPLAY"))

    first_binding = next(
        intent for intent in first.intents if intent.capability_key == "wms.fulfillment.notify_pkg_binding"
    )
    second_binding = next(
        intent for intent in second.intents if intent.capability_key == "wms.fulfillment.notify_pkg_binding"
    )

    assert first_binding.operation_key == second_binding.operation_key == "WMS:PKG-001:PALLET-001"
    assert (
        first_binding.dispatch_key == second_binding.dispatch_key == ("wms-notify-pkg-binding:WMS:PKG-001:PALLET-001")
    )
    assert first_binding.payload_hash == second_binding.payload_hash


def test_sorter_preview_exposes_stable_notify_pkg_binding_operation_identity() -> None:
    preview = SorterInboundPreviewService().preview_rough_sorter_inbound(
        {
            "request_id": "REQ-001",
            "object_key": "PKG-001",
            "target_cell_code": "CELL-001",
            "local_physical_completed": True,
        }
    )

    assert preview["effect_ports"]["pkg_binding"] == OPERATION_IDENTITY
