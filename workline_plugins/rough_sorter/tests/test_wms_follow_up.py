from __future__ import annotations

from datetime import datetime, timedelta

from src.app.execution.models import WmsConfirmation

from rough_sorter.application.wms_follow_up import RoughSorterWmsFollowUpPlanner


def test_wait_follow_up_uses_new_identity_and_preserves_canonical_request() -> None:
    received_at = datetime(2026, 8, 18, 9, 0, 1)
    original_operation_id = "019d0000-0000-7000-8000-000000000031"
    follow_up_operation_id = "019d0000-0001-7000-8000-000000000032"
    confirmation = WmsConfirmation(
        operation="inbound.material.admission_decide@v1",
        operation_id=original_operation_id,
        material_execution_id=21,
        request_digest="a" * 64,
        request_payload={
            "operation": "inbound.material.admission_decide@v1",
            "operation_id": original_operation_id,
            "timestamp": 1_787_040_000_000,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "six_in_one": {
                    "LotCode": "L",
                    "DateCode": "D",
                    "Qty": "1",
                    "ProductNo": "P",
                    "MfrPN": "M",
                    "PONumber": "PO",
                },
                "measurements": {"diameter_mm": "1", "thickness_mm": "1"},
                "shape_result": "PASS",
                "line_run_epoch_id": "11",
                "workline_code": "ROUGH-LINE-1",
                "source_position": {"type": "HANDOFF_POSITION", "location_code": "MEASUREMENT-1"},
            },
        },
        deadline_at=received_at + timedelta(minutes=1),
    )
    planner = RoughSorterWmsFollowUpPlanner(operation_id_factory=lambda: follow_up_operation_id)

    follow_up = planner.plan(
        confirmation,
        response_result="WAIT",
        retry_after_ms=60_000,
        received_at=received_at,
    )

    assert follow_up is not None
    assert follow_up.operation == confirmation.operation
    assert follow_up.operation_id == follow_up_operation_id
    assert follow_up.request_payload == {
        **confirmation.request_payload,
        "operation_id": follow_up_operation_id,
        "timestamp": 1_787_043_601_000,
    }
    assert follow_up.next_attempt_at == received_at + timedelta(seconds=60)


def test_non_wait_result_does_not_create_follow_up() -> None:
    confirmation = WmsConfirmation(
        operation="inbound.material.admission_decide@v1",
        operation_id="019d0000-0000-7000-8000-000000000031",
        material_execution_id=21,
        request_digest="a" * 64,
        request_payload={},
        deadline_at=datetime(2026, 8, 18, 9, 1),
    )

    assert (
        RoughSorterWmsFollowUpPlanner().plan(
            confirmation,
            response_result="ACCEPT",
            retry_after_ms=250,
            received_at=datetime(2026, 8, 18, 9, 0),
        )
        is None
    )
