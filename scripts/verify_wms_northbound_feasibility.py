"""验证 WMS Mock 是否实现当前冻结的 Transport 北向合同。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.wms_adapter.strict_json import is_json_utf8_media_type  # noqa: E402
from src.core.outbound_http.contracts import (  # noqa: E402
    OutboundHttpDeliveryState,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResponseLimits,
)
from src.core.outbound_http.factory import build_outbound_http_transport  # noqa: E402
from src.core.uuid7 import new_uuid7  # noqa: E402

TRANSPORT_PATH = "/api/v1/wes/transport-requests"
MAX_RESPONSE_BYTES = 256 * 1024
SIGNED_INT64_MAX = 2**63 - 1

RACK_MOVE: dict[str, Any] = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800000,
    "data": {
        "transport_task_id": "transport-rack-probe",
        "kind": "RACK_MOVE",
        "rack_id": "rack-probe",
        "source": {"kind": "RACK_POSITION", "location_code": "buffer-a"},
        "target": {"kind": "RACK_POSITION", "location_code": "station-a"},
        "target_face": "90",
        "rcs_template_id": "F01",
    },
}

RACK_ROTATE: dict[str, Any] = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800001,
    "data": {
        "transport_task_id": "transport-rotate-probe",
        "kind": "RACK_ROTATE",
        "rack_id": "rack-rotate-probe",
        "source": {"kind": "RACK_POSITION", "location_code": "station-b"},
        "target": {"kind": "RACK_POSITION", "location_code": "station-b"},
        "target_face": "270",
        "rcs_template_id": "CTU02",
    },
}

BIN_MOVE: dict[str, Any] = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800002,
    "data": {
        "transport_task_id": "transport-bin-probe",
        "kind": "BIN_MOVE",
        "moves": [
            {
                "container_id": "bin-move",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-a", "rack_face": "90", "slot_id": "3"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "roller-in"},
            }
        ],
    },
}

BIN_EXCHANGE: dict[str, Any] = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4475",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800003,
    "data": {
        "transport_task_id": "transport-exchange-probe",
        "kind": "BIN_EXCHANGE",
        "moves": [
            {
                "container_id": "bin-a",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-a", "rack_face": "90", "slot_id": "1"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-b", "rack_face": "90", "slot_id": "1"},
            },
            {
                "container_id": "bin-b",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-b", "rack_face": "90", "slot_id": "1"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-a", "rack_face": "90", "slot_id": "1"},
            },
            {
                "container_id": "bin-c",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-a", "rack_face": "90", "slot_id": "2"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-b", "rack_face": "90", "slot_id": "2"},
            },
            {
                "container_id": "bin-d",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-b", "rack_face": "90", "slot_id": "2"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-a", "rack_face": "90", "slot_id": "2"},
            },
        ],
    },
}


def _fresh_probe_payload(template: dict[str, Any], task_prefix: str) -> dict[str, Any]:
    payload = deepcopy(template)
    payload["operation_id"] = new_uuid7()
    payload["data"]["transport_task_id"] = f"{task_prefix}-{payload['operation_id']}"
    suffix = payload["operation_id"].rsplit("-", maxsplit=1)[-1]
    data = payload["data"]
    if data["kind"] in {"RACK_MOVE", "RACK_ROTATE"}:
        data["rack_id"] = f"{data['rack_id']}-{suffix}"
    else:
        rack_ids: dict[str, str] = {}
        for move in data["moves"]:
            move["container_id"] = f"{move['container_id']}-{suffix}"
            for position in (move["source"], move["target"]):
                if position["kind"] == "RACK_BIN_SLOT":
                    rack_ids.setdefault(position["rack_id"], f"{position['rack_id']}-{suffix}")
                    position["rack_id"] = rack_ids[position["rack_id"]]
    return payload


@dataclass(frozen=True)
class ProbeCaseResult:
    """单个合同断言的脱敏结果。"""

    case_id: str
    passed: bool
    detail: str = "CONTRACT_ASSERTION"


@dataclass(frozen=True)
class FeasibilityReport:
    """当前 Transport Mock 可行性报告。"""

    cases: tuple[ProbeCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)


async def _request(
    client: Any,
    method: str,
    path: str,
    *,
    request_timeout_seconds: float,
    **kwargs: Any,
) -> httpx.Response | None:
    if not isinstance(client, httpx.AsyncClient):
        payload = kwargs.get("json")
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        headers = (("content-type", "application/json"),) if payload is not None else ()
        result = await client.send(
            OutboundHttpRequest(
                method=OutboundHttpMethod(method),
                path=path,
                headers=headers,
                body=body,
                response_limits=OutboundHttpResponseLimits(
                    max_wire_bytes=MAX_RESPONSE_BYTES,
                    max_decoded_bytes=MAX_RESPONSE_BYTES,
                ),
            )
        )
        if result.delivery_state != OutboundHttpDeliveryState.RESPONSE_RECEIVED or result.status_code is None:
            return None
        return httpx.Response(
            result.status_code,
            headers=result.response_headers,
            content=result.decoded_body,
            request=httpx.Request(method, f"http://wms-probe{path}"),
        )
    try:
        async with asyncio.timeout(request_timeout_seconds):
            request = client.build_request(method, path, **kwargs)
            response = await client.send(request, stream=True)
            try:
                content = bytearray()
                async for chunk in response.aiter_raw(chunk_size=8192):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        return None
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(content),
                    request=request,
                )
            finally:
                await response.aclose()
    except (httpx.HTTPError, TimeoutError):
        return None


def _json_object(response: httpx.Response | None) -> dict[str, Any] | None:
    if response is None:
        return None
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_json_response_headers(response: httpx.Response) -> bool:
    headers = response.headers.multi_items()
    content_types = [value for name, value in headers if name.casefold() == "content-type"]
    if len(content_types) != 1 or not is_json_utf8_media_type(content_types[0]):
        return False
    encodings = [value for name, value in headers if name.casefold() == "content-encoding"]
    return len(encodings) <= 1 and (not encodings or encodings[0].strip().casefold() == "identity")


def _matches_ack(
    response: httpx.Response | None,
    *,
    status_code: int,
    operation_id: str,
    code: str,
    transport_task_id: str,
    reason_code: str | None = None,
) -> bool:
    payload = _json_object(response)
    if response is None or response.status_code != status_code or payload is None:
        return False
    timestamp = payload.get("timestamp")
    expected_data = {"transport_task_id": transport_task_id}
    if reason_code is not None:
        expected_data["reason_code"] = reason_code
    return (
        set(payload) == {"operation_id", "code", "timestamp", "data"}
        and payload["operation_id"] == operation_id
        and payload["code"] == code
        and _valid_json_response_headers(response)
        and isinstance(timestamp, int)
        and not isinstance(timestamp, bool)
        and 0 <= timestamp <= SIGNED_INT64_MAX
        and payload["data"] == expected_data
    )


async def run_probe(
    client: httpx.AsyncClient,
    *,
    request_timeout_seconds: float,
) -> FeasibilityReport:
    """只通过公开 HTTP 面验证当前合同，不读取 Mock 内部状态。"""

    rack_move = _fresh_probe_payload(RACK_MOVE, "transport-rack-probe")
    rack_rotate = _fresh_probe_payload(RACK_ROTATE, "transport-rotate-probe")
    bin_move = _fresh_probe_payload(BIN_MOVE, "transport-bin-probe")
    bin_exchange = _fresh_probe_payload(BIN_EXCHANGE, "transport-exchange-probe")
    invalid = _fresh_probe_payload(BIN_EXCHANGE, "transport-invalid-probe")
    invalid["data"]["moves"][0]["vehicle_id"] = "private-agv"
    same_face_rotate = _fresh_probe_payload(RACK_ROTATE, "transport-same-face-probe")
    unknown_face_rotate = _fresh_probe_payload(RACK_ROTATE, "transport-unknown-face-probe")
    active_rack_conflict = _fresh_probe_payload(RACK_MOVE, "transport-active-rack-probe")
    active_rack_conflict["data"]["rack_id"] = rack_move["data"]["rack_id"]
    active_rack_conflict["data"]["target"]["location_code"] = "station-active-conflict"
    rack_faces = {
        rack_rotate["data"]["rack_id"]: "90",
        same_face_rotate["data"]["rack_id"]: same_face_rotate["data"]["target_face"],
    }
    for payload in (bin_move, bin_exchange):
        for move in payload["data"]["moves"]:
            for position in (move["source"], move["target"]):
                if position["kind"] == "RACK_BIN_SLOT":
                    rack_faces[position["rack_id"]] = position["rack_face"]
    await _request(
        client,
        "POST",
        "/debug/rack-faces",
        request_timeout_seconds=request_timeout_seconds,
        json={"rack_faces": rack_faces},
    )
    root = await _request(client, "GET", "/", request_timeout_seconds=request_timeout_seconds)
    root_payload = _json_object(root)

    unsupported = deepcopy(rack_move)
    unsupported["operation"] = "transport.task.unsupported@v1"
    unsupported_response = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=unsupported,
    )
    first = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=rack_move,
    )
    duplicate = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=rack_move,
    )
    changed = deepcopy(rack_move)
    changed["data"]["target"]["location_code"] = "station-b"
    conflict = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=changed,
    )
    active_conflict = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=active_rack_conflict,
    )
    rotate = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=rack_rotate,
    )
    same_face = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=same_face_rotate,
    )
    unknown_face = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=unknown_face_rotate,
    )
    move_bin = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=bin_move,
    )
    exchange = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=bin_exchange,
    )
    rejected = await _request(
        client,
        "POST",
        TRANSPORT_PATH,
        request_timeout_seconds=request_timeout_seconds,
        json=invalid,
    )

    first_payload = _json_object(first)
    duplicate_payload = _json_object(duplicate)
    cases = (
        ProbeCaseResult(
            "service_contract",
            root is not None
            and root.status_code == 200
            and root_payload
            == {
                "service": "wms-transport-mock",
                "ready": True,
                "transport_path": TRANSPORT_PATH,
                "authentication": "NONE",
            },
        ),
        ProbeCaseResult(
            "operation_identity_scoped",
            _matches_ack(
                unsupported_response,
                status_code=422,
                operation_id=unsupported["operation_id"],
                code="REJECTED",
                transport_task_id=unsupported["data"]["transport_task_id"],
                reason_code="UNSUPPORTED_OPERATION",
            ),
        ),
        ProbeCaseResult(
            "rack_move_received",
            _matches_ack(
                first,
                status_code=202,
                operation_id=rack_move["operation_id"],
                code="RECEIVED",
                transport_task_id=rack_move["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "rack_move_duplicate",
            _matches_ack(
                duplicate,
                status_code=200,
                operation_id=rack_move["operation_id"],
                code="DUPLICATE",
                transport_task_id=rack_move["data"]["transport_task_id"],
            )
            and first_payload is not None
            and duplicate_payload is not None
            and duplicate_payload["timestamp"] == first_payload["timestamp"]
            and duplicate_payload["data"] == first_payload["data"],
        ),
        ProbeCaseResult(
            "rack_move_conflict",
            _matches_ack(
                conflict,
                status_code=409,
                operation_id=rack_move["operation_id"],
                code="CONFLICT",
                transport_task_id=rack_move["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "active_rack_conflict",
            _matches_ack(
                active_conflict,
                status_code=409,
                operation_id=active_rack_conflict["operation_id"],
                code="CONFLICT",
                transport_task_id=active_rack_conflict["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "rack_rotate_received",
            _matches_ack(
                rotate,
                status_code=202,
                operation_id=rack_rotate["operation_id"],
                code="RECEIVED",
                transport_task_id=rack_rotate["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "rack_rotate_same_face_conflict",
            _matches_ack(
                same_face,
                status_code=409,
                operation_id=same_face_rotate["operation_id"],
                code="CONFLICT",
                transport_task_id=same_face_rotate["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "rack_rotate_unknown_face_unavailable",
            _matches_ack(
                unknown_face,
                status_code=503,
                operation_id=unknown_face_rotate["operation_id"],
                code="UNAVAILABLE",
                transport_task_id=unknown_face_rotate["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "bin_move_received",
            _matches_ack(
                move_bin,
                status_code=202,
                operation_id=bin_move["operation_id"],
                code="RECEIVED",
                transport_task_id=bin_move["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "bin_exchange_received",
            _matches_ack(
                exchange,
                status_code=202,
                operation_id=bin_exchange["operation_id"],
                code="RECEIVED",
                transport_task_id=bin_exchange["data"]["transport_task_id"],
            ),
        ),
        ProbeCaseResult(
            "closed_payload_rejected",
            _matches_ack(
                rejected,
                status_code=422,
                operation_id=invalid["operation_id"],
                code="REJECTED",
                transport_task_id=invalid["data"]["transport_task_id"],
                reason_code="INVALID_DATA",
            ),
        ),
    )
    return FeasibilityReport(cases=cases)


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return seconds


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证当前 WMS Transport Mock 北向合同")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--timeout-seconds", type=_positive_seconds, default=2.0)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    transport = build_outbound_http_transport(
        system_id="wms_transport_feasibility",
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        report = await run_probe(transport, request_timeout_seconds=args.timeout_seconds)
    finally:
        await transport.aclose()
    print(json.dumps({"passed": report.passed, "cases": [asdict(case) for case in report.cases]}, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
