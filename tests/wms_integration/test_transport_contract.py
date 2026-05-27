from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.rack.models import RackTaskType
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.services import WmsTransportContractService


def test_transport_contract_builds_rack_task_envelope_without_behavior_drift() -> None:
    service = WmsTransportContractService()

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
    envelope = WmsTransportContractService().build_rack_task_envelope(
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
        metadata_json={"rack_type": "SINGLE_LAYER", "priority": 8},
    )

    envelope = WmsTransportContractService().build_handling_ctu_move_envelope(
        operation=operation,
        move=move,
        sequence_no=1,
        is_full_box_exchange=True,
    )
    payload = envelope["payload_json"]

    assert envelope["dispatch_key"] == "handling:full-box:release-001:move:1"
    assert envelope["target_code"] == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert payload["request_id"] == envelope["dispatch_key"]
    assert payload["dispatch_key"] == envelope["dispatch_key"]
    assert payload["exchange_request_code"] == envelope["dispatch_key"]
    assert payload["callback_type"] == "WMS_FULL_BOX_EXCHANGE_RESULT"
    assert payload["request_type"] == "FULL_BIN_EXCHANGE"
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

    envelope = WmsTransportContractService().build_handling_ctu_move_envelope(
        operation=operation,
        move=move,
        sequence_no=3,
        is_full_box_exchange=False,
    )
    payload = envelope["payload_json"]

    assert envelope["dispatch_key"] == "handling:bin-op-001:move:3"
    assert envelope["target_code"] == "WMS_RCS_BIN_OPERATION"
    assert payload["callback_type"] == "WMS_TRANSPORT_COMPLETED"
    assert payload["request_type"] == "BIN_MOVE"
    assert "bin_code" not in payload
    assert "material_session_id" not in payload
    assert "code" not in payload["carrier"]


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
