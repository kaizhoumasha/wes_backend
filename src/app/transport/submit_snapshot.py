"""Transport submit 持久化快照的唯一规范化边界。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from src.app.transport.contracts import (
    ExchangeBinsRequest,
    MoveBinsRequest,
    MoveRackRequest,
    RotateRackRequest,
    TransportContractError,
    TransportRequest,
)

SUBMIT_OPERATION = "transport.task.submit@v1"


def build_submit_data(request: TransportRequest, transport_task_id: str) -> dict[str, Any]:
    common = {"transport_task_id": transport_task_id, "kind": request.kind.value}
    if isinstance(request, MoveRackRequest):
        return {
            **common,
            "rack_id": request.rack_id,
            "source": _json_value(request.source),
            "target": _json_value(request.target),
        }
    if isinstance(request, RotateRackRequest):
        return {
            **common,
            "rack_id": request.rack_id,
            "position": _json_value(request.position),
            "target_face": request.target_face.value,
        }
    if isinstance(request, MoveBinsRequest):
        return {**common, "moves": [_json_value(move) for move in request.moves]}
    if isinstance(request, ExchangeBinsRequest):
        return {**common, "exchange_pairs": [_json_value(pair) for pair in request.exchange_pairs]}
    raise TransportContractError("unsupported transport request")


def build_submit_envelope(
    operation_id: str,
    timestamp_ms: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "operation": SUBMIT_OPERATION,
        "timestamp": timestamp_ms,
        "data": payload,
    }


def submit_payload_digest(operation_id: str, timestamp_ms: int, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        build_submit_envelope(operation_id, timestamp_ms, payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":")))


__all__ = ["SUBMIT_OPERATION", "build_submit_data", "build_submit_envelope", "submit_payload_digest"]
