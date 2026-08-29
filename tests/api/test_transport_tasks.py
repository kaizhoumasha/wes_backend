"""Transport 调试创建与本地只读状态 API 合同。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.transport.contracts import (
    TransportContractError,
    TransportHandle,
    TransportIdempotencyConflict,
    TransportResourceConflict,
)
from src.core.exceptions import NotFoundException
from src.core.uuid7 import new_uuid7
from src.register import register_exception, register_routers


class FakeTransportPort:
    def __init__(self) -> None:
        handle = TransportHandle("transport-api-test", new_uuid7())
        self.move_rack = AsyncMock(return_value=handle)
        self.rotate_rack = AsyncMock(return_value=handle)
        self.move_bins = AsyncMock(return_value=handle)
        self.exchange_bins = AsyncMock(return_value=handle)


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        closed=False,
        port=FakeTransportPort(),
        service=SimpleNamespace(
            get_task_snapshot=AsyncMock(),
            list_task_snapshots=AsyncMock(),
            preview_debug_task_reset=AsyncMock(),
            reset_debug_task=AsyncMock(),
        ),
    )


async def _allow_permission() -> None:
    return None


def _app(runtime: SimpleNamespace | None) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.state.transport_runtime = runtime
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/transport/"):
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = _allow_permission
    return app


def _route(app: FastAPI, path: str, method: str) -> APIRoute | None:
    return next(
        (
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == path and method in route.methods
        ),
        None,
    )


def _permission(route: APIRoute) -> list[str]:
    return [getattr(dependency.dependency, "permission_required", "") for dependency in route.dependencies]


def _rack_position(location_code: str) -> dict[str, str]:
    return {"kind": "RACK_POSITION", "location_code": location_code}


def _rack_slot(rack_id: str, slot_id: str) -> dict[str, str]:
    return {"kind": "RACK_BIN_SLOT", "rack_id": rack_id, "rack_face": "A", "slot_id": slot_id}


def _valid_payload(kind: str) -> dict[str, object]:
    common: dict[str, object] = {
        "client_request_id": new_uuid7(),
        "station_id": "STATION-DEBUG",
        "kind": kind,
    }
    if kind == "RACK_MOVE":
        return {
            **common,
            "data": {
                "rack_id": "RACK-01",
                "source": _rack_position("BUFFER-01"),
                "target": _rack_position("LINE-01"),
                "target_face": "A",
            },
        }
    if kind == "RACK_ROTATE":
        return {
            **common,
            "data": {
                "rack_id": "RACK-01",
                "position": _rack_position("LINE-01"),
                "target_face": "B",
            },
        }
    if kind == "BIN_MOVE":
        return {
            **common,
            "data": {
                "moves": [
                    {
                        "bin_id": "BIN-01",
                        "source": _rack_slot("RACK-01", "SLOT-01"),
                        "target": {"kind": "HANDOFF_POSITION", "location_code": "HANDOFF-01"},
                    }
                ]
            },
        }
    if kind == "BIN_EXCHANGE":
        return {
            **common,
            "data": {
                "exchange_pairs": [
                    {
                        "left_bin_id": "BIN-01",
                        "left_location": _rack_slot("RACK-01", "SLOT-01"),
                        "right_bin_id": "BIN-02",
                        "right_location": _rack_slot("RACK-02", "SLOT-01"),
                    }
                ]
            },
        }
    raise AssertionError(f"unsupported test kind: {kind}")


def _invalid_payload(case: str) -> dict[str, object]:
    payload = deepcopy(_valid_payload("RACK_MOVE"))
    data = payload["data"]
    assert isinstance(data, dict)
    if case == "unknown-kind":
        payload["kind"] = "UNKNOWN"
    elif case == "missing-kind":
        payload.pop("kind")
    elif case == "non-canonical-uuid7":
        payload["client_request_id"] = "019F12D0-58D7-7B4D-A23A-1B90AA5D4471"
    elif case == "non-uuid7":
        payload["client_request_id"] = "019f12d0-58d7-6b4d-a23a-1b90aa5d4471"
    elif case == "blank-text":
        data["rack_id"] = "   "
    elif case == "long-text":
        data["rack_id"] = "R" * 101
    elif case == "wrong-rack-position":
        data["source"] = {"kind": "HANDOFF_POSITION", "location_code": "HANDOFF-01"}
    elif case == "extra-root":
        payload["force"] = True
    elif case == "extra-data":
        data["legacy"] = True
    elif case == "extra-position":
        source = data["source"]
        assert isinstance(source, dict)
        source["legacy"] = True
    elif case.startswith("forbidden-"):
        payload[case.removeprefix("forbidden-")] = "forbidden"
    elif case in {"moves-empty", "moves-too-many", "extra-member"}:
        payload = deepcopy(_valid_payload("BIN_MOVE"))
        move_data = payload["data"]
        assert isinstance(move_data, dict)
        moves = move_data["moves"]
        assert isinstance(moves, list)
        if case == "moves-empty":
            move_data["moves"] = []
        elif case == "moves-too-many":
            move_data["moves"] = moves * 5
        else:
            member = moves[0]
            assert isinstance(member, dict)
            member["legacy"] = True
    elif case in {"exchange-empty", "exchange-too-many", "wrong-exchange-position"}:
        payload = deepcopy(_valid_payload("BIN_EXCHANGE"))
        exchange_data = payload["data"]
        assert isinstance(exchange_data, dict)
        pairs = exchange_data["exchange_pairs"]
        assert isinstance(pairs, list)
        if case == "exchange-empty":
            exchange_data["exchange_pairs"] = []
        elif case == "exchange-too-many":
            exchange_data["exchange_pairs"] = pairs * 3
        else:
            pair = pairs[0]
            assert isinstance(pair, dict)
            pair["left_location"] = {"kind": "HANDOFF_POSITION", "location_code": "HANDOFF-01"}
    else:
        raise AssertionError(f"unsupported invalid case: {case}")
    return payload


def test_transport_routes_are_registered_with_separate_permissions() -> None:
    app = _app(_runtime())

    create_route = _route(app, "/api/v1/transport/debug-tasks", "POST")
    preview_reset_route = _route(
        app,
        "/api/v1/transport/debug-tasks/{transport_task_id}/reset-preview",
        "GET",
    )
    reset_route = _route(app, "/api/v1/transport/debug-tasks/{transport_task_id}/reset", "POST")
    list_route = _route(app, "/api/v1/transport/tasks", "GET")
    read_route = _route(app, "/api/v1/transport/tasks/{transport_task_id}", "GET")

    assert create_route is not None
    assert preview_reset_route is not None
    assert reset_route is not None
    assert list_route is not None
    assert read_route is not None
    assert _permission(create_route) == ["ops:transport:debug-create"]
    assert _permission(preview_reset_route) == ["ops:transport:debug-preview"]
    assert _permission(reset_route) == ["ops:transport:debug-reset"]
    assert _permission(list_route) == ["ops:transport-task:list"]
    assert _permission(read_route) == ["ops:transport-task:read"]


def test_debug_task_openapi_exposes_exactly_four_named_examples_and_union_branches() -> None:
    schema = _app(_runtime()).openapi()
    content = schema["paths"]["/api/v1/transport/debug-tasks"]["post"]["requestBody"]["content"]["application/json"]

    assert set(content["examples"]) == {"rack_move", "rack_rotate", "bin_move", "bin_exchange"}
    union_schema = schema["components"]["schemas"]["_DebugTransportTaskRequest"]
    assert len(union_schema["oneOf"]) == 4
    assert union_schema["discriminator"]["propertyName"] == "kind"


def test_debug_reset_openapi_exposes_transport_task_id_length_contract() -> None:
    schema = _app(_runtime()).openapi()
    parameters = schema["paths"]["/api/v1/transport/debug-tasks/{transport_task_id}/reset-preview"]["get"]["parameters"]
    task_id = next(parameter for parameter in parameters if parameter["name"] == "transport_task_id")

    assert task_id["schema"]["minLength"] == 1
    assert task_id["schema"]["maxLength"] == 80


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_method"),
    [
        ("RACK_MOVE", "move_rack"),
        ("RACK_ROTATE", "rotate_rack"),
        ("BIN_MOVE", "move_bins"),
        ("BIN_EXCHANGE", "exchange_bins"),
    ],
)
async def test_debug_task_dispatches_exactly_one_transport_operation(kind: str, expected_method: str) -> None:
    runtime = _runtime()
    payload = _valid_payload(kind)
    expected_handle = TransportHandle("transport-api-test", str(payload["client_request_id"]))
    getattr(runtime.port, expected_method).return_value = expected_handle

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-tasks", json=payload)

    assert response.status_code == 202
    assert response.json()["code"] == "1004"
    assert response.json()["data"] == {
        "transport_task_id": expected_handle.transport_task_id,
        "client_request_id": expected_handle.client_request_id,
    }
    for method_name in ("move_rack", "rotate_rack", "move_bins", "exchange_bins"):
        assert getattr(runtime.port, method_name).await_count == (1 if method_name == expected_method else 0)
    called_args = getattr(runtime.port, expected_method).await_args.args
    assert called_args[1].workline_id == "TRANSPORT_DEBUG"
    assert called_args[1].station_id == "STATION-DEBUG"


@pytest.mark.asyncio
async def test_debug_task_reset_preview_and_apply_expose_bounded_cleanup_result() -> None:
    runtime = _runtime()
    runtime.service.preview_debug_task_reset.return_value = SimpleNamespace(
        transport_task_id="transport-reset-test",
        status="RECONCILING",
        evidence_count=0,
        callback_receipt_count=0,
        position_projection_count=0,
        outcome_version=0,
        member_count=1,
        binding_count=1,
        active_binding_count=1,
    )
    runtime.service.reset_debug_task.return_value = SimpleNamespace(
        transport_task_id="transport-reset-test",
        deleted_callback_receipt_count=0,
        deleted_evidence_count=0,
        deleted_position_projection_count=0,
        deleted_member_count=1,
        deleted_binding_count=1,
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        preview = await client.get("/api/v1/transport/debug-tasks/transport-reset-test/reset-preview")
        applied = await client.post("/api/v1/transport/debug-tasks/transport-reset-test/reset")

    assert preview.status_code == 200
    assert preview.json()["data"] == {
        "transport_task_id": "transport-reset-test",
        "status": "RECONCILING",
        "evidence_count": 0,
        "callback_receipt_count": 0,
        "position_projection_count": 0,
        "outcome_version": 0,
        "member_count": 1,
        "binding_count": 1,
        "active_binding_count": 1,
    }
    assert applied.status_code == 200
    assert applied.json()["data"] == {
        "transport_task_id": "transport-reset-test",
        "deleted_callback_receipt_count": 0,
        "deleted_evidence_count": 0,
        "deleted_position_projection_count": 0,
        "deleted_member_count": 1,
        "deleted_binding_count": 1,
    }
    runtime.service.preview_debug_task_reset.assert_awaited_once_with("transport-reset-test")
    runtime.service.reset_debug_task.assert_awaited_once_with("transport-reset-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("encoded_task_id", ["%20%20%20", "%00invalid"])
async def test_debug_task_reset_rejects_blank_or_nul_task_id_before_service(
    method: str,
    encoded_task_id: str,
) -> None:
    runtime = _runtime()
    path = f"/api/v1/transport/debug-tasks/{encoded_task_id}/{'reset-preview' if method == 'GET' else 'reset'}"

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.request(method, path)

    assert response.status_code == 422
    runtime.service.preview_debug_task_reset.assert_not_awaited()
    runtime.service.reset_debug_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_transport_tasks_returns_summary_page_without_raw_request_or_result() -> None:
    runtime = _runtime()
    runtime.service.list_task_snapshots.return_value = SimpleNamespace(
        items=(
            SimpleNamespace(
                transport_task_id="transport-api-test",
                client_request_id=new_uuid7(),
                submit_operation_id=new_uuid7(),
                kind="BIN_MOVE",
                status="FAILED",
                reason_code="TARGET_BLOCKED",
                created_at="2026-08-20T10:00:00Z",
                updated_at="2026-08-20T10:01:00Z",
                latest_evidence=SimpleNamespace(
                    operation="transport.task.resulted@v1",
                    operation_id=new_uuid7(),
                    outcome_revision=1,
                    status="APPLIED",
                    conflict_code=None,
                    received_at="2026-08-20T10:00:30Z",
                    processed_at="2026-08-20T10:00:31Z",
                ),
            ),
        ),
        next_cursor="next-page",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/transport/tasks",
            params={"limit": 1, "cursor": "current-page", "kind": "BIN_MOVE", "status": "FAILED"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["next_cursor"] == "next-page"
    item = response.json()["data"]["items"][0]
    assert item["transport_task_id"] == "transport-api-test"
    assert item["latest_evidence"]["status"] == "APPLIED"
    assert "request" not in item
    assert "result" not in item
    runtime.service.list_task_snapshots.assert_awaited_once_with(
        limit=1,
        cursor="current-page",
        kind="BIN_MOVE",
        status="FAILED",
    )


@pytest.mark.asyncio
async def test_get_transport_task_returns_local_snapshot_without_raw_callback() -> None:
    runtime = _runtime()
    runtime.service.get_task_snapshot.return_value = SimpleNamespace(
        transport_task_id="transport-api-test",
        client_request_id=new_uuid7(),
        submit_operation_id=new_uuid7(),
        kind="BIN_MOVE",
        status="SUCCEEDED",
        reason_code=None,
        created_at="2026-08-20T10:00:00Z",
        updated_at="2026-08-20T10:01:00Z",
        request={
            "client_request_id": new_uuid7(),
            "caller": {"workline_id": "TRANSPORT_DEBUG", "station_id": "STATION-DEBUG"},
            "kind": "BIN_MOVE",
            "moves": [
                {
                    "bin_id": "BIN-01",
                    "source": _rack_slot("RACK-01", "SLOT-01"),
                    "target": {"kind": "HANDOFF_POSITION", "location_code": "HANDOFF-01"},
                }
            ],
        },
        result={
            "outcome_version": 1,
            "status": "FAILED",
            "reason_code": "TARGET_BLOCKED",
            "members": [
                {
                    "object_id": "BIN-01",
                    "status": "FAILED",
                    "final_position": None,
                    "position_unknown": False,
                    "failure_code": "TARGET_BLOCKED",
                    "arrival_face": None,
                }
            ],
        },
        latest_evidence=SimpleNamespace(
            operation="transport.task.resulted@v1",
            operation_id=new_uuid7(),
            outcome_revision=1,
            status="APPLIED",
            conflict_code=None,
            received_at="2026-08-20T10:00:30Z",
            processed_at="2026-08-20T10:00:31Z",
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.get("/api/v1/transport/tasks/transport-api-test")

    assert response.status_code == 200
    assert response.json()["code"] == "1000"
    data = response.json()["data"]
    assert data["status"] == "SUCCEEDED"
    assert data["latest_evidence"]["status"] == "APPLIED"
    assert data["request"]["kind"] == "BIN_MOVE"
    assert data["result"]["members"][0]["status"] == "FAILED"
    assert data["created_at"].endswith("Z")
    assert data["latest_evidence"]["processed_at"].endswith("Z")
    assert "payload_json" not in response.text
    runtime.service.get_task_snapshot.assert_awaited_once_with("transport-api-test")


@pytest.mark.asyncio
async def test_get_transport_task_maps_missing_task_and_unavailable_runtime() -> None:
    missing_runtime = _runtime()
    missing_runtime.service.get_task_snapshot.side_effect = NotFoundException(
        resource_type="TransportTask",
        resource_id="missing",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(missing_runtime)), base_url="http://test") as client:
        missing = await client.get("/api/v1/transport/tasks/missing")
    async with AsyncClient(transport=ASGITransport(app=_app(None)), base_url="http://test") as client:
        unavailable = await client.get("/api/v1/transport/tasks/anything")

    assert missing.status_code == 404
    assert missing.json()["code"] == "3000"
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "5030"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (TransportIdempotencyConflict("changed payload"), 409, "3012"),
        (TransportResourceConflict("active resource"), 409, "3012"),
        (TransportContractError("invalid domain request"), 400, "2004"),
    ],
)
async def test_debug_task_maps_domain_failures(error: Exception, expected_status: int, expected_code: str) -> None:
    runtime = _runtime()
    runtime.port.move_rack.side_effect = error

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-tasks", json=_valid_payload("RACK_MOVE"))

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code


@pytest.mark.asyncio
async def test_debug_task_rejects_business_identity_and_unknown_fields_before_dispatch() -> None:
    runtime = _runtime()
    payload = _valid_payload("RACK_MOVE")
    payload["workline_id"] = "BUSINESS-LINE"
    data = payload["data"]
    assert isinstance(data, dict)
    data["legacy"] = True

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-tasks", json=payload)

    assert response.status_code == 422
    assert all(method.await_count == 0 for method in _port_methods(runtime.port))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "unknown-kind",
        "missing-kind",
        "non-canonical-uuid7",
        "non-uuid7",
        "blank-text",
        "long-text",
        "moves-empty",
        "moves-too-many",
        "exchange-empty",
        "exchange-too-many",
        "wrong-rack-position",
        "wrong-exchange-position",
        "extra-root",
        "extra-data",
        "extra-member",
        "extra-position",
        "forbidden-workline_id",
        "forbidden-transport_task_id",
        "forbidden-operation_id",
        "forbidden-timestamp",
    ],
)
async def test_debug_task_rejects_invalid_structure_before_dispatch(case: str) -> None:
    runtime = _runtime()

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-tasks", json=_invalid_payload(case))

    assert response.status_code == 422
    assert all(method.await_count == 0 for method in _port_methods(runtime.port))


@pytest.mark.asyncio
async def test_transport_routes_reject_closed_runtime() -> None:
    runtime = _runtime()
    runtime.closed = True

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        create = await client.post("/api/v1/transport/debug-tasks", json=_valid_payload("RACK_MOVE"))
        list_response = await client.get("/api/v1/transport/tasks")
        read = await client.get("/api/v1/transport/tasks/anything")

    assert (create.status_code, create.json()["code"]) == (503, "5030")
    assert (list_response.status_code, list_response.json()["code"]) == (503, "5030")
    assert (read.status_code, read.json()["code"]) == (503, "5030")


def _port_methods(port: FakeTransportPort) -> tuple[AsyncMock, ...]:
    return port.move_rack, port.rotate_rack, port.move_bins, port.exchange_bins
