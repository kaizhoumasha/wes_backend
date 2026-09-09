"""人工 Bin typed intent 到宿主 wire 的静态边界。"""

from __future__ import annotations

from typing import Any

import wes_plugin_sdk as sdk

from .manual_bin_admission_wire import MANUAL_BIN_ADMISSION_OPERATION, parse_manual_bin_admission_request
from .manual_bin_apply_report_wire import MANUAL_BIN_APPLY_REPORT_OPERATION, parse_manual_bin_apply_report_request


def encode_admission(intent: sdk.ManualBinAdmissionIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.ManualBinAdmissionIntent:
        raise TypeError("manual bin admission requires ManualBinAdmissionIntent")
    return parse_manual_bin_admission_request(
        {
            "operation_id": intent.operation_id,
            "operation": MANUAL_BIN_ADMISSION_OPERATION,
            "timestamp": timestamp,
            "data": {"bin_code": intent.bin_code, "scanned_at": intent.scanned_at},
        }
    ).model_dump(mode="json")


def encode_apply_report(intent: sdk.ManualBinApplyReportIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.ManualBinApplyReportIntent:
        raise TypeError("manual bin apply report requires ManualBinApplyReportIntent")
    data: dict[str, Any] = {
        "completion_operation_id": intent.completion_operation_id,
        "task_id": intent.task_id,
        "bin_code": intent.bin_code,
        "apply_revision": intent.apply_revision,
        "apply_result": intent.apply_result,
        "occurred_at": intent.occurred_at,
    }
    if intent.reason_code is not None:
        data["reason_code"] = intent.reason_code
    return parse_manual_bin_apply_report_request(
        {
            "operation_id": intent.operation_id,
            "operation": MANUAL_BIN_APPLY_REPORT_OPERATION,
            "timestamp": timestamp,
            "data": data,
        }
    ).model_dump(mode="json")


__all__ = ["encode_admission", "encode_apply_report"]
