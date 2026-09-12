from __future__ import annotations

import wes_plugin_sdk as sdk


def test_manual_bin_facade_constructs_closed_immutable_intents() -> None:
    admission = sdk.wms_operations.outbound_manual_bin_work_admission(
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        task_id="PICK-001",
        bin_code="BIN-001",
        scanned_at=1_788_389_999_000,
    )

    assert admission.task_id == "PICK-001"  # nosec B101 - pytest assertion
    assert isinstance(admission, sdk.ManualBinAdmissionIntent)  # nosec B101 - pytest assertion
