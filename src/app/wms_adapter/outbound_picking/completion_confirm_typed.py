"""任务完成确认的纯 SDK 与持久化 wire 转换。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import completion_confirm_wire as wire


def encode_request(intent: sdk.CompletionConfirmIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.CompletionConfirmIntent:
        raise TypeError("completion confirm requires CompletionConfirmIntent")
    request = wire.parse_completion_confirm_request(
        {
            "operation": wire.COMPLETION_CONFIRM_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "last_applied_plan_revision": intent.last_applied_plan_revision,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.CompletionConfirmOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("completion confirm response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported completion confirm response code")
    response = wire.parse_completion_confirm_response(status, payload)
    if isinstance(response, wire.CompletionConfirmDecidedResponse):
        if isinstance(response.data, wire.PickingTaskCompleted):
            return sdk.CompletionConfirmOutcome(sdk.PickingTaskCompleted())
        if isinstance(response.data, wire.PickingTaskPlanRevisionStale):
            return sdk.CompletionConfirmOutcome(sdk.PickingTaskPlanRevisionStale(response.data.current_plan_revision))
        return sdk.CompletionConfirmOutcome(sdk.PickingTaskBusinessInProgress(response.data.retry_after_ms))
    if response.code == "UNAVAILABLE":
        return sdk.CompletionConfirmOutcome(sdk.OperationUnavailable())
    if response.code == "CONFLICT":
        return sdk.CompletionConfirmOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.CompletionConfirmOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
