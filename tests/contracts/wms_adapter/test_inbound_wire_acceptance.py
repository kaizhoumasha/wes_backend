from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.inbound_wire import (
    ADMISSION_OPERATION,
    DECISION_PATH,
    FACT_PATH,
    NG_PLACEMENT_OPERATION,
    PLACEMENT_OPERATION,
    RECOVERY_OPERATION,
    REPLACEMENT_PLAN_OPERATION,
    TARGET_OPERATION,
    parse_outbound_request,
    parse_outbound_response,
    parse_recovery_event,
)

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


def _envelope(operation: str, data: dict[str, object]) -> dict[str, object]:
    return {"operation_id": OPERATION_ID, "operation": operation, "timestamp": 1, "data": data}


def _handoff(code: str = "LINE-IN") -> dict[str, str]:
    return {"type": "HANDOFF_POSITION", "location_code": code}


def _cell() -> dict[str, str]:
    return {
        "type": "ONE_LAYER_BIN_CELL",
        "rack_id": "RACK-1",
        "rack_slot_code": "SLOT-1",
        "bin_id": "BIN-1",
        "bin_cell_id": "CELL-1",
    }


def _admission_data() -> dict[str, object]:
    return {
        "material_execution_id": "EXEC-1",
        "material_trace_id": "TRACE-1",
        "six_in_one": {
            "LotCode": "LOT",
            "DateCode": "20260816",
            "Qty": "1",
            "ProductNo": "PN",
            "MfrPN": "MFR",
            "PONumber": "PO",
        },
        "measurements": {"diameter_mm": "12.345", "thickness_mm": "0.500"},
        "shape_result": "PASS",
        "line_run_epoch_id": "EPOCH-1",
        "workline_code": "WL-1",
        "source_position": _handoff(),
    }


def _replacement_response(face: object = "90") -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {
            "result": "READY",
            "rack_replacement_id": "replacement-1",
            "old_loaded_rack": {
                "rack_id": "rack-old",
                "source": {"kind": "RACK_POSITION", "location_code": "work"},
                "target": {"kind": "ZONE", "location_code": "storage-zone"},
                "target_face": face,
            },
            "new_empty_rack": {
                "rack_id": "rack-new",
                "source": {"kind": "RACK", "location_code": "rack-new"},
                "target": {"kind": "RACK_POSITION", "location_code": "work"},
                "target_face": "270",
            },
        },
    }


def test_outbound_operation_closure_and_paths_are_exact() -> None:
    assert {
        ADMISSION_OPERATION,
        TARGET_OPERATION,
        PLACEMENT_OPERATION,
        NG_PLACEMENT_OPERATION,
        REPLACEMENT_PLAN_OPERATION,
    } == {
        "inbound.material.admission_decide@v1",
        "inbound.material.target_decide@v1",
        "inbound.material.placement_report@v1",
        "inbound.material.ng_placement_report@v1",
        "inbound.source_rack.replacement_plan_decide@v1",
    }
    assert RECOVERY_OPERATION == "inbound.execution.recovery_decided@v1"
    assert (DECISION_PATH, FACT_PATH) == ("/api/v1/wes/decisions", "/api/v1/wes/facts")


def test_admission_request_is_strict_and_preserves_measurement_strings() -> None:
    request = parse_outbound_request(
        _envelope(
            ADMISSION_OPERATION,
            {
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "six_in_one": {
                    "LotCode": "LOT",
                    "DateCode": "20260816",
                    "Qty": "1",
                    "ProductNo": "PN",
                    "MfrPN": "MFR",
                    "PONumber": "PO",
                },
                "measurements": {"diameter_mm": "12.345", "thickness_mm": "0.500"},
                "shape_result": "PASS",
                "line_run_epoch_id": "EPOCH-1",
                "workline_code": "WL-1",
                "source_position": _handoff(),
            },
        )
    )

    assert request.operation == ADMISSION_OPERATION
    assert request.data.measurements.diameter_mm == "12.345"

    invalid = request.model_dump(mode="json")
    invalid["data"]["measurements"]["diameter_mm"] = 12.345
    with pytest.raises(ValidationError):
        parse_outbound_request(invalid)
    invalid = request.model_dump(mode="json")
    invalid["data"]["unexpected"] = True
    with pytest.raises(ValidationError):
        parse_outbound_request(invalid)


