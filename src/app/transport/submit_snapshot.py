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
            "target_face": request.target_face.value,
        }
    if isinstance(request, RotateRackRequest):
        return {
            **common,
            "rack_id": request.rack_id,
            "source": _json_value(request.position),
            "target": _json_value(request.position),
            "target_face": request.target_face.value,
        }
    if isinstance(request, MoveBinsRequest):
        moves = [
            {
                "container_id": move.bin_id,
                "source": _json_value(move.source),
                "target": _json_value(move.target),
            }
            for move in request.moves
        ]
        return {**common, "moves": sorted(moves, key=lambda move: move["container_id"])}
    if isinstance(request, ExchangeBinsRequest):
        moves = [
            move
            for pair in request.exchange_pairs
            for move in (
                {
                    "container_id": pair.left_bin_id,
                    "source": _json_value(pair.left_location),
                    "target": _json_value(pair.right_location),
                },
                {
                    "container_id": pair.right_bin_id,
                    "source": _json_value(pair.right_location),
                    "target": _json_value(pair.left_location),
                },
            )
        ]
        return {**common, "moves": sorted(moves, key=lambda move: move["container_id"])}
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


def build_submit_request_body(operation_id: str, timestamp_ms: int, payload: dict[str, Any]) -> bytes:
    return json.dumps(
        build_submit_envelope(operation_id, timestamp_ms, payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def request_body_digest(request_body: bytes) -> str:
    return hashlib.sha256(request_body).hexdigest()


def _json_value(value: object) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":")))


__all__ = [
    "SUBMIT_OPERATION",
    "build_submit_data",
    "build_submit_envelope",
    "build_submit_request_body",
    "request_body_digest",
]
