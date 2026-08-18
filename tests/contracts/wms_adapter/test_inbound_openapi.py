from __future__ import annotations

import json

from src.app.wms_adapter.inbound_openapi import RECOVERY_EVENT_REQUEST_SCHEMA, WMS_EVENT_RESPONSES
from src.app.wms_adapter.inbound_wire import RECOVERY_OPERATION


def test_inbound_openapi_exposes_only_the_approved_recovery_operation() -> None:
    schema = RECOVERY_EVENT_REQUEST_SCHEMA

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation_id", "operation", "timestamp", "data"]
    assert schema["properties"]["operation"]["enum"] == [RECOVERY_OPERATION]
    serialized = json.dumps(schema, ensure_ascii=False)
    assert "inbound.future" not in serialized
    assert "transport.task" not in serialized


def test_recovery_openapi_closes_single_execution_position_and_decision_rule() -> None:
    data = RECOVERY_EVENT_REQUEST_SCHEMA["properties"]["data"]

    assert data["additionalProperties"] is False
    assert set(data["required"]) == {
        "recovery_id",
        "material_execution_id",
        "material_trace_id",
        "reconciling_evidence_id",
        "decision",
        "authoritative_position",
        "reason_code",
    }
    assert data["properties"]["authoritative_position"]["oneOf"][-1] == {"type": "null"}
    assert data["properties"]["decision"]["enum"] == ["CONTINUE", "ABORT"]
    assert "allOf" in data


def test_recovery_openapi_uses_field_owned_identifier_limits_without_ascii_restriction() -> None:
    data = RECOVERY_EVENT_REQUEST_SCHEMA["properties"]["data"]

    assert data["properties"]["material_execution_id"]["maxLength"] == 120
    assert data["properties"]["material_trace_id"]["maxLength"] == 160
    assert "maxLength" not in data["properties"]["recovery_id"]
    assert "maxLength" not in data["properties"]["reconciling_evidence_id"]
    assert "A-Za-z" not in json.dumps(RECOVERY_EVENT_REQUEST_SCHEMA)


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
