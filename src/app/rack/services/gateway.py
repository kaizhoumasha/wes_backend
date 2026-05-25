"""WMS/RCS 货架操作 Gateway。

Rack 领域只暴露内部 task 语义，第三方 payload 和逻辑端点编码统一在这里收口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.rack.models.operation import RackTaskType
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_RACK_OPERATION_ENDPOINT = "WMS_RCS_RACK_OPERATION"


class WmsRcsRackGateway:
    """构造货架操作下发包络。"""

    def build_task_envelope(
        self,
        *,
        operation_key: str,
        operation_type: str,
        sequence_no: int,
        task_type: str,
        trace_id: str,
        workline_id: int | None,
        workline_code: str | None,
        material_session_id: int | None,
        rack_code: str | None,
        rack_kind: str | None,
        source_position_code: str | None,
        target_position_code: str | None,
        target_position_role: str | None,
        actions_json: Mapping[str, Any] | None = None,
        request_json: Mapping[str, Any] | None = None,
        target_code: str | None = None,
    ) -> DispatchEnvelope:
        normalized_task_type = _rack_task_type(task_type)
        dispatch_key = f"rack-operation:{operation_key}:{sequence_no}:{normalized_task_type}"
        actions = dict(actions_json or {})
        actions.setdefault("action", normalized_task_type)

        payload = {
            **dict(request_json or {}),
            "request_id": dispatch_key,
            "dispatch_key": dispatch_key,
            "callback_type": _callback_type(normalized_task_type),
            "operation_key": operation_key,
            "operation_type": operation_type,
            "sequence_no": sequence_no,
            "task_type": normalized_task_type,
            "workline_code": workline_code,
            "rack_code": rack_code,
            "rack_kind": rack_kind,
            "source_position_code": source_position_code,
            "target_position_code": target_position_code,
            "target_position_role": target_position_role,
            "source": {"position_code": source_position_code},
            "target": {
                "position_code": target_position_code,
                "position_role": target_position_role,
            },
            "trace_id": trace_id,
            "actions": actions,
        }
        return DispatchEnvelope(
            dispatch_key=dispatch_key,
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=target_code or DEFAULT_RACK_OPERATION_ENDPOINT,
            payload_json=payload,
            operation_domain="RACK",
            operation_key=operation_key,
            workline_id=workline_id,
            session_id=material_session_id,
            trace_id=trace_id,
        )


def _rack_task_type(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in {item.value for item in RackTaskType}:
        raise ValueError(f"不支持的 rack task 类型: {value}")
    return normalized


def _callback_type(task_type: str) -> str:
    if task_type == RackTaskType.MOVE_RACK.value:
        return "WMS_RACK_MOVED"
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return "WMS_RACK_ARRIVED"
    if task_type == RackTaskType.TURN_RACK_SIDE.value:
        return "WMS_RACK_TURNED"
    return "WMS_RACK_TASK_RESULT"


wms_rcs_rack_gateway = WmsRcsRackGateway()


__all__ = ["DEFAULT_RACK_OPERATION_ENDPOINT", "WmsRcsRackGateway", "wms_rcs_rack_gateway"]
