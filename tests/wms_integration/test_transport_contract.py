from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.rack.models import RackTaskType
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.services import DEFAULT_RACK_OPERATION_ENDPOINT, WmsTransportContractService


def _transport_service() -> WmsTransportContractService:
    return WmsTransportContractService(
        registry=EndpointRegistry(
            {
                "WMS_RCS_RACK_OPERATION": "http://wms-rcs/api/wes/rack-operation",
                "WMS_RCS_BIN_OPERATION": "http://wms-rcs/api/wes/transport-request",
                "WMS_RCS_FULL_BOX_EXCHANGE": "http://wms-rcs/api/wes/full-box-exchange",
            }
        )
    )


def test_transport_contract_builds_rack_task_envelope_without_behavior_drift() -> None:
    service = _transport_service()

    envelope = service.build_rack_task_envelope(
        operation_key="op-001",
        operation_type="RACK_TRANSPORT",
        sequence_no=2,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
        trace_id="trace-rack-001",
        workline_id=45,
        workline_code="WL-SMT-01",
        material_session_id=300,
        rack_code="RACK-001",
        rack_kind="SINGLE_LAYER",
        source_position_code="SRC-01",
        target_position_code="DST-01",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
        actions_json={"priority": 8},
        request_json={"external_batch": "batch-001"},
    )

    assert envelope.dispatch_key == "rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK"
    assert envelope.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP
    assert envelope.target_type == SystemOutboxTargetType.HTTP_ENDPOINT
    assert envelope.target_code == "WMS_RCS_RACK_OPERATION"
    assert envelope.operation_domain == "RACK"
    assert envelope.operation_key == "op-001"
    assert envelope.workline_id == 45
    assert envelope.session_id == 300
    assert envelope.trace_id == "trace-rack-001"
    assert envelope.payload_json == {
        "external_batch": "batch-001",
        "request_id": "rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
        "dispatch_key": "rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
        "callback_type": "WMS_RACK_ARRIVED",
        "operation_key": "op-001",
        "operation_type": "RACK_TRANSPORT",
        "sequence_no": 2,
        "task_type": "ALLOCATE_AND_MOVE_RACK",
        "workline_code": "WL-SMT-01",
        "rack_code": "RACK-001",
        "rack_kind": "SINGLE_LAYER",
        "source_position_code": "SRC-01",
        "target_position_code": "DST-01",
        "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
        "source": {"position_code": "SRC-01"},
        "target": {
            "position_code": "DST-01",
            "position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
        },
        "trace_id": "trace-rack-001",
        "actions": {"priority": 8, "action": "ALLOCATE_AND_MOVE_RACK"},
        "station": {"workline_code": "WL-SMT-01", "position_code": "DST-01"},
        "position_code": "DST-01",
    }


@pytest.mark.parametrize(
    ("task_type", "callback_type"),
    [
        (RackTaskType.MOVE_RACK.value, "WMS_RACK_TASK_RESULT"),
        (RackTaskType.TURN_RACK_SIDE.value, "WMS_RACK_TASK_RESULT"),
        (RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "WMS_RACK_ARRIVED"),
    ],
)
def test_transport_contract_preserves_rack_callback_types(task_type: str, callback_type: str) -> None:
    envelope = _transport_service().build_rack_task_envelope(
        operation_key=f"op-{task_type}",
        operation_type="RACK_TRANSPORT",
        sequence_no=1,
        task_type=task_type,
        trace_id=f"trace-{task_type}",
        workline_id=None,
        workline_code="WL-SMT-01",
        material_session_id=None,
        rack_code=None,
        rack_kind=None,
        source_position_code=None,
        target_position_code="DST-01",
        target_position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK",
    )

    assert envelope.payload_json["callback_type"] == callback_type


def test_transport_contract_builds_full_box_handling_payload_without_behavior_drift() -> None:
    operation = SimpleNamespace(
        operation_key="full-box:release-001",
        operation_type="SMT_FULL_BOX_EXCHANGE_RELEASE",
        trace_id="trace-full-box-001",
        workline_code="WL-SMT-01",
        material_session_id=300,
    )
    move = _handling_move(
        rack_code="RACK-001",
        rack_slot_code="A",
        metadata_json={"rack_release_id": "release-001", "rack_type": "SINGLE_LAYER", "priority": 8},
    )

    envelope = _transport_service().build_handling_ctu_move_envelope(
        operation=operation,
        move=move,
        sequence_no=1,
        is_full_box_exchange=True,
    )
    payload = envelope.payload_json

    assert envelope.dispatch_key == "handling:full-box:release-001:move:1"
    assert envelope.target_code == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert payload["request_id"] == envelope.dispatch_key
    assert payload["dispatch_key"] == envelope.dispatch_key
    assert payload["exchange_request_code"] == envelope.dispatch_key
    assert payload["callback_type"] == "WMS_FULL_BOX_EXCHANGE_RESULT"
    assert payload["request_type"] == "FULL_BIN_EXCHANGE"
    assert payload["rack_release_id"] == "release-001"
    assert payload["rack_id"] == "RACK-001"
    assert payload["rack_type"] == "SINGLE_LAYER"
    assert payload["rack_slot_code"] == "A"
    assert payload["from_location"] == "RACK-001:A"
    assert payload["to_location"] == "SMT_BUFFER"
    assert payload["priority"] == 8


