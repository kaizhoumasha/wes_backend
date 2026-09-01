"""按当前 Transport 合同运行的 WMS 联调 Mock。"""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.staticfiles import StaticFiles

from src.app.transport.callback_json import canonical_callback_json
from src.app.wms_adapter.strict_json import StrictJsonError, is_json_utf8_media_type, loads_transport_json
from src.core.uuid7 import is_uuid7
from tests.mock.wms_transport_mock_openapi import (
    MOCK_DEBUG_TAG,
    OPENAPI_TAGS,
    TRANSPORT_CALLBACK_RESPONSES,
    TRANSPORT_SUBMISSION_RESPONSES,
    WMS_TRANSPORT_CONTRACT_TAG,
    transport_callback_openapi_extra,
    transport_callback_request_schema,
    transport_submit_openapi_extra,
)

TRANSPORT_PATH = "/api/v1/wes/transport-requests"
WES_TRANSPORT_EVENT_URL = os.getenv("WES_TRANSPORT_EVENT_URL", "http://localhost:8001/api/v1/wms/events")
BODY_LIMIT = 256 * 1024
SUBMIT_OPERATION = "transport.task.submit@v1"
SIGNED_INT64_MAX = 2**63 - 1

SubmitMode = Literal["NORMAL", "UNAVAILABLE", "COORDINATED_EXCHANGE_UNSUPPORTED"]


class SubmitModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SubmitMode


class RackFaceConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rack_faces: dict[str, str]


class TransportSubmissionSnapshot(BaseModel):
    operation_id: str
    operation: str
    transport_task_id: str | None
    request: dict[str, object]
    status_code: int
    response: dict[str, object]


class TransportSubmissionSnapshots(BaseModel):
    submissions: list[TransportSubmissionSnapshot]


@dataclass(frozen=True, slots=True)
class _StoredSubmission:
    digest: str
    status_code: int
    response: dict[str, Any]
    snapshot: dict[str, Any]


