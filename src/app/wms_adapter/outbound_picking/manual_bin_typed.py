"""人工 Bin typed intent 到宿主 wire 的静态边界。"""

from __future__ import annotations

from typing import Any

import wes_plugin_sdk as sdk

from .manual_bin_admission_wire import (
    MANUAL_BIN_ADMISSION_OPERATION,
    ManualBinAdmissionDecidedResponse,
    ManualBinWait,
    ManualBinWorkRequired,
    parse_manual_bin_admission_request,
)
from .manual_bin_completed_wire import ManualBinCompletedEvent


def encode_admission(intent: sdk.ManualBinAdmissionIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.ManualBinAdmissionIntent:
        raise TypeError("manual bin admission requires ManualBinAdmissionIntent")
    return parse_manual_bin_admission_request(
        {
            "operation_id": intent.operation_id,
            "operation": MANUAL_BIN_ADMISSION_OPERATION,
            "timestamp": timestamp,
            "data": {"task_id": intent.task_id, "bin_code": intent.bin_code, "scanned_at": intent.scanned_at},
        }
    ).model_dump(mode="json")


def decode_admission_outcome(payload: object) -> sdk.ManualBinAdmissionOutcome:
    response = ManualBinAdmissionDecidedResponse.model_validate(payload)
    data = response.data
    if isinstance(data, ManualBinWorkRequired):
        return sdk.ManualBinAdmissionOutcome(
            operation_id=response.operation_id, result="WORK_REQUIRED", task_id=data.task_id
        )
    if isinstance(data, ManualBinWait):
        return sdk.ManualBinAdmissionOutcome(
            operation_id=response.operation_id, result="WAIT", retry_after_ms=data.retry_after_ms
        )
    return sdk.ManualBinAdmissionOutcome(operation_id=response.operation_id, result="NO_WORK")


def decode_completed_fact(payload: object) -> sdk.ManualBinCompletedFact:
    event = ManualBinCompletedEvent.model_validate(payload)
    return sdk.ManualBinCompletedFact(
        task_id=event.data.task_id,
        bin_code=event.data.bin_code,
        result=event.data.result,
        completed_at=event.data.completed_at,
    )


__all__ = ["decode_admission_outcome", "decode_completed_fact", "encode_admission"]