@pytest.mark.parametrize(
    ("operation", "data"),
    [
        (
            TARGET_OPERATION,
            {
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "pkg_id": "PKG-1",
                "inbound_admission_id": "ADM-1",
                "source_position": _handoff("LINE-OUT"),
                "current_rack_id": "RACK-1",
            },
        ),
        (
            PLACEMENT_OPERATION,
            {
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "pkg_id": "PKG-1",
                "inbound_admission_id": "ADM-1",
                "target_assignment_id": "TARGET-1",
                "target_position": _cell(),
                "placement_sequence": 1,
                "command_code": "CMD-1",
                "placed_at": 1,
            },
        ),
        (
            NG_PLACEMENT_OPERATION,
            {
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "ng_evidence_id": "EVIDENCE-1",
                "ng_position": {"type": "NG_POSITION", "location_code": "NG-1"},
                "reason_code": "BUSINESS_REJECT",
                "business_context": "ROUGH_SORT_INBOUND",
            },
        ),
        (
            REPLACEMENT_PLAN_OPERATION,
            {
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "current_rack_id": "RACK-1",
            },
        ),
    ],
)
def test_other_outbound_requests_are_closed(operation: str, data: dict[str, object]) -> None:
    request = parse_outbound_request(_envelope(operation, data))
    assert request.operation == operation
    with pytest.raises(ValidationError):
        parse_outbound_request(_envelope(operation, {**data, "unknown": 1}))


def test_optional_pkg_id_must_be_omitted_instead_of_null() -> None:
    with pytest.raises(ValidationError):
        parse_outbound_request(
            _envelope(
                NG_PLACEMENT_OPERATION,
                {
                    "material_execution_id": "EXEC-1",
                    "material_trace_id": "TRACE-1",
                    "pkg_id": None,
                    "ng_evidence_id": "EVIDENCE-1",
                    "ng_position": {"type": "NG_POSITION", "location_code": "NG-1"},
                    "reason_code": "BUSINESS_REJECT",
                    "business_context": "ROUGH_SORT_INBOUND",
                },
            )
        )


def test_identifier_constraints_follow_the_authoritative_field_owners() -> None:
    request = parse_outbound_request(
        _envelope(
            ADMISSION_OPERATION,
            {
                "material_execution_id": "执" * 120,
                "material_trace_id": "料" * 160,
                "six_in_one": {
                    "LotCode": "LOT",
                    "DateCode": "20260816",
                    "Qty": "1",
                    "ProductNo": "PN",
                    "MfrPN": "MFR",
                    "PONumber": "PO",
                },
                "measurements": {"diameter_mm": "12.345", "thickness_mm": "0.500"},
                "shape_result": "PASS",
                "line_run_epoch_id": "运行批次" * 40,
                "workline_code": "粗分工作线" * 30,
                "source_position": _handoff("入口位置" * 30),
            },
        )
    )
    assert request.data.workline_code == "粗分工作线" * 30

    for field, value in (("material_execution_id", "E" * 121), ("material_trace_id", "T" * 161)):
        invalid = request.model_dump(mode="json")
        invalid["data"][field] = value
        with pytest.raises(ValidationError):
            parse_outbound_request(invalid)

    for field, value in (("workline_code", "   "), ("line_run_epoch_id", "EPOCH\x00BAD")):
        invalid = request.model_dump(mode="json")
        invalid["data"][field] = value
        with pytest.raises(ValidationError):
            parse_outbound_request(invalid)

    placement = _envelope(
        PLACEMENT_OPERATION,
        {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "pkg_id": "业务身份" * 40,
            "inbound_admission_id": "准入身份" * 40,
            "target_assignment_id": "目标分配" * 40,
            "target_position": _cell(),
            "placement_sequence": 1,
            "command_code": "C" * 160,
            "placed_at": 1,
        },
    )
    assert parse_outbound_request(placement).data.command_code == "C" * 160
    placement["data"]["command_code"] = "C" * 161
    with pytest.raises(ValidationError):
        parse_outbound_request(placement)


def test_device_text_has_no_field_cap_but_must_be_nonblank_nul_free_utf8() -> None:
    data = _admission_data()
    data["six_in_one"]["LotCode"] = "批次" * 300  # type: ignore[index]
    request = parse_outbound_request(_envelope(ADMISSION_OPERATION, data))
    assert request.data.six_in_one.LotCode == "批次" * 300

    for invalid in ("   ", "LOT\x00BAD", "\ud800"):
        invalid_data = _admission_data()
        invalid_data["six_in_one"]["LotCode"] = invalid  # type: ignore[index]
        with pytest.raises(ValidationError):
            parse_outbound_request(_envelope(ADMISSION_OPERATION, invalid_data))


@pytest.mark.parametrize("measurement", ["-1", "+1", "1e3", "01", "1."])
def test_measurement_string_rejects_noncanonical_decimal_forms(measurement: str) -> None:
    data = _admission_data()
    data["measurements"]["diameter_mm"] = measurement  # type: ignore[index]
    with pytest.raises(ValidationError):
        parse_outbound_request(_envelope(ADMISSION_OPERATION, data))


