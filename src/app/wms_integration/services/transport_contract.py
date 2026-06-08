"""WMS/RCS 运输类下发合同。

该服务只收口 WES 内部对象到 WMS/RCS 下发包络的转换，不负责 outbox 派发。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType

DEFAULT_RACK_OPERATION_ENDPOINT = "WMS_RCS_RACK_OPERATION"
BIN_OPERATION_ENDPOINT = "WMS_RCS_BIN_OPERATION"
FULL_BOX_EXCHANGE_ENDPOINT = "WMS_RCS_FULL_BOX_EXCHANGE"
SINGLE_LAYER_RACK_OPERATION_AUTHORITY_SYSTEM = "WMS"
SINGLE_LAYER_RACK_KIND = "SINGLE_LAYER"

_RACK_TASK_TYPES = frozenset({"MOVE_RACK", "ALLOCATE_AND_MOVE_RACK", "TURN_RACK_SIDE"})
_RACK_ARRIVED_TASK_TYPE = "ALLOCATE_AND_MOVE_RACK"
_FORBIDDEN_DIRECT_DEVICE_FIELDS = frozenset(
    {
        "rcs_url",
        "rcs_path",
        "agv_id",
        "ctu_id",
        "vehicle_id",
        "physical_coordinate",
    }
)


class WmsTransportContractService:
    """构造 WMS/RCS 运输类请求合同。"""

    def build_single_layer_rack_operation_request(
        self,
        *,
        business_demand_key: str,
        workline_code: str,
        endpoint_code: str,
        rack_kind: str,
        operation_type: str,
        payload: Mapping[str, Any],
        timeout_seconds: int,
        rack_code: str | None = None,
        rack_snapshot_ref: str | None = None,
        dispatch_key: str | None = None,
        target_code: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_dispatch_key = _single_layer_rack_dispatch_key(
            dispatch_key=dispatch_key,
            business_demand_key=business_demand_key,
            workline_code=workline_code,
            endpoint_code=endpoint_code,
        )
        resolved_payload = deepcopy(dict(payload))
        _reject_direct_device_fields(resolved_payload)
        normalized_rack_kind = _single_layer_rack_kind(rack_kind)
        resolved_payload.update(
            {
                "dispatch_key": resolved_dispatch_key,
                "operation_key": resolved_dispatch_key,
                "business_demand_key": business_demand_key,
                "workline_code": workline_code,
                "endpoint_code": endpoint_code,
                "rack_kind": normalized_rack_kind,
                "authority_system": SINGLE_LAYER_RACK_OPERATION_AUTHORITY_SYSTEM,
            }
        )
        if rack_code is not None:
            resolved_payload["rack_code"] = rack_code
        if rack_snapshot_ref is not None:
            resolved_payload["rack_snapshot_ref"] = rack_snapshot_ref
        if trace_id is not None:
            resolved_payload["trace_id"] = trace_id
        _attach_single_layer_request_json(resolved_payload)

        return {
            "operation_type": _required_text(operation_type, "operation_type"),
            "operation_key": resolved_dispatch_key,
            "target_code": _logical_target_code(target_code),
            "payload": resolved_payload,
            "timeout_seconds": timeout_seconds,
        }

    def build_rack_task_envelope(
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
            "callback_type": _rack_callback_type(normalized_task_type),
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

    def build_handling_ctu_move_envelope(
        self,
        *,
        operation: Any,
        move: Any,
        sequence_no: int,
        is_full_box_exchange: bool,
    ) -> dict[str, Any]:
        dispatch_key = f"handling:{operation.operation_key}:move:{sequence_no}"
        operation_type = str(getattr(operation, "operation_type", "") or "")
        target_code = _handling_target_code(is_full_box_exchange)
        metadata = _metadata_dict(move)
        request_type = _handling_request_type(is_full_box_exchange)
        return {
            "dispatch_key": dispatch_key,
            "target_code": target_code,
            "payload_json": _drop_none(
                {
                    "request_id": dispatch_key,
                    "dispatch_key": dispatch_key,
                    "exchange_request_code": dispatch_key,
                    "callback_type": _handling_callback_type(is_full_box_exchange),
                    "request_type": request_type,
                    "operation_key": operation.operation_key,
                    "operation_type": operation_type,
                    "sequence_no": sequence_no,
                    "trace_id": getattr(operation, "trace_id", None),
                    "workline_code": getattr(operation, "workline_code", None),
                    "material_session_id": getattr(operation, "material_session_id", None),
                    "object_type": move.object_type,
                    "bin_code": getattr(move, "bin_code", None),
                    "placeholder_key": getattr(move, "placeholder_key", None),
                    "candidate_authorized_bin_ids": getattr(move, "candidate_authorized_bin_ids", None),
                    "rack_id": getattr(move, "rack_code", None) or metadata.get("rack_id"),
                    "rack_type": metadata.get("rack_type"),
                    "rack_slot_code": getattr(move, "rack_slot_code", None) or metadata.get("rack_slot_code"),
                    "from_location": move.source_code,
                    "to_location": move.target_code,
                    "priority": metadata.get("priority"),
                    "target_code": target_code,
                    "source": {
                        "type": move.source_type,
                        "code": move.source_code,
                    },
                    "target": {
                        "type": move.target_type,
                        "code": move.target_code,
                    },
                    "carrier": {
                        "type": getattr(move, "carrier_type", None),
                        "code": getattr(move, "carrier_code", None),
                    },
                }
            ),
        }


def _rack_task_type(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in _RACK_TASK_TYPES:
        raise ValueError(f"不支持的 rack task 类型: {value}")
    return normalized


def _rack_callback_type(task_type: str) -> str:
    if task_type == _RACK_ARRIVED_TASK_TYPE:
        return "WMS_RACK_ARRIVED"
    return "WMS_RACK_TASK_RESULT"


def _handling_target_code(is_full_box_exchange: bool) -> str:
    return FULL_BOX_EXCHANGE_ENDPOINT if is_full_box_exchange else BIN_OPERATION_ENDPOINT


def _handling_request_type(is_full_box_exchange: bool) -> str:
    return "FULL_BIN_EXCHANGE" if is_full_box_exchange else "BIN_MOVE"


def _handling_callback_type(is_full_box_exchange: bool) -> str:
    return "WMS_FULL_BOX_EXCHANGE_RESULT" if is_full_box_exchange else "WMS_TRANSPORT_COMPLETED"


def _metadata_dict(move: Any) -> dict[str, Any]:
    metadata = getattr(move, "metadata_json", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _single_layer_rack_dispatch_key(
    *,
    dispatch_key: str | None,
    business_demand_key: str,
    workline_code: str,
    endpoint_code: str,
) -> str:
    if dispatch_key is not None:
        return _required_text(dispatch_key, "dispatch_key")
    return "wms-rack-operation:{business_demand_key}:{workline_code}:{endpoint_code}".format(
        business_demand_key=_required_text(business_demand_key, "business_demand_key"),
        workline_code=_required_text(workline_code, "workline_code"),
        endpoint_code=_required_text(endpoint_code, "endpoint_code"),
    )


def _required_text(value: str | None, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"single-layer rack operation requires {field_name}")
    return normalized


def _single_layer_rack_kind(value: str) -> str:
    rack_kind = _required_text(value, "rack_kind")
    if rack_kind != SINGLE_LAYER_RACK_KIND:
        raise ValueError("single-layer rack operation rack_kind must be SINGLE_LAYER")
    return rack_kind


def _logical_target_code(value: str | None) -> str:
    target_code = str(value or DEFAULT_RACK_OPERATION_ENDPOINT).strip()
    if not target_code:
        raise ValueError("single-layer rack operation requires target_code")
    if target_code.lower().startswith(("http://", "https://")):
        raise ValueError("single-layer rack operation target_code must be a logical endpoint code, not a URL")
    return target_code


def _reject_direct_device_fields(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            text_key = str(key)
            if text_key in _FORBIDDEN_DIRECT_DEVICE_FIELDS:
                raise ValueError(
                    f"single-layer rack operation payload contains forbidden direct-device field {text_key}"
                )
            _reject_direct_device_fields(item, path=f"{path}.{text_key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_direct_device_fields(item, path=f"{path}[{index}]")


def _attach_single_layer_request_json(payload: dict[str, Any]) -> None:
    rack_tasks = payload.get("rack_tasks")
    if not isinstance(rack_tasks, list):
        return
    task_request_json = deepcopy({key: value for key, value in payload.items() if key != "rack_tasks"})
    for task in rack_tasks:
        if not isinstance(task, dict):
            continue
        existing_request = task.get("request_json")
        request_json = dict(existing_request) if isinstance(existing_request, Mapping) else {}
        request_json = {**request_json, **deepcopy(task_request_json)}
        task["request_json"] = request_json


wms_transport_contract_service = WmsTransportContractService()


__all__ = [
    "BIN_OPERATION_ENDPOINT",
    "DEFAULT_RACK_OPERATION_ENDPOINT",
    "FULL_BOX_EXCHANGE_ENDPOINT",
    "SINGLE_LAYER_RACK_OPERATION_AUTHORITY_SYSTEM",
    "WmsTransportContractService",
    "wms_transport_contract_service",
]