def test_transport_contract_builds_bin_move_payload_and_drops_none_values() -> None:
    operation = SimpleNamespace(
        operation_key="bin-op-001",
        operation_type="SORTER_FEED_BIN",
        trace_id="trace-bin-001",
        workline_code="WL-SMT-01",
        material_session_id=None,
    )
    move = _handling_move(
        bin_code=None,
        carrier_code=None,
        metadata_json={},
    )

    envelope = _transport_service().build_handling_ctu_move_envelope(
        operation=operation,
        move=move,
        sequence_no=3,
        is_full_box_exchange=False,
    )
    payload = envelope.payload_json

    assert envelope.dispatch_key == "handling:bin-op-001:move:3"
    assert envelope.target_code == "WMS_RCS_BIN_OPERATION"
    assert payload["callback_type"] == "WMS_TRANSPORT_COMPLETED"
    assert payload["request_type"] == "BIN_MOVE"
    assert "bin_code" not in payload
    assert "material_session_id" not in payload
    assert "code" not in payload["carrier"]


def test_transport_contract_builds_single_layer_rack_operation_with_wms_authority() -> None:
    contract = _transport_service().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_code="RACK-001",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        trace_id="trace-single-layer-001",
        payload={
            "target_position_code": "SINGLE_LAYER_A",
            "station": {"position_code": "SINGLE_LAYER_A"},
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": "SINGLE_LAYER_A",
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                }
            ],
        },
        timeout_seconds=1800,
    )

    assert contract["operation_type"] == "SUPPLY_SINGLE_LAYER_RACK"
    assert contract["operation_key"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    assert contract["target_code"] == DEFAULT_RACK_OPERATION_ENDPOINT
    assert not contract["target_code"].startswith(("http://", "https://"))
    assert contract["timeout_seconds"] == 1800

    payload = contract["payload"]
    assert payload["dispatch_key"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    assert payload["business_demand_key"] == "WMS-DEMAND-001"
    assert payload["workline_code"] == "WL-SMT-01"
    assert payload["endpoint_code"] == "SINGLE_LAYER_A"
    assert payload["rack_kind"] == "SINGLE_LAYER"
    assert payload["rack_code"] == "RACK-001"
    assert payload["target_position_code"] == "SINGLE_LAYER_A"
    assert payload["station"]["position_code"] == "SINGLE_LAYER_A"
    assert payload["authority_system"] == "WMS"
    assert "source_system" not in payload


def test_transport_contract_rejects_non_single_layer_rack_kind_for_single_layer_builder() -> None:
    with pytest.raises(ValueError, match="rack_kind must be SINGLE_LAYER"):
        _transport_service().build_single_layer_rack_operation_request(
            business_demand_key="WMS-DEMAND-001",
            workline_code="WL-SMT-01",
            endpoint_code="TARGET_STATION",
            rack_kind="FIVE_LAYER",
            rack_snapshot_ref="snapshot:WL-SMT-01:TARGET_STATION",
            operation_type="SUPPLY_TARGET_RACK",
            payload={"rack_tasks": [_single_layer_supply_task()]},
            timeout_seconds=1800,
        )


def test_transport_contract_preserves_stable_dispatch_key() -> None:
    service = _transport_service()

    first = service.build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={"rack_tasks": [_single_layer_supply_task()]},
        timeout_seconds=1800,
    )
    second = service.build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={"rack_tasks": [_single_layer_supply_task()]},
        timeout_seconds=1800,
    )
    explicit = service.build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={"rack_tasks": [_single_layer_supply_task()]},
        timeout_seconds=1800,
        dispatch_key="dispatch:caller:WMS-DEMAND-001",
    )

    assert first["operation_key"] == second["operation_key"]
    assert first["payload"]["dispatch_key"] == second["payload"]["dispatch_key"]
    assert explicit["operation_key"] == "dispatch:caller:WMS-DEMAND-001"
    assert explicit["payload"]["dispatch_key"] == "dispatch:caller:WMS-DEMAND-001"
    with pytest.raises(ValueError, match="dispatch_key"):
        service.build_single_layer_rack_operation_request(
            business_demand_key="WMS-DEMAND-001",
            workline_code="WL-SMT-01",
            endpoint_code="SINGLE_LAYER_A",
            rack_kind="SINGLE_LAYER",
            rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
            operation_type="SUPPLY_SINGLE_LAYER_RACK",
            payload={"rack_tasks": [_single_layer_supply_task()]},
            timeout_seconds=1800,
            dispatch_key=" ",
        )


