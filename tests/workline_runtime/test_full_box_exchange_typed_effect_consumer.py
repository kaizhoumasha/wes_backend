"""Material-flow `full_box_exchange` typed EFFECT 消费合同。"""

from __future__ import annotations

from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import SorterInboundRuntimeService
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind


def _payload(*, request_id: str = "REQ-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "correlation_id": "CORR-001",
        "provider_code": "WMS",
        "rack_code": "RACK-001",
        "rack_side": "A",
        "empty_box_id": "EMPTY-001",
        "full_box_id": "FULL-001",
        "full_box_object_keys": ["PKG-FULL-001"],
        "remaining_object_keys": ["PKG-FULL-001", "PKG-PIECE-001"],
        "source_version": "rack-state:v7",
        "plugin_binding_id": 23,
        "plugin_binding_version": 5,
        "workline_id": 7,
        "session_id": 11,
        "trace_id": "trace-full-box-exchange",
    }


def test_runtime_emits_one_typed_system_capability_and_filters_sorting_candidates() -> None:
    plan = SorterInboundRuntimeService().build_full_box_exchange_plan(_payload())

    intents = [
        intent
        for intent in plan.intents
        if intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
        and intent.capability_key == "wms.fulfillment.full_box_exchange"
    ]

    assert len(intents) == 1
    intent = intents[0]
    assert intent.contract_version == "v1"
    assert intent.operation_key == "WMS:RACK-001:EMPTY-001:FULL-001"
    assert intent.dispatch_key == "wms-full-box-exchange:WMS:RACK-001:EMPTY-001:FULL-001"
    assert intent.payload_json == {
        "dispatch_key": "wms-full-box-exchange:WMS:RACK-001:EMPTY-001:FULL-001",
        "provider_code": "WMS",
        "rack_id": "RACK-001",
        "empty_box_id": "EMPTY-001",
        "full_box_id": "FULL-001",
        "workline_id": 7,
        "session_id": 11,
        "trace_id": "trace-full-box-exchange",
    }
    assert intent.precondition_json == {
        "rack_id": "RACK-001",
        "empty_box_id": "EMPTY-001",
        "full_box_id": "FULL-001",
        "local_physical_fact_recorded": True,
    }
    assert intent.fact_version == "rack-state:v7"
    assert plan.evidence["sorting_candidate_object_keys"] == ["PKG-PIECE-001"]


def test_dispatch_identity_is_stable_across_request_replay() -> None:
    service = SorterInboundRuntimeService()
    first = service.build_full_box_exchange_plan(_payload(request_id="REQ-001"))
    second = service.build_full_box_exchange_plan(_payload(request_id="REQ-REPLAY"))

    assert first.intents[0].operation_key == second.intents[0].operation_key
    assert first.intents[0].dispatch_key == second.intents[0].dispatch_key
    assert first.intents[0].payload_hash == second.intents[0].payload_hash