def test_measurement_string_accepts_arbitrary_fractional_precision() -> None:
    data = _admission_data()
    data["measurements"]["diameter_mm"] = "0.123456789012345678901234567890"  # type: ignore[index]
    request = parse_outbound_request(_envelope(ADMISSION_OPERATION, data))
    assert request.data.measurements.diameter_mm == "0.123456789012345678901234567890"


def test_response_status_code_and_result_pairings_are_strict() -> None:
    admission = parse_outbound_response(
        ADMISSION_OPERATION,
        200,
        {
            "operation_id": OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 2,
            "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
        },
    )
    assert admission.data.result == "ACCEPT"

    wait = parse_outbound_response(
        TARGET_OPERATION,
        200,
        {
            "operation_id": OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 2,
            "data": {"result": "WAIT", "reason_code": "BUSY", "retry_after_ms": 60_000},
        },
    )
    assert wait.data.retry_after_ms == 60_000

    with pytest.raises(ValidationError):
        parse_outbound_response(
            TARGET_OPERATION,
            200,
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "WAIT", "reason_code": "BUSY", "retry_after_ms": 60_001},
            },
        )
    with pytest.raises((ValidationError, ValueError)):
        parse_outbound_response(
            PLACEMENT_OPERATION, 202, {"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}}
        )
    with pytest.raises(ValidationError):
        parse_outbound_response(
            PLACEMENT_OPERATION, 200, {"operation_id": OPERATION_ID, "code": "DECIDED", "timestamp": 2, "data": {}}
        )
    with pytest.raises(ValidationError):
        parse_outbound_response(
            ADMISSION_OPERATION,
            422,
            {
                "operation_id": OPERATION_ID,
                "code": "REJECTED",
                "timestamp": 2,
                "data": {"reason_code": "INVALID_DATA", "field_path": "data.material_trace_id"},
            },
        )


@pytest.mark.parametrize("face", ["90", "270", "FACE@01", "面-1", " ", "\x00", "x" * 1000])
def test_replacement_response_preserves_broad_positions_and_any_non_empty_face(face: str) -> None:
    response = parse_outbound_response(REPLACEMENT_PLAN_OPERATION, 200, _replacement_response(face))

    assert response.data.old_loaded_rack.target.kind == "ZONE"
    assert response.data.old_loaded_rack.target_face == face
    assert response.data.new_empty_rack.source.kind == "RACK"


@pytest.mark.parametrize("face", ["", None, 90, True])
def test_replacement_response_rejects_empty_or_non_string_face(face: object) -> None:
    with pytest.raises(ValidationError):
        parse_outbound_response(REPLACEMENT_PLAN_OPERATION, 200, _replacement_response(face))


def test_replacement_response_rejects_mismatched_rack_reference_identity() -> None:
    response = _replacement_response()
    response["data"]["new_empty_rack"]["source"]["location_code"] = "other-rack"  # type: ignore[index]

    with pytest.raises(ValidationError, match="rack_id"):
        parse_outbound_response(REPLACEMENT_PLAN_OPERATION, 200, response)


def test_recovery_is_single_execution_strict_and_continue_has_no_null_position() -> None:
    value = _envelope(
        RECOVERY_OPERATION,
        {
            "recovery_id": "REC-1",
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "reconciling_evidence_id": "31",
            "decision": "CONTINUE",
            "authoritative_position": _handoff(),
            "reason_code": "MANUAL_CONFIRMED",
        },
    )
    assert parse_recovery_event(value).data.decision == "CONTINUE"

    legacy_batch = value.copy()
    legacy_batch["data"] = {**value["data"], "affected_execution_ids": ["EXEC-1"]}
    with pytest.raises(ValidationError):
        parse_recovery_event(legacy_batch)
    null_position = value.copy()
    null_position["data"] = {
        **value["data"],
        "authoritative_position": None,
    }
    with pytest.raises(ValidationError):
        parse_recovery_event(null_position)
    old_operation = {**value, "operation": "inbound.execution.reconciliation_decided@v1"}
    with pytest.raises(ValidationError):
        parse_recovery_event(old_operation)


@pytest.mark.parametrize("timestamp", [0, -1, True, 1.0])
def test_envelope_timestamp_is_positive_strict_integer(timestamp: object) -> None:
    with pytest.raises(ValidationError):
        parse_outbound_request(
            {
                **_envelope(
                    REPLACEMENT_PLAN_OPERATION,
                    {"material_execution_id": "EXEC-1", "material_trace_id": "TRACE-1", "current_rack_id": "RACK-1"},
                ),
                "timestamp": timestamp,
            }
        )
