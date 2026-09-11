"""人工 Bin typed intent 到宿主 wire 的静态边界。"""

from __future__ import annotations

from typing import Any

import wes_plugin_sdk as sdk

from .manual_bin_admission_wire import MANUAL_BIN_ADMISSION_OPERATION, parse_manual_bin_admission_request


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


__all__ = ["encode_admission"]
