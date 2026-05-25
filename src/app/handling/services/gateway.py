"""Handling 外部系统协议网关。"""

from __future__ import annotations

from typing import Any

_FULL_BOX_EXCHANGE_OPERATION_MARKERS = ("FULL_BOX_EXCHANGE", "FULL_BIN_EXCHANGE", "RACK_BIN_EXCHANGE")


class WmsRcsHandlingGateway:
    """将内部 Handling move 转换为 WMS/RCS 请求包络。"""

    def build_ctu_move_envelope(self, *, operation: Any, move: Any, sequence_no: int) -> dict[str, Any]:
        dispatch_key = f"handling:{operation.operation_key}:move:{sequence_no}"
        operation_type = str(getattr(operation, "operation_type", "") or "")
        target_code = _target_code(operation_type)
        metadata = _metadata_dict(move)
        request_type = _request_type(operation_type)
        return {
            "dispatch_key": dispatch_key,
            "target_code": target_code,
            "payload_json": _drop_none(
                {
                    "request_id": dispatch_key,
                    "dispatch_key": dispatch_key,
                    "exchange_request_code": dispatch_key,
                    "callback_type": _callback_type(operation_type),
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


def _target_code(operation_type: str) -> str:
    return "WMS_RCS_FULL_BOX_EXCHANGE" if _is_full_box_exchange(operation_type) else "WMS_RCS_BIN_OPERATION"


def _request_type(operation_type: str) -> str:
    return "FULL_BIN_EXCHANGE" if _is_full_box_exchange(operation_type) else "BIN_MOVE"


def _callback_type(operation_type: str) -> str:
    return "WMS_FULL_BOX_EXCHANGE_RESULT" if _is_full_box_exchange(operation_type) else "WMS_TRANSPORT_COMPLETED"


def _is_full_box_exchange(operation_type: str) -> bool:
    normalized = operation_type.upper()
    return any(marker in normalized for marker in _FULL_BOX_EXCHANGE_OPERATION_MARKERS)


def _metadata_dict(move: Any) -> dict[str, Any]:
    metadata = getattr(move, "metadata_json", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


wms_rcs_handling_gateway = WmsRcsHandlingGateway()


__all__ = ["WmsRcsHandlingGateway", "wms_rcs_handling_gateway"]
