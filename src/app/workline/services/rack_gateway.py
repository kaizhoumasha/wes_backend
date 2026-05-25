"""货架操作外部系统协议网关。"""

from __future__ import annotations

import os
from typing import Any

from src.app.workline.models.rack_task import WorklineRackTaskType

_DEFAULT_WMS_RCS_RACK_OPERATION_URL = "http://wms-rcs/api/wes/rack-operation"


class WmsRcsRackGateway:
    """将内部 Rack task 语义转换为 WMS/RCS 请求包络。"""

    def build_rack_task_envelope(
        self,
        *,
        operation_key: str,
        operation_type: str,
        workline_code: str,
        trace_id: str,
        target_code: str | None,
        spec: Any,
    ) -> dict[str, Any]:
        task_type = str(spec.task_type)
        dispatch_key = f"rack-operation:{operation_key}:{spec.sequence_no}:{task_type}"
        actions_json = {
            "action": task_type,
            "required": bool(spec.required),
        }
        request_json = _drop_none(
            {
                "request_id": dispatch_key,
                "dispatch_key": dispatch_key,
                "callback_type": _callback_type(task_type),
                "operation_key": operation_key,
                "operation_type": operation_type,
                "sequence_no": spec.sequence_no,
                "task_type": task_type,
                "workline_code": workline_code,
                "rack_code": spec.rack_code,
                "rack_kind": spec.rack_kind,
                "source_position_code": spec.source_position_code,
                "target_position_code": spec.target_position_code,
                "target_position_role": spec.target_position_role,
                "trace_id": trace_id,
                "actions": actions_json,
            }
        )
        return {
            "dispatch_key": dispatch_key,
            "target_code": _target_code(target_code),
            "request_json": request_json,
            "actions_json": actions_json,
            "payload_json": request_json,
        }


def _target_code(target_code: str | None) -> str:
    if target_code is not None and target_code.strip():
        return target_code.strip()
    for env_name in ("WMS_RCS_RACK_OPERATION_URL", "WMS_RCS_TRANSPORT_REQUEST_URL", "WMS_RCS_TARGET_CODE"):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
    return _DEFAULT_WMS_RCS_RACK_OPERATION_URL


def _callback_type(task_type: str) -> str:
    if task_type == WorklineRackTaskType.MOVE_RACK.value:
        return "WMS_RACK_MOVED"
    if task_type == WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return "WMS_RACK_ARRIVED"
    if task_type == WorklineRackTaskType.TURN_RACK_SIDE.value:
        return "WMS_RACK_TURNED"
    return "WMS_RACK_TASK_RESULT"


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


wms_rcs_rack_gateway = WmsRcsRackGateway()


__all__ = ["WmsRcsRackGateway", "wms_rcs_rack_gateway"]