class TransportSubmissionStore:
    """保存 Mock 进程内搬运提交身份和首次确定响应。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], _StoredSubmission] = {}
        self._conflicts: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._task_operations: dict[str, str] = {}
        self._task_outcome_revisions: dict[str, int] = {}
        self._task_member_facts: dict[str, dict[str, dict[str, object]]] = {}
        self._terminal_tasks: set[str] = set()
        self._resource_tasks: dict[tuple[str, str], str] = {}
        self._rack_faces: dict[str, str] = {}
        self._next_mode: SubmitMode = "NORMAL"

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._conflicts.clear()
            self._task_operations.clear()
            self._task_outcome_revisions.clear()
            self._task_member_facts.clear()
            self._terminal_tasks.clear()
            self._resource_tasks.clear()
            self._rack_faces.clear()
            self._next_mode = "NORMAL"

    def configure_rack_faces(self, rack_faces: dict[str, str]) -> None:
        with self._lock:
            self._rack_faces.update(rack_faces)

    def rack_faces(self, rack_ids: set[str]) -> dict[str, str]:
        with self._lock:
            return {rack_id: self._rack_faces[rack_id] for rack_id in rack_ids if rack_id in self._rack_faces}

    def configure_next_mode(self, mode: SubmitMode) -> None:
        with self._lock:
            self._next_mode = mode

    def consume_next_mode(self) -> SubmitMode:
        with self._lock:
            mode = self._next_mode
            self._next_mode = "NORMAL"
            return mode

    def existing(
        self,
        operation: str,
        operation_id: str,
        digest: str,
        transport_task_id: str | None,
    ) -> tuple[int, dict[str, Any]] | None:
        with self._lock:
            record = self._records.get((operation, operation_id))
            if record is None:
                return None
            if record.digest != digest:
                conflict_key = (operation, operation_id, digest)
                response = self._conflicts.get(conflict_key)
                if response is None:
                    response = _ack(operation_id, "CONFLICT", transport_task_id)
                    self._conflicts[conflict_key] = response
                return 409, deepcopy(response)
            if record.status_code == 202:
                return 200, {**deepcopy(record.response), "code": "DUPLICATE"}
            return record.status_code, deepcopy(record.response)

    def task_conflicts(self, transport_task_id: str, operation_id: str) -> bool:
        with self._lock:
            bound_operation = self._task_operations.get(transport_task_id)
            return bound_operation is not None and bound_operation != operation_id

    def resources_conflict(self, resources: set[tuple[str, str]], transport_task_id: str) -> bool:
        with self._lock:
            return any(
                (bound_task := self._resource_tasks.get(resource)) is not None and bound_task != transport_task_id
                for resource in resources
            )

    def store(
        self,
        *,
        operation: str,
        operation_id: str,
        transport_task_id: str | None,
        request: dict[str, Any],
        digest: str,
        status_code: int,
        response: dict[str, Any],
        resources: set[tuple[str, str]] | None = None,
    ) -> None:
        with self._lock:
            snapshot = {
                "operation_id": operation_id,
                "operation": operation,
                "transport_task_id": transport_task_id,
                "request": deepcopy(request),
                "status_code": status_code,
                "response": deepcopy(response),
            }
            self._records[(operation, operation_id)] = _StoredSubmission(
                digest,
                status_code,
                deepcopy(response),
                snapshot,
            )
            if status_code == 202 and transport_task_id is not None:
                self._task_operations[transport_task_id] = operation_id
                for resource in resources or set():
                    self._resource_tasks[resource] = transport_task_id

    def _frozen_data(self, transport_task_id: str) -> dict[str, Any] | None:
        operation_id = self._task_operations.get(transport_task_id)
        record = self._records.get((SUBMIT_OPERATION, operation_id or ""))
        request = record.snapshot.get("request") if record is not None else None
        data = request.get("data") if isinstance(request, dict) else None
        return data if isinstance(data, dict) else None

    def apply_position(self, data: dict[str, object]) -> None:
        with self._lock:
            if data.get("milestone") != "TARGET_PLACED":
                return
            transport_task_id = data.get("transport_task_id")
            container_id = data.get("container_id")
            final_position = data.get("final_position")
            if not isinstance(transport_task_id, str) or not isinstance(container_id, str):
                return
            frozen_data = self._frozen_data(transport_task_id)
            if frozen_data is None or frozen_data.get("kind") not in {"BIN_MOVE", "BIN_EXCHANGE"}:
                return
            moves = frozen_data.get("moves")
            if not isinstance(moves, list):
                return
            frozen_move = next(
                (move for move in moves if isinstance(move, dict) and move.get("container_id") == container_id),
                None,
            )
            if frozen_move is None or final_position != frozen_move.get("target"):
                return
            member_facts = self._task_member_facts.setdefault(transport_task_id, {})
            existing = member_facts.get(container_id, {})
            if "final_position" in existing and existing["final_position"] != final_position:
                return
            member_facts[container_id] = {**existing, "final_position": deepcopy(final_position)}

    def apply_result(self, data: dict[str, object]) -> None:
        with self._lock:
            transport_task_id = data.get("transport_task_id")
            outcome_revision = data.get("outcome_revision")
            if (
                not isinstance(transport_task_id, str)
                or not isinstance(outcome_revision, int)
                or isinstance(outcome_revision, bool)
                or not 1 <= outcome_revision <= SIGNED_INT64_MAX
                or transport_task_id in self._terminal_tasks
                or outcome_revision <= self._task_outcome_revisions.get(transport_task_id, 0)
            ):
                return
            frozen_data = self._frozen_data(transport_task_id)
            if not isinstance(frozen_data, dict) or not _result_matches_frozen_request(frozen_data, data):
                return
            member_facts = _known_member_facts(data)
            frozen_member_facts = self._task_member_facts.get(transport_task_id, {})
            if not set(frozen_member_facts) <= set(member_facts) or any(
                any(member_facts[object_id].get(key) != value for key, value in fact.items())
                for object_id, fact in frozen_member_facts.items()
            ):
                return
            self._task_member_facts[transport_task_id] = {
                object_id: deepcopy(fact) for object_id, fact in member_facts.items()
            }
            self._task_outcome_revisions[transport_task_id] = outcome_revision
            if _result_has_unknown_position(data):
                return
            if data.get("kind") in {"RACK_MOVE", "RACK_ROTATE"}:
                rack_id = data["rack_id"]
                arrival_face = data["arrival_face"]
                if self._resource_tasks.get(("RACK", rack_id)) == transport_task_id:
                    self._rack_faces[rack_id] = arrival_face
            self._resource_tasks = {
                resource: bound_task
                for resource, bound_task in self._resource_tasks.items()
                if bound_task != transport_task_id
            }
            self._terminal_tasks.add(transport_task_id)

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(record.snapshot) for record in self._records.values()]


class FixedTransportBodyLimitMiddleware:
    """在 Starlette 缓冲搬运提交请求体前执行固定字节上限。"""

    def __init__(self, app, *, submit_path: str) -> None:
        self.app = app
        self._submit_path = submit_path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != self._submit_path:
            await self.app(scope, receive, send)
            return
        messages: list[dict[str, Any]] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            messages.append(message)
            if message["type"] != "http.request":
                continue
            size += len(message.get("body", b""))
            if size > BODY_LIMIT:
                await Response(status_code=413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


app = FastAPI(title="WMS Transport Mock", version="1.0.0", docs_url=None, redoc_url=None, openapi_tags=OPENAPI_TAGS)
app.mount(
    "/static/swagger-ui",
    StaticFiles(directory=Path(__file__).resolve().parents[2] / "src" / "static" / "swagger-ui"),
    name="swagger-ui",
)
app.add_middleware(FixedTransportBodyLimitMiddleware, submit_path=TRANSPORT_PATH)
transport_submission_store = TransportSubmissionStore()


@app.get("/docs", include_in_schema=False)
async def swagger_docs() -> Response:
    from fastapi.openapi.docs import get_swagger_ui_html

    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui/swagger-ui.css",
        swagger_favicon_url="",
        swagger_ui_parameters={"validatorUrl": None},
    )


def reset_mock_wms_state() -> None:
    transport_submission_store.reset()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _ack(
    operation_id: str, code: str, transport_task_id: str | None, *, reason_code: str | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if transport_task_id:
        data["transport_task_id"] = transport_task_id
    if reason_code is not None:
        data["reason_code"] = reason_code
    return {"operation_id": operation_id, "code": code, "timestamp": _now_ms(), "data": data}


def _message_digest(envelope: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_callback_json(envelope).encode("utf-8")).hexdigest()


def _valid_identity(envelope: object) -> tuple[str, str] | None:
    if not isinstance(envelope, dict):
        return None
    operation_id = envelope.get("operation_id")
    operation = envelope.get("operation")
    if not is_uuid7(operation_id) or operation_id != operation_id.lower() or not isinstance(operation, str):
        return None
    return operation, operation_id


def _nonblank(value: object, *, max_length: int) -> bool:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > max_length:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _strict_object(value: object, required: set[str], optional: set[str] | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    allowed = required | (optional or set())
    return value if required <= set(value) <= allowed else None


def _position(value: object, *, allowed_kinds: set[str]) -> tuple[Any, ...] | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in allowed_kinds:
        return None
    if kind in {"RACK", "ZONE", "RACK_POSITION", "HANDOFF_POSITION"}:
        position = _strict_object(value, {"kind", "location_code"})
        if position is None or not _nonblank(position["location_code"], max_length=100):
            return None
        return kind, position["location_code"]
    position = _strict_object(value, {"kind", "rack_id", "rack_face", "slot_id"})
    if (
        position is None
        or not _nonblank(position["rack_id"], max_length=100)
        or type(position["rack_face"]) is not str
        or position["rack_face"] == ""
        or not _nonblank(position["slot_id"], max_length=100)
    ):
        return None
    return kind, position["rack_id"], position["rack_face"], position["slot_id"]


def _result_matches_frozen_request(frozen: dict[str, Any], result: dict[str, object]) -> bool:
    kind = frozen.get("kind")
    if result.get("kind") != kind:
        return False
    if kind in {"RACK_MOVE", "RACK_ROTATE"}:
        if result.get("rack_id") != frozen.get("rack_id"):
            return False
        if result.get("position_unknown") is True:
            return result.get("status") == "FAILED"
        final_position = result.get("final_position")
        if _position(final_position, allowed_kinds={"RACK_POSITION"}) is None:
            return False
        arrival_face = result.get("arrival_face")
        if type(arrival_face) is not str or arrival_face == "":
            return False
        if result.get("status") == "SUCCEEDED":
            target = frozen.get("target")
            target_matches = (
                final_position == target if isinstance(target, dict) and target.get("kind") == "RACK_POSITION" else True
            )
            return target_matches and arrival_face == frozen.get("target_face")
        return result.get("status") == "FAILED"
    if kind not in {"BIN_MOVE", "BIN_EXCHANGE"}:
        return False
    moves = frozen.get("moves")
    results = result.get("results")
    if not isinstance(moves, list) or not isinstance(results, list) or not results:
        return False
    frozen_by_container = {
        move.get("container_id"): move
        for move in moves
        if isinstance(move, dict) and isinstance(move.get("container_id"), str)
    }
    result_by_container = {
        item.get("container_id"): item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("container_id"), str)
    }
    if len(frozen_by_container) != len(moves) or len(result_by_container) != len(results):
        return False
    if set(result_by_container) != set(frozen_by_container):
        return False
    for container_id, item in result_by_container.items():
        if item.get("position_unknown") is True:
            if item.get("status") != "FAILED":
                return False
            continue
        final_position = item.get("final_position")
        if _position(final_position, allowed_kinds={"RACK_BIN_SLOT", "HANDOFF_POSITION"}) is None:
            return False
        if item.get("status") == "SUCCEEDED":
            if final_position != frozen_by_container[container_id].get("target"):
                return False
        elif item.get("status") != "FAILED":
            return False
    return True


def _result_has_unknown_position(result: dict[str, object]) -> bool:
    if result.get("kind") in {"RACK_MOVE", "RACK_ROTATE"}:
        return result.get("position_unknown") is True
    results = result.get("results")
    return isinstance(results, list) and any(
        isinstance(item, dict) and item.get("position_unknown") is True for item in results
    )


def _known_member_facts(result: dict[str, object]) -> dict[str, dict[str, object]]:
    results = result.get("results")
    if not isinstance(results, list):
        return {}
    facts: dict[str, dict[str, object]] = {}
    for item in results:
        if not isinstance(item, dict) or item.get("position_unknown") is True:
            continue
        container_id = item.get("container_id")
        if isinstance(container_id, str):
            facts[container_id] = {
                "status": item.get("status"),
                "final_position": deepcopy(item.get("final_position")),
                "failure_code": item.get("failure_code"),
                "arrival_face": item.get("arrival_face"),
            }
    return facts


def _valid_rack_data(data: dict[str, Any], kind: str) -> bool:
    rack = _strict_object(
        data,
        {"transport_task_id", "kind", "rack_id", "source", "target", "target_face", "rcs_template_id"},
    )
    if (
        rack is None
        or not _nonblank(rack["transport_task_id"], max_length=80)
        or not _nonblank(rack["rack_id"], max_length=100)
        or type(rack["target_face"]) is not str
        or rack["target_face"] == ""
        or rack["rcs_template_id"] not in {"CTU01", "CTU02", "CTU03", "F01"}
    ):
        return False
    source = _position(rack["source"], allowed_kinds={"RACK", "ZONE", "RACK_POSITION"})
    target = _position(rack["target"], allowed_kinds={"RACK", "ZONE", "RACK_POSITION"})
    if source is None or target is None:
        return False
    if any(position[0] == "RACK" and position[1] != rack["rack_id"] for position in (source, target)):
        return False
    if kind == "RACK_ROTATE":
        return rack["rcs_template_id"] == "CTU02" and source[0] == "RACK_POSITION" and source == target
    approved_edges = {
        "CTU01": {
            ("ZONE", "RACK_POSITION"),
            ("RACK", "RACK_POSITION"),
            ("RACK_POSITION", "RACK_POSITION"),
        },
        "CTU03": {
            ("RACK_POSITION", "RACK"),
            ("RACK_POSITION", "ZONE"),
            ("RACK_POSITION", "RACK_POSITION"),
        },
        "F01": {("RACK_POSITION", "RACK_POSITION")},
    }
    return source != target and (source[0], target[0]) in approved_edges.get(rack["rcs_template_id"], set())


def _valid_bin_data(data: dict[str, Any], kind: str) -> bool:
    request = _strict_object(data, {"transport_task_id", "kind", "moves"})
    if request is None or not _nonblank(request["transport_task_id"], max_length=80):
        return False
    moves = request["moves"]
    if not isinstance(moves, list) or not moves:
        return False
    if (kind == "BIN_MOVE" and len(moves) > 4) or (kind == "BIN_EXCHANGE" and len(moves) not in {2, 4}):
        return False
    identities: list[str] = []
    slots: list[tuple[Any, ...]] = []
    exchange_edges: list[tuple[tuple[Any, ...], tuple[Any, ...]]] = []
    rack_faces: dict[str, str] = {}
    for raw_move in moves:
        move = _strict_object(raw_move, {"container_id", "source", "target"})
        if move is None or not _nonblank(move["container_id"], max_length=100):
            return False
        allowed = {"RACK_BIN_SLOT"} if kind == "BIN_EXCHANGE" else {"RACK_BIN_SLOT", "HANDOFF_POSITION"}
        source = _position(move["source"], allowed_kinds=allowed)
        target = _position(move["target"], allowed_kinds=allowed)
        if source is None or target is None or source == target or "RACK_BIN_SLOT" not in {source[0], target[0]}:
            return False
        if kind == "BIN_EXCHANGE":
            exchange_edges.append((source, target))
        identities.append(move["container_id"])
        for position in (source, target):
            if position[0] != "RACK_BIN_SLOT":
                continue
            if kind == "BIN_MOVE" and position in slots:
                return False
            slots.append(position)
            rack_id, rack_face = str(position[1]), str(position[2])
            if rack_id in rack_faces and rack_faces[rack_id] != rack_face:
                return False
            rack_faces[rack_id] = rack_face
    if len(identities) != len(set(identities)) or identities != sorted(identities):
        return False
    if kind == "BIN_EXCHANGE":
        endpoint_groups = {(slot[1], slot[2]) for slot in slots}
        if not 1 <= len(endpoint_groups) <= 2:
            return False
        if len(endpoint_groups) == 2 and any(
            (source[1], source[2]) == (target[1], target[2]) for source, target in exchange_edges
        ):
            return False
        sources = [source for source, _ in exchange_edges]
        targets = [target for _, target in exchange_edges]
        if len(sources) != len(set(sources)) or len(targets) != len(set(targets)) or set(sources) != set(targets):
            return False
        source_targets = {
            (canonical_callback_json(move["source"]), canonical_callback_json(move["target"])) for move in moves
        }
        if any((target, source) not in source_targets for source, target in source_targets):
            return False
    return True


def _valid_submit_envelope(envelope: dict[str, Any]) -> tuple[bool, str]:
    if set(envelope) != {"operation_id", "operation", "timestamp", "data"}:
        return False, "INVALID_ENVELOPE"
    timestamp = envelope["timestamp"]
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or not 0 <= timestamp <= SIGNED_INT64_MAX:
        return False, "INVALID_ENVELOPE"
    data = envelope["data"]
    if not isinstance(data, dict):
        return False, "INVALID_DATA"
    kind = data.get("kind")
    if not isinstance(kind, str):
        return False, "INVALID_DATA"
    if kind in {"RACK_MOVE", "RACK_ROTATE"}:
        return _valid_rack_data(data, kind), "INVALID_DATA"
    if kind in {"BIN_MOVE", "BIN_EXCHANGE"}:
        return _valid_bin_data(data, kind), "INVALID_DATA"
    return False, "INVALID_DATA"


def _resource_keys(data: dict[str, Any]) -> set[tuple[str, str]]:
    kind = data["kind"]
    if kind in {"RACK_MOVE", "RACK_ROTATE"}:
        return {("RACK", data["rack_id"])}
    resources = {("BIN", move["container_id"]) for move in data["moves"]}
    resources.update(
        ("RACK", position["rack_id"])
        for move in data["moves"]
        for position in (move["source"], move["target"])
        if position["kind"] == "RACK_BIN_SLOT"
    )
    return resources


def _rack_face_admission(data: dict[str, Any]) -> Literal["ACCEPT", "CONFLICT", "UNAVAILABLE"]:
    kind = data["kind"]
    if kind == "RACK_MOVE":
        return "ACCEPT"
    if kind == "RACK_ROTATE":
        requested_faces = {data["rack_id"]: data["target_face"]}
    else:
        requested_faces = {
            position["rack_id"]: position["rack_face"]
            for move in data["moves"]
            for position in (move["source"], move["target"])
            if position["kind"] == "RACK_BIN_SLOT"
        }
    current_faces = transport_submission_store.rack_faces(set(requested_faces))
    if set(current_faces) != set(requested_faces):
        return "UNAVAILABLE"
    if kind == "RACK_ROTATE":
        return "CONFLICT" if current_faces[data["rack_id"]] == data["target_face"] else "ACCEPT"
    return "CONFLICT" if any(current_faces[rack_id] != face for rack_id, face in requested_faces.items()) else "ACCEPT"


def _associated_response(
    *,
    operation: str,
    operation_id: str,
    transport_task_id: str | None,
    request: dict[str, Any],
    digest: str,
    status_code: int,
    code: str,
    reason_code: str | None = None,
    resources: set[tuple[str, str]] | None = None,
) -> JSONResponse:
    response = _ack(operation_id, code, transport_task_id, reason_code=reason_code)
    transport_submission_store.store(
        operation=operation,
        operation_id=operation_id,
        transport_task_id=transport_task_id,
        request=request,
        digest=digest,
        status_code=status_code,
        response=response,
        resources=resources,
    )
    return JSONResponse(status_code=status_code, content=response)


@app.post(
    TRANSPORT_PATH,
    tags=[WMS_TRANSPORT_CONTRACT_TAG],
    responses=TRANSPORT_SUBMISSION_RESPONSES,
    openapi_extra=transport_submit_openapi_extra(),
)
async def submit_transport(request: Request) -> Response:
    if not is_json_utf8_media_type(request.headers.get("content-type", "")):
        return Response(status_code=400)
    if request.headers.get("content-encoding", "identity").casefold() != "identity":
        return Response(status_code=400)
    raw_body = await request.body()
    try:
        envelope = loads_transport_json(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return Response(status_code=400)
    identity = _valid_identity(envelope)
    if identity is None:
        return Response(status_code=400)
    operation, operation_id = identity
    if not isinstance(envelope, dict):
        return Response(status_code=400)
    data = envelope.get("data")
    transport_task_id = data.get("transport_task_id") if isinstance(data, dict) else None
    task_id = transport_task_id if _nonblank(transport_task_id, max_length=80) else None
    digest = _message_digest(envelope)
    existing = transport_submission_store.existing(operation, operation_id, digest, task_id)
    if existing is not None:
        status_code, response = existing
        return JSONResponse(status_code=status_code, content=response)
    if operation != SUBMIT_OPERATION:
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=422,
            code="REJECTED",
            reason_code="UNSUPPORTED_OPERATION",
        )
    valid, reason_code = _valid_submit_envelope(envelope)
    if not valid:
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=422,
            code="REJECTED",
            reason_code=reason_code,
        )
    if transport_submission_store.task_conflicts(task_id or "", operation_id):
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=409,
            code="CONFLICT",
        )
    mode = transport_submission_store.consume_next_mode()
    if mode == "UNAVAILABLE":
        return JSONResponse(status_code=503, content=_ack(operation_id, "UNAVAILABLE", task_id))
    if mode == "COORDINATED_EXCHANGE_UNSUPPORTED" and data.get("kind") == "BIN_EXCHANGE":
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=422,
            code="REJECTED",
            reason_code="COORDINATED_BIN_EXCHANGE_UNSUPPORTED",
        )
    face_admission = _rack_face_admission(data)
    if face_admission == "UNAVAILABLE":
        return JSONResponse(status_code=503, content=_ack(operation_id, "UNAVAILABLE", task_id))
    if face_admission == "CONFLICT":
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=409,
            code="CONFLICT",
        )
    resources = _resource_keys(data)
    if transport_submission_store.resources_conflict(resources, task_id or ""):
        return _associated_response(
            operation=operation,
            operation_id=operation_id,
            transport_task_id=task_id,
            request=envelope,
            digest=digest,
            status_code=409,
            code="CONFLICT",
        )
    return _associated_response(
        operation=operation,
        operation_id=operation_id,
        transport_task_id=task_id,
        request=envelope,
        digest=digest,
        status_code=202,
        code="RECEIVED",
        resources=resources,
    )


@app.post("/debug/reset", tags=[MOCK_DEBUG_TAG])
async def debug_reset() -> dict[str, bool]:
    reset_mock_wms_state()
    return {"reset": True}


@app.post("/debug/transport-submit-mode", tags=[MOCK_DEBUG_TAG])
async def debug_transport_submit_mode(request: SubmitModeRequest) -> dict[str, SubmitMode]:
    transport_submission_store.configure_next_mode(request.mode)
    return {"mode": request.mode}


@app.post("/debug/rack-faces", tags=[MOCK_DEBUG_TAG])
async def debug_rack_faces(request: RackFaceConfiguration) -> RackFaceConfiguration:
    if any(not _nonblank(rack_id, max_length=100) for rack_id in request.rack_faces) or any(
        type(face) is not str or face == "" for face in request.rack_faces.values()
    ):
        return JSONResponse(status_code=422, content={"code": "INVALID_RACK_ID"})
    transport_submission_store.configure_rack_faces(request.rack_faces)
    return request


@app.get("/debug/transport-submissions", response_model=TransportSubmissionSnapshots, tags=[MOCK_DEBUG_TAG])
async def debug_transport_submissions() -> TransportSubmissionSnapshots:
    return TransportSubmissionSnapshots(submissions=transport_submission_store.snapshots())


async def _post_transport_callback(url: str, payload: dict[str, object]) -> int:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
    return response.status_code


def _apply_callback_state(payload: dict[str, object]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    operation = payload.get("operation")
    if operation == "transport.task.member_position_changed@v1":
        transport_submission_store.apply_position(data)
    elif operation == "transport.task.resulted@v1":
        transport_submission_store.apply_result(data)


@app.post(
    "/debug/transport-callbacks",
    tags=[MOCK_DEBUG_TAG],
    responses=TRANSPORT_CALLBACK_RESPONSES,
    openapi_extra=transport_callback_openapi_extra(),
)
async def debug_transport_callback(payload: dict[str, object]) -> Response:
    try:
        status_code = await _post_transport_callback(WES_TRANSPORT_EVENT_URL, payload)
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"code": "WES_CALLBACK_TIMEOUT"})
    except httpx.RequestError:
        return JSONResponse(status_code=502, content={"code": "WES_CALLBACK_UNAVAILABLE"})
    if status_code in {200, 202}:
        _apply_callback_state(payload)
    return JSONResponse(content={"status_code": status_code})


@app.get("/", tags=[MOCK_DEBUG_TAG])
async def root() -> dict[str, object]:
    return {
        "service": "wms-transport-mock",
        "ready": True,
        "transport_path": TRANSPORT_PATH,
        "authentication": "NONE",
    }


_default_openapi = app.openapi


def _mock_openapi() -> dict[str, Any]:
    document = _default_openapi()
    document["paths"]["/debug/transport-callbacks"]["post"]["requestBody"]["content"]["application/json"]["schema"] = (
        transport_callback_request_schema()
    )
    return document


app.openapi = _mock_openapi


__all__ = ["app", "reset_mock_wms_state", "transport_submission_store"]
