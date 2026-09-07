"""Bin 工作计划的纯 SDK 与持久化 wire 边界。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import work_plan_wire as wire


def encode_request(intent: sdk.BinWorkPlanIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.BinWorkPlanIntent:
        raise TypeError("work plan requires BinWorkPlanIntent")
    request = wire.parse_bin_work_plan_request(
        {
            "operation": wire.BIN_WORK_PLAN_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {"task_id": intent.task_id, "bin_code": intent.bin_code, "scanned_at": intent.scanned_at},
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.BinWorkPlanOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("work plan response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported work plan response code")
    response = wire.parse_bin_work_plan_response(status, payload)
    if isinstance(response, wire.BinWorkPlanDecidedResponse):
        if isinstance(response.data, wire.BinWorkPlanReady):
            return sdk.BinWorkPlanOutcome(sdk.BinWorkPlanReady(tuple(response.data.cell_ids)))
        if isinstance(response.data, wire.BinWorkPlanWait):
            return sdk.BinWorkPlanOutcome(sdk.BinWorkPlanWait(response.data.retry_after_ms))
        return sdk.BinWorkPlanOutcome(sdk.BinWorkPlanNoWork())
    if response.code == "UNAVAILABLE":
        return sdk.BinWorkPlanOutcome(sdk.OperationUnavailable())
    if response.code == "CONFLICT":
        return sdk.BinWorkPlanOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.BinWorkPlanOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
