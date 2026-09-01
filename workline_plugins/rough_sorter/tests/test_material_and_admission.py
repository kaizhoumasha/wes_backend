from __future__ import annotations

import pytest
from conftest import (
    EPOCH_ID,
    EXECUTION_ID,
    TRACE_ID,
    WORKLINE_CODE,
    runtime_snapshot,
)
from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateWmsConfirmation,
    DeferExecution,
    DevicePosition,
    PauseForReconciliation,
    Wait,
)

from rough_sorter.facts import (
    AdmissionDecidedFact,
    AdmissionResult,
    MaterialEvidenceReadyFact,
    ShapeResult,
)
from rough_sorter.handlers.admission_decided import AdmissionDecidedHandler
from rough_sorter.handlers.material_evidence_ready import MaterialEvidenceReadyHandler


def _position(location_id: str, location_type: str) -> DevicePosition:
    return DevicePosition(
        location_id=location_id,
        location_type=location_type,
        material_trace_id=TRACE_ID,
    )


def _readers(*_positions: tuple[str, str, str | None, bool]):
    return ()


def _material_fact(**overrides: object) -> MaterialEvidenceReadyFact:
    values: dict[str, object] = {
        "fact_id": "evidence:1",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "1",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "material_trace_id": TRACE_ID,
        "line_run_epoch_id": EPOCH_ID,
        "workline_code": WORKLINE_CODE,
        "lot_code": "LOT-1",
        "date_code": "20260817",
        "qty": "100",
        "product_no": "PRODUCT-1",
        "mfr_pn": "MFR-1",
        "po_number": "PO-1",
        "diameter_mm": "100.5",
        "thickness_mm": "2.0",
        "shape_result": ShapeResult.PASS,
        "source_position": _position("measurement", "MEASUREMENT_POSITION"),
        "request_operation_id": "019d0000-0000-7000-8000-000000000001",
    }
    values.update(overrides)
    return MaterialEvidenceReadyFact(**values)


def _admission_fact(result: AdmissionResult, **overrides: object) -> AdmissionDecidedFact:
    values: dict[str, object] = {
        "fact_id": "evidence:2",
        "runtime_snapshot": runtime_snapshot(),
        "evidence_id": "2",
        "fact_version": "1.0",
        "material_execution_id": EXECUTION_ID,
        "operation_id": "019d0000-0000-7000-8000-000000000001",
        "material_trace_id": TRACE_ID,
        "result": result,
        "source_position": _position("measurement", "MEASUREMENT_POSITION"),
        "device_ready": True,
    }
    values.update(overrides)
    return AdmissionDecidedFact(**values)


def test_complete_material_evidence_requests_admission_deterministically() -> None:
    readers = _readers(("measurement", "MEASUREMENT_POSITION", TRACE_ID, False))
    handler = MaterialEvidenceReadyHandler(*readers)
    fact = _material_fact()

    first = handler(fact)
    second = handler(fact)

    assert first == second
    assert first == (
        CreateWmsConfirmation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            operation="inbound.material.admission_decide@v1",
            operation_id=fact.request_operation_id,
            evidence_refs=(fact.evidence_id,),
            snapshot_refs=(f"execution:{EXECUTION_ID}", f"epoch:{EPOCH_ID}"),
        ),
    )


@pytest.mark.parametrize("field", ["lot_code", "diameter_mm", "thickness_mm"])
def test_material_evidence_rejects_incomplete_or_invalid_scan(field: str) -> None:
    with pytest.raises(ValueError):
        _material_fact(**{field: " "})


def test_shape_failure_remains_measurement_fact_and_still_requests_wms_admission() -> None:
    readers = _readers(("measurement", "MEASUREMENT_POSITION", TRACE_ID, False))
    decision = MaterialEvidenceReadyHandler(*readers)(_material_fact(shape_result=ShapeResult.FAIL))

    assert isinstance(decision[0], CreateWmsConfirmation)


def test_plugin_accepts_nonblank_stable_operation_identity_without_revalidating_wms_wire() -> None:
    fact = _material_fact(request_operation_id="stable-admission-operation-1")

    assert fact.request_operation_id == "stable-admission-operation-1"


def test_admission_accept_creates_only_measurement_pick_and_put() -> None:
    source = _position("measurement", "MEASUREMENT_POSITION")
    inlet = _position("pipeline-inlet", "PIPELINE_INLET")
    readers = _readers(
        (source.location_id, source.location_type, TRACE_ID, False),
        (inlet.location_id, inlet.location_type, None, True),
    )
    fact = _admission_fact(
        AdmissionResult.ACCEPT,
        source_position=source,
        next_position=inlet,
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
    )

    assert AdmissionDecidedHandler(*readers)(fact) == (
        CreateDeviceCommand(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            device_role="MEASUREMENT_DEVICE",
            task_type="PICK_AND_PUT",
            material_trace_id=TRACE_ID,
            source=source,
            target=inlet,
        ),
    )


def test_admission_wait_does_not_create_a_device_command() -> None:
    readers = _readers(("measurement", "MEASUREMENT_POSITION", TRACE_ID, False))
    fact = _admission_fact(AdmissionResult.WAIT, reason_code="WMS_RETRY_LATER")

    assert AdmissionDecidedHandler(*readers)(fact) == (
        Wait(material_execution_id=EXECUTION_ID, fact_id=fact.fact_id, reason_code="WMS_RETRY_LATER"),
    )


def test_admission_reject_uses_measurement_device_directly_to_wms_ng() -> None:
    source = _position("measurement", "MEASUREMENT_POSITION")
    ng = _position("ng-1", "NG_POSITION")
    readers = _readers(
        (source.location_id, source.location_type, TRACE_ID, False),
        (ng.location_id, ng.location_type, None, True),
    )
    fact = _admission_fact(
        AdmissionResult.REJECT,
        source_position=source,
        next_position=ng,
        reason_code="GRN_REJECTED",
    )

    decisions = AdmissionDecidedHandler(*readers)(fact)

    assert decisions == (
        CreateDeviceCommand(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            device_role="MEASUREMENT_DEVICE",
            task_type="PICK_AND_PUT",
            material_trace_id=TRACE_ID,
            source=source,
            target=ng,
        ),
    )


def test_admission_does_not_dispatch_when_device_is_not_ready() -> None:
    readers = _readers(("measurement", "MEASUREMENT_POSITION", TRACE_ID, False))
    fact = _admission_fact(
        AdmissionResult.ACCEPT,
        pkg_id="pkg-1",
        inbound_admission_id="admission-1",
        next_position=_position("pipeline-inlet", "PIPELINE_INLET"),
        device_ready=False,
    )

    assert AdmissionDecidedHandler(*readers)(fact) == (
        DeferExecution(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="MEASUREMENT_DEVICE_NOT_READY",
        ),
    )


def test_wms_admission_conflict_pauses_only_current_execution_and_position() -> None:
    source = _position("measurement", "MEASUREMENT_POSITION")
    readers = _readers((source.location_id, source.location_type, TRACE_ID, False))
    fact = _admission_fact(AdmissionResult.RECONCILING, reason_code="WMS_ADMISSION_CONFLICT")

    assert AdmissionDecidedHandler(*readers)(fact) == (
        PauseForReconciliation(
            material_execution_id=EXECUTION_ID,
            fact_id=fact.fact_id,
            reason_code="WMS_ADMISSION_CONFLICT",
            affected_resource_ids=(source.location_id,),
        ),
    )
