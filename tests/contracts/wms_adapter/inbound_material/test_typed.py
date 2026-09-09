from dataclasses import replace

from wes_plugin_sdk import DevicePosition, wms_operations
from wes_plugin_sdk.wms_types import AdmissionAccepted, AdmissionOutcome, Measurements, SixInOne

from src.app.wms_adapter.inbound_material.typed import decode_outcome, decode_request, encode_request
from src.app.wms_adapter.inbound_material.wire import ADMISSION_OPERATION


def test_admission_typed_round_trip_keeps_wire_identity_and_device_text() -> None:
    intent = wms_operations.inbound_material_admission_decide(
        material_execution_id="EXEC-1",
        fact_id="FACT-1",
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        material_trace_id="TRACE-1",
        six_in_one=SixInOne(" LOT ", "DATE", "1", "PN", "MFR", "PO"),
        measurements=Measurements("1.000", "0.500"),
        shape_result="PASS",
        workline_code="WL-1",
        source_position=DevicePosition("IN-1", "MEASUREMENT_POSITION", "TRACE-1"),
    )
    payload = encode_request(intent, timestamp=1)
    assert payload["operation"] == ADMISSION_OPERATION
    assert payload["data"]["source_position"] == {"type": "HANDOFF_POSITION", "location_code": "IN-1"}
    assert payload["data"]["six_in_one"]["LotCode"] == " LOT "
    assert "fact_id" not in payload["data"]
    assert decode_request(payload, fact_id="REPLAY") == replace(intent, fact_id="REPLAY")


def test_durable_wms_response_becomes_closed_typed_outcome() -> None:
    response = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    assert decode_outcome(ADMISSION_OPERATION, response, material_trace_id="TRACE-1") == AdmissionOutcome(
        AdmissionAccepted("PKG-1", "ADM-1")
    )
    response["data"]["unexpected"] = True
    assert decode_outcome(ADMISSION_OPERATION, response, material_trace_id="TRACE-1") == AdmissionOutcome(
        AdmissionAccepted("PKG-1", "ADM-1")
    )
