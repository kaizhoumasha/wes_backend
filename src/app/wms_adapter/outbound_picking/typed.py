"""prepare 纯 typed intent/outcome 与宿主 wire 的静态边界。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import wire


def encode_request(intent: sdk.PickingTaskPrepareIntent, *, timestamp: int) -> dict[str, Any]:
    """固定 prepare 请求；调用者负责可靠事务、时钟和发送。"""
    if type(intent) is not sdk.PickingTaskPrepareIntent:
        raise TypeError("prepare requires PickingTaskPrepareIntent")
    request = wire.parse_picking_task_prepare_request(
        {
            "operation": wire.PICKING_TASK_PREPARE_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {"task_id": intent.task_id, "workline_code": intent.work_line_code},
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.PickingTaskPrepareOutcome:
    """从已持久化响应构造结果；实际 HTTP status 仍由 Adapter 接收时校验。"""
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("prepare response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"PREPARE_ACCEPTED": 202, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported prepare response code")
    response = wire.parse_picking_task_prepare_response(status, payload)
    if isinstance(response, wire.PickingTaskPrepareAcceptedResponse):
        return sdk.PickingTaskPrepareOutcome(sdk.PrepareAccepted())
    if isinstance(response, wire.PickingTaskPrepareUnavailableResponse):
        return sdk.PickingTaskPrepareOutcome(sdk.OperationUnavailable())
    if isinstance(response, wire.PickingTaskPrepareConflictResponse):
        return sdk.PickingTaskPrepareOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.PickingTaskPrepareOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