def test_transport_contract_allows_wms_allocated_single_layer_rack_request() -> None:
    contract = _transport_service().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-ALLOCATE-001",
        workline_code="WL-SMT-01",
        endpoint_code="SOURCE_STATION_A",
        rack_kind="SINGLE_LAYER",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={"rack_tasks": [_single_layer_supply_task()]},
        timeout_seconds=1800,
    )

    assert contract["payload"]["rack_kind"] == "SINGLE_LAYER"
    assert "rack_code" not in contract["payload"]
    assert "rack_snapshot_ref" not in contract["payload"]
    assert contract["payload"]["authority_system"] == "WMS"


def test_transport_contract_rejects_direct_device_fields_recursively() -> None:
    service = _transport_service()

    for forbidden_key in ("rcs_url", "rcs_path", "agv_id", "ctu_id", "vehicle_id", "physical_coordinate"):
        with pytest.raises(ValueError, match=forbidden_key):
            service.build_single_layer_rack_operation_request(
                business_demand_key="WMS-DEMAND-001",
                workline_code="WL-SMT-01",
                endpoint_code="SINGLE_LAYER_A",
                rack_kind="SINGLE_LAYER",
                rack_code="RACK-001",
                operation_type="SUPPLY_SINGLE_LAYER_RACK",
                payload={
                    "rack_tasks": [_single_layer_supply_task()],
                    "nested": [{"resource": {forbidden_key: "direct-device"}}],
                },
                timeout_seconds=1800,
            )


def test_transport_contract_task_request_json_cannot_override_authority_fields() -> None:
    contract = _transport_service().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_code="RACK-001",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={
            "rack_tasks": [
                {
                    **_single_layer_supply_task(),
                    "request_json": {
                        "business_demand_key": "MUTATED",
                        "workline_code": "MUTATED",
                        "endpoint_code": "MUTATED",
                        "rack_kind": "FIVE_LAYER",
                        "authority_system": "RCS",
                    },
                }
            ]
        },
        timeout_seconds=1800,
    )

    request_json = contract["payload"]["rack_tasks"][0]["request_json"]
    assert request_json["business_demand_key"] == "WMS-DEMAND-001"
    assert request_json["workline_code"] == "WL-SMT-01"
    assert request_json["endpoint_code"] == "SINGLE_LAYER_A"
    assert request_json["rack_kind"] == "SINGLE_LAYER"
    assert request_json["authority_system"] == "WMS"


def test_transport_contract_rejects_url_target_code_case_insensitively() -> None:
    with pytest.raises(ValueError, match="logical endpoint code"):
        _transport_service().build_single_layer_rack_operation_request(
            business_demand_key="WMS-DEMAND-001",
            workline_code="WL-SMT-01",
            endpoint_code="SINGLE_LAYER_A",
            rack_kind="SINGLE_LAYER",
            rack_code="RACK-001",
            operation_type="SUPPLY_SINGLE_LAYER_RACK",
            payload={"rack_tasks": [_single_layer_supply_task()]},
            timeout_seconds=1800,
            target_code="HTTPS://rcs.example.test/task",
        )


def test_transport_contract_deep_copies_payload() -> None:
    payload = {
        "rack_tasks": [_single_layer_supply_task()],
        "station": {"position_code": "SINGLE_LAYER_A"},
    }
    contract = _transport_service().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_code="RACK-001",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload=payload,
        timeout_seconds=1800,
    )

    payload["rack_tasks"][0]["target_position_code"] = "MUTATED"
    payload["station"]["position_code"] = "MUTATED"

    assert contract["payload"]["rack_tasks"][0]["target_position_code"] == "SINGLE_LAYER_A"
    assert contract["payload"]["station"]["position_code"] == "SINGLE_LAYER_A"


def _handling_move(**overrides: Any) -> SimpleNamespace:
    values = {
        "object_type": "BIN",
        "bin_code": "BIN-001",
        "placeholder_key": None,
        "candidate_authorized_bin_ids": ["BIN-001", "BIN-002"],
        "source_type": "RACK_SLOT",
        "source_code": "RACK-001:A",
        "target_type": "BUFFER",
        "target_code": "SMT_BUFFER",
        "carrier_type": "CTU",
        "carrier_code": "CTU-001",
        "rack_code": None,
        "rack_slot_code": None,
        "metadata_json": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _single_layer_supply_task() -> dict[str, Any]:
    return {
        "sequence_no": 1,
        "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
        "rack_kind": "SINGLE_LAYER",
        "target_position_code": "SINGLE_LAYER_A",
        "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
    }
