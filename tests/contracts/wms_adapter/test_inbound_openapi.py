from __future__ import annotations

import json

from src.app.wms_adapter.inbound_openapi import RECONCILIATION_EVENT_REQUEST_SCHEMA, WMS_EVENT_RESPONSES
from src.app.wms_adapter.inbound_wire import RECONCILIATION_OPERATION


def test_inbound_openapi_exposes_only_the_approved_reconciliation_operation() -> None:
    schema = RECONCILIATION_EVENT_REQUEST_SCHEMA

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation_id", "operation", "timestamp", "data"]
    assert schema["properties"]["operation"]["enum"] == [RECONCILIATION_OPERATION]
    serialized = json.dumps(schema, ensure_ascii=False)
    assert "inbound.future" not in serialized
    assert "transport.task" not in serialized


def test_reconciliation_openapi_closes_arrays_positions_and_global_decision_rule() -> None:
    data = RECONCILIATION_EVENT_REQUEST_SCHEMA["properties"]["data"]

    assert data["additionalProperties"] is False
    assert data["properties"]["affected_execution_ids"]["minItems"] == 1
    assert data["properties"]["affected_execution_ids"]["uniqueItems"] is True
    positions = data["properties"]["authoritative_positions"]
    assert positions["minItems"] == 1
    item = positions["items"]
    assert item["additionalProperties"] is False
    assert item["properties"]["position"]["oneOf"][-1] == {"type": "null"}
    assert data["properties"]["decision"]["enum"] == ["CONTINUE", "ABORT"]
    assert "allOf" in data


def test_reconciliation_openapi_uses_field_owned_identifier_limits_without_ascii_restriction() -> None:
    data = RECONCILIATION_EVENT_REQUEST_SCHEMA["properties"]["data"]
    position_item = data["properties"]["authoritative_positions"]["items"]

    assert data["properties"]["affected_execution_ids"]["items"]["maxLength"] == 120
    assert position_item["properties"]["material_execution_id"]["maxLength"] == 120
    assert position_item["properties"]["material_trace_id"]["maxLength"] == 160
    assert "maxLength" not in data["properties"]["reconciliation_id"]
    assert "maxLength" not in position_item["properties"]["pkg_id"]
    assert "A-Za-z" not in json.dumps(RECONCILIATION_EVENT_REQUEST_SCHEMA)


def test_shared_event_responses_include_inbound_empty_ack_and_invalid_data_without_mutating_transport_contract() -> (
    None
):
    received_data = WMS_EVENT_RESPONSES[202]["content"]["application/json"]["schema"]["properties"]["data"]
    rejected_data = WMS_EVENT_RESPONSES[422]["content"]["application/json"]["schema"]["properties"]["data"]

    assert {tuple(variant["required"]) for variant in received_data["oneOf"]} == {
        ("transport_task_id",),
        (),
    }
    reason_codes = {
        reason for variant in rejected_data["oneOf"] for reason in variant["properties"]["reason_code"]["enum"]
    }
    assert reason_codes == {"INVALID_EVIDENCE", "INVALID_DATA", "UNSUPPORTED_OPERATION"}
