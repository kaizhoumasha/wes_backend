from __future__ import annotations

import pytest
import wes_plugin_sdk as sdk


def test_manual_bin_facade_constructs_closed_immutable_intents() -> None:
    admission = sdk.wms_operations.outbound_manual_bin_work_admission(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        task_id="PICK-001",
        bin_code="A000000001",
        scanned_at=1_788_389_999_000,
    )

    assert admission.task_id == "PICK-001"  # nosec B101 - pytest assertion
    assert isinstance(admission, sdk.ManualBinAdmissionIntent)  # nosec B101 - pytest assertion


def test_manual_bin_outcomes_are_closed_without_wire_payloads() -> None:
    decided = sdk.ManualBinAdmissionOutcome(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        result="WORK_REQUIRED",
        task_id="PICK-001",
    )
    completed = sdk.ManualBinCompletedFact(
        task_id="PICK-001", bin_code="A000000001", result="NORMAL", completed_at=1_788_389_999_000
    )

    assert decided.task_id == completed.task_id  # nosec B101 - pytest assertion
    assert not hasattr(decided, "payload")  # nosec B101 - pytest assertion
    with pytest.raises(ValueError):
        sdk.ManualBinAdmissionOutcome(operation_id=decided.operation_id, result="WAIT")
