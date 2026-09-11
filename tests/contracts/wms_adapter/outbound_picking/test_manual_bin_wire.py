from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import (
    ManualBinAdmissionDecidedResponse,
    ManualBinWorkRequired,
    parse_manual_bin_admission_request,
    parse_manual_bin_admission_response,
)
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import (
    parse_manual_bin_completed_event,
)

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


def test_admission_request_binds_task_to_actual_bin_and_scan_time() -> None:
    request = parse_manual_bin_admission_request(
        {
            "operation_id": OPERATION_ID,
            "operation": "outbound.manual_bin.work_admission_decide@v1",
            "timestamp": 1_788_389_900_000,
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "scanned_at": 1_788_389_899_900,
                "expected_bin": "BIN-002",
            },
        }
    )

    assert request.data.model_dump() == {"task_id": "PICK-001", "bin_code": "BIN-001", "scanned_at": 1_788_389_899_900}


@pytest.mark.parametrize(
    "data",
    [
        {"task_id": "PICK-001", "bin_code": "BIN-001", "scanned_at": 1_788_389_900_001},
        {"task_id": "PICK-001", "bin_code": "", "scanned_at": 1_788_389_899_900},
    ],
)
def test_admission_request_rejects_contract_drift(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_manual_bin_admission_request(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_admission_decide@v1",
                "timestamp": 1_788_389_900_000,
                "data": data,
            }
        )


@pytest.mark.parametrize(
    "parser,payload",
    [
        (
            parse_manual_bin_admission_request,
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_admission_decide@v1",
                "timestamp": 1,
                "data": {"task_id": "PICK-001", "bin_code": "BIN-001", "scanned_at": 0},
            },
        ),
        (
            parse_manual_bin_completed_event,
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_completed@v1",
                "timestamp": 1,
                "data": {"task_id": "PICK-001", "bin_code": "BIN-001", "result": "NORMAL", "completed_at": 0},
            },
        ),
    ],
)
def test_manual_bin_contract_rejects_zero_business_timestamps(parser, payload: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        parser(payload)


@pytest.mark.parametrize(
    ("data", "result"),
    [
        ({"result": "WORK_REQUIRED", "task_id": "PICK-001"}, "WORK_REQUIRED"),
        ({"result": "NO_WORK", "task_id": "PICK-001"}, "NO_WORK"),
        ({"result": "WAIT", "retry_after_ms": 1000, "task_id": "PICK-001"}, "WAIT"),
    ],
)
def test_admission_response_is_a_strict_union(data: dict[str, object], result: str) -> None:
    response = parse_manual_bin_admission_response(
        200,
        {"operation_id": OPERATION_ID, "code": "DECIDED", "timestamp": 1_788_389_900_010, "data": data},
    )

    assert isinstance(response, ManualBinAdmissionDecidedResponse)
    assert response.data.result == result
    if result != "WORK_REQUIRED":
        assert "task_id" not in response.data.model_dump()


@pytest.mark.parametrize(
    "data",
    [
        {"result": "WORK_REQUIRED"},
    ],
)
def test_admission_response_rejects_missing_required_fields(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_manual_bin_admission_response(
            200,
            {"operation_id": OPERATION_ID, "code": "DECIDED", "timestamp": 1_788_389_900_010, "data": data},
        )


@pytest.mark.parametrize("result", ["NORMAL", "NG"])
def test_work_completed_accepts_final_bin_decision(result: str) -> None:
    event = parse_manual_bin_completed_event(
        {
            "operation_id": OPERATION_ID,
            "operation": "outbound.manual_bin.work_completed@v1",
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": result,
                "completed_at": 1_788_389_999_000,
            },
        }
    )

    assert event.data.result == result


def test_work_completed_rejects_future_completion_time() -> None:
    with pytest.raises(ValidationError):
        parse_manual_bin_completed_event(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_completed@v1",
                "timestamp": 1_788_390_000_000,
                "data": {
                    "task_id": "PICK-001",
                    "bin_code": "BIN-001",
                    "result": "NORMAL",
                    "completed_at": 1_788_390_000_001,
                },
            }
        )


@pytest.mark.parametrize("task_id", [None, "", "bad task", 123])
def test_admission_rejects_invalid_task_id(task_id: object) -> None:
    with pytest.raises(ValidationError):
        parse_manual_bin_admission_request(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_admission_decide@v1",
                "timestamp": 100,
                "data": {"task_id": task_id, "bin_code": "BIN-001", "scanned_at": 99},
            }
        )


def test_admission_requires_task_id() -> None:
    with pytest.raises(ValidationError):
        parse_manual_bin_admission_request(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_bin.work_admission_decide@v1",
                "timestamp": 100,
                "data": {"bin_code": "BIN-001", "scanned_at": 99},
            }
        )


@pytest.mark.parametrize("task_id", ["PICK-001", "PICK-OTHER"])
def test_admission_response_matches_requested_task(task_id: str) -> None:
    request = parse_manual_bin_admission_request(
        {
            "operation_id": OPERATION_ID,
            "operation": "outbound.manual_bin.work_admission_decide@v1",
            "timestamp": 100,
            "data": {"task_id": "PICK-001", "bin_code": "BIN-001", "scanned_at": 99},
        }
    )
    response = {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 101,
        "data": {"result": "WORK_REQUIRED", "task_id": task_id},
    }
    if task_id == "PICK-001":
        parsed = parse_manual_bin_admission_response(200, response, request=request)
        assert isinstance(parsed, ManualBinAdmissionDecidedResponse)
        assert isinstance(parsed.data, ManualBinWorkRequired)
        assert parsed.data.task_id == task_id
    else:
        with pytest.raises(ValueError, match="task_id"):
            parse_manual_bin_admission_response(200, response, request=request)
