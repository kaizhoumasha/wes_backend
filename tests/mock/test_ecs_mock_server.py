"""通用 ECS Mock 外部设备协议测试。"""

import asyncio
import builtins
import importlib
import importlib.util
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from tests.mock import ecs_mock_server


class CapturingAsyncClient:
    requests: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict | None = None):
        self.requests.append({"url": url, "json": json, "headers": headers or {}})
        return FakeResponse()


class FakeResponse:
    status_code = 200
    text = '{"code": 200, "message": "OK"}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"code": 200, "message": "OK"}


def setup_function() -> None:
    CapturingAsyncClient.requests.clear()
    ecs_mock_server.reset_mock_state()


def _command_payload(command_code: str, task_type: str = "PICK_AND_PLACE", **overrides) -> dict:
    payload = {
        "device_code": "ROBOT-ARM-01",
        "command_code": command_code,
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "task_type": task_type,
        "timestamp": 1_786_579_200_000,
        "params": {},
        "trace_id": f"TRACE-{command_code}",
    }
    payload.update(overrides)
    return payload


def test_ecs_mock_catalog_loads_without_src_package(monkeypatch) -> None:
    module_path = ecs_mock_server.DOCKER_APP_ROOT / "tests" / "mock" / "ecs_mock_catalog.py"
    spec = importlib.util.spec_from_file_location("isolated_ecs_mock_catalog", module_path)
    assert spec is not None and spec.loader is not None
    original_import = builtins.__import__

    def import_without_src(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src" or name.startswith("src."):
            raise ModuleNotFoundError("No module named 'src'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_src)
    isolated_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_module)

    data = isolated_module.default_success_data(
        "ROBOT-ARM-01",
        "PICK_AND_PLACE",
        {"object_key": "PKG-001"},
    )

    assert data == {
        "device_code": "ROBOT-ARM-01",
        "task_type": "PICK_AND_PLACE",
        "accepted_params": {"object_key": "PKG-001"},
    }


def test_ecs_mock_default_callbacks_target_localhost(monkeypatch) -> None:
    monkeypatch.delenv("WES_RESULT_CALLBACK_URL", raising=False)
    monkeypatch.delenv("WES_EVENT_CALLBACK_URL", raising=False)

    reloaded = importlib.reload(ecs_mock_server)

    assert reloaded.WES_RESULT_CALLBACK_URL == "http://localhost:8001/api/v1/callback/result"
    assert reloaded.WES_EVENT_CALLBACK_URL == "http://localhost:8001/api/v1/callback/event"


def test_ecs_mock_acknowledges_known_device_and_callbacks_success(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-ECS-001", params={"object_key": "PKG-001"}),
        )

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "ACCEPTED", "trace_id": "TRACE-CMD-ECS-001"}
    callback = CapturingAsyncClient.requests[0]
    assert callback["url"] == ecs_mock_server.WES_RESULT_CALLBACK_URL
    assert callback["json"]["result"] == "SUCCESS"
    assert callback["json"]["data"] == {
        "device_code": "ROBOT-ARM-01",
        "task_type": "PICK_AND_PLACE",
        "accepted_params": {"object_key": "PKG-001"},
    }
    assert callback["headers"]["X-App-ID"]


def test_ecs_mock_rejects_unknown_or_unsupported_device_command(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)

    with TestClient(ecs_mock_server.app) as client:
        unknown = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-404", device_code="UNKNOWN"),
        )
        unsupported = client.post(
            "/api/v1/device/command",
            json=_command_payload(
                "CMD-UNSUPPORTED",
                device_code="CAMERA-CONVEYOR-01",
                contract_key="camera.scan",
            ),
        )

    assert unknown.status_code == 404
    assert unknown.json() == {"code": 404, "message": "DEVICE_NOT_FOUND", "trace_id": "TRACE-CMD-404"}
    assert unsupported.status_code == 422
    assert unsupported.json() == {
        "code": 422,
        "message": "ANNEX_VALIDATION_FAILED",
        "trace_id": "TRACE-CMD-UNSUPPORTED",
    }
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_fixed_endpoints_return_closed_error_envelopes() -> None:
    with TestClient(ecs_mock_server.app) as client:
        invalid = client.post("/api/v1/device/command", json={"device_code": "ROBOT-ARM-01", "extra": True})
        mismatch = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-MISMATCH", contract_version="3.0"),
        )
        missing = client.get("/api/v1/device/status", params={"device_code": "UNKNOWN"})
        ecs_mock_server.runtime_states["ROBOT-ARM-01"].status = "RUNNING"
        busy = client.post("/api/v1/device/command", json=_command_payload("CMD-BUSY"))

    assert invalid.status_code == 400
    assert invalid.json() == {"code": 400, "message": "INVALID_ENVELOPE"}
    assert mismatch.status_code == 422
    assert mismatch.json() == {
        "code": 422,
        "message": "ANNEX_VALIDATION_FAILED",
        "trace_id": "TRACE-CMD-MISMATCH",
    }
    assert missing.status_code == 404
    assert missing.json() == {"code": 404, "message": "DEVICE_NOT_FOUND"}
    assert busy.status_code == 429
    assert busy.headers["Retry-After"] == "5"
    assert busy.json() == {"code": 429, "message": "CAPACITY_EXCEEDED", "trace_id": "TRACE-CMD-BUSY"}


@pytest.mark.parametrize("invalid", [{"timestamp": "1"}, {"timestamp": 0}, {"device_code": "ARM 01"}])
def test_ecs_mock_rejects_non_strict_command_fields(invalid: dict) -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/device/command", json={**_command_payload("CMD-STRICT"), **invalid})
    assert response.status_code == 400
    assert response.json()["message"] == "INVALID_ENVELOPE"


@pytest.mark.parametrize(
    "invalid",
    [
        {"command_code": "CMD@INVALID"},
        {"task_type": "TASK#INVALID"},
        {"contract_version": "V" * 41},
        {"trace_id": "T" * 121},
    ],
)
def test_ecs_mock_rejects_tokens_outside_frozen_wire(invalid: dict) -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/device/command", json={**_command_payload("CMD-TOKEN"), **invalid})
    assert response.status_code == 400


def test_ecs_mock_invalid_envelope_preserves_valid_trace_id() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={**_command_payload("CMD-INVALID-TRACE"), "timestamp": 0, "trace_id": "TRACE-VALID"},
        )
    assert response.status_code == 400
    assert response.json()["trace_id"] == "TRACE-VALID"


def test_ecs_mock_accepts_frozen_wire_token_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                **_command_payload("C" * 160),
                "task_type": "P" * 160,
                "contract_version": "2" * 40,
                "trace_id": "T" * 120,
            },
        )
    assert response.status_code == 422
    assert response.json()["message"] == "ANNEX_VALIDATION_FAILED"


def test_ecs_mock_max_command_code_generates_valid_stable_result_identity(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={**_command_payload("C" * 160), "trace_id": "TRACE-MAX-COMMAND"},
        )

    assert response.status_code == 200
    source_event_id = CapturingAsyncClient.requests[0]["json"]["source_event_id"]
    assert source_event_id.startswith("RESULT-")
    assert len(source_event_id) <= 160


def test_ecs_mock_fixed_endpoints_close_405_and_413_envelopes() -> None:
    with TestClient(ecs_mock_server.app) as client:
        wrong_method = client.get("/api/v1/device/command")
        oversized = client.post(
            "/api/v1/device/command",
            content=b"x" * (256 * 1024 + 1),
            headers={"content-type": "application/json"},
        )

    assert wrong_method.status_code == 405
    assert wrong_method.json() == {"code": 405, "message": "METHOD_NOT_ALLOWED"}
    assert oversized.status_code == 413
    assert oversized.json() == {"code": 413, "message": "PAYLOAD_TOO_LARGE"}


def test_ecs_mock_body_limit_stops_on_client_disconnect() -> None:
    downstream_called = False
    messages = iter(
        [
            {"type": "http.request", "body": b"partial", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return next(messages)

    async def send(_message) -> None:
        return None

    middleware = ecs_mock_server.FixedWireBodyLimitMiddleware(downstream)
    asyncio.run(
        middleware(
            {"type": "http", "path": "/api/v1/device/command"},
            receive,
            send,
        )
    )

    assert downstream_called is False


def test_ecs_mock_command_identity_is_idempotent_and_conflicting_payload_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    payload = _command_payload("CMD-IDEMPOTENT")
    with TestClient(ecs_mock_server.app) as client:
        first = client.post("/api/v1/device/command", json=payload)
        duplicate = client.post("/api/v1/device/command", json={**payload, "trace_id": "TRACE-RETRY"})
        conflict = client.post("/api/v1/device/command", json={**payload, "params": {"changed": True}})

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json() == {"code": 200, "message": "ACCEPTED", "trace_id": "TRACE-RETRY"}
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"
    assert len(CapturingAsyncClient.requests) == 1


def test_ecs_mock_omits_absent_trace_from_ack_and_result(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    payload = _command_payload("CMD-NO-TRACE")
    payload.pop("trace_id")
    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/device/command", json=payload)

    assert response.status_code == 200
    assert "trace_id" not in response.json()
    result = CapturingAsyncClient.requests[0]["json"]
    assert result["result"] == "SUCCESS"
    assert "trace_id" not in result


def test_ecs_mock_rejected_command_permanently_fixes_identity(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    payload = _command_payload("CMD-REJECTED-IDEMPOTENT")
    ecs_mock_server.runtime_states["ROBOT-ARM-01"].status = "RUNNING"
    with TestClient(ecs_mock_server.app) as client:
        rejected = client.post("/api/v1/device/command", json=payload)
        conflict = client.post("/api/v1/device/command", json={**payload, "params": {"changed": True}})
        ecs_mock_server.runtime_states["ROBOT-ARM-01"].status = "IDLE"
        accepted = client.post("/api/v1/device/command", json={**payload, "trace_id": "TRACE-RETRY"})

    assert rejected.status_code == 429
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"
    assert accepted.status_code == 200
    assert accepted.json()["trace_id"] == "TRACE-RETRY"
    assert len(CapturingAsyncClient.requests) == 1


def test_ecs_mock_status_returns_generic_device_contracts() -> None:
    with TestClient(ecs_mock_server.app) as client:
        single = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        missing = client.get("/api/v1/device/status")
        unknown = client.get("/api/v1/device/status", params={"device_code": "UNKNOWN"})

    assert single.status_code == 200
    assert single.json()["status"] == "IDLE"
    assert single.json()["contract_key"] == "arm.pick"
    assert single.headers["Cache-Control"] == "no-store"
    assert missing.status_code == 400
    assert missing.json() == {"code": 400, "message": "INVALID_ENVELOPE"}
    assert unknown.status_code == 404
    assert unknown.json() == {"code": 404, "message": "DEVICE_NOT_FOUND"}


def test_ecs_mock_status_rejects_invalid_device_token() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status", params={"device_code": "BAD@TOKEN"})
    assert response.status_code == 400
    assert response.json()["message"] == "INVALID_ENVELOPE"


def test_default_ecs_mock_implements_uniform_command_status_and_callback_wire(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    command = {
        "device_code": "ROBOT-ARM-01",
        "command_code": "CMD-UNIFORM-001",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "task_type": "PICK_AND_PLACE",
        "timestamp": 1_786_579_200_000,
        "params": {"object_key": "PKG-001"},
        "trace_id": "TRACE-UNIFORM-001",
    }

    with TestClient(ecs_mock_server.app) as client:
        status = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        ack = client.post("/api/v1/device/command", json=command)

    assert status.json() == {
        "device_code": "ROBOT-ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": status.json()["timestamp"],
    }
    assert ack.json() == {"code": 200, "message": "ACCEPTED", "trace_id": "TRACE-UNIFORM-001"}
    callback = CapturingAsyncClient.requests[0]["json"]
    assert callback["contract_key"] == "arm.pick"
    assert callback["contract_version"] == "2.0"
    assert callback["source_event_id"].startswith("RESULT-")
    assert len(callback["source_event_id"]) == 71
    assert callback["error_detail"] is None
    assert callback["trace_id"] == "TRACE-UNIFORM-001"


def test_ecs_mock_lists_received_commands_latest_first(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        for command_code, task_type in (("CMD-FIRST", "PICK_AND_PLACE"), ("CMD-SECOND", "MOVE")):
            response = client.post(
                "/api/v1/device/command",
                json=_command_payload(command_code, task_type),
            )
            assert response.status_code == 200
        commands = client.get("/api/v1/mock/commands", params={"device_code": "ROBOT-ARM-01"})

    assert [item["command_code"] for item in commands.json()["data"]["commands"]] == [
        "CMD-SECOND",
        "CMD-FIRST",
    ]


def test_ecs_mock_event_callbacks_generic_payload(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    payload = {
        "device_code": "CAMERA-CONVEYOR-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_780_296_263_000,
        "data": {"barcode": "PKG-001"},
    }

    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/mock/event", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["wes_http_status"] == 200
    callback = CapturingAsyncClient.requests[0]["json"]
    assert callback == {
        **payload,
        "contract_key": "camera.scan",
        "contract_version": "2.0",
        "source_event_id": callback["source_event_id"],
    }
    assert callback["source_event_id"].startswith("EVENT-")


def test_ecs_mock_event_openapi_exposes_generic_example() -> None:
    with TestClient(ecs_mock_server.app) as client:
        content = client.get("/openapi.json").json()["paths"]["/api/v1/mock/event"]["post"]["requestBody"]["content"][
            "application/json"
        ]

    assert set(content["examples"]) == {"scan_completed"}
    assert content["examples"]["scan_completed"]["value"] == {
        "device_code": "CAMERA-CONVEYOR-01",
        "event_type": "SCAN_COMPLETED",
        "data": {"barcode": "PKG-001"},
    }


@pytest.mark.parametrize(
    ("scenario", "expected_callback_result"),
    (("fail", "FAILED"), ("timeout", None)),
)
def test_ecs_mock_fault_scenario_applies_to_one_command(
    monkeypatch,
    scenario: str,
    expected_callback_result: str | None,
) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/ROBOT-ARM-01/scenario", json={"scenario": scenario})
        first = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-FAULT"),
        )
        second = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-AFTER-FAULT"),
        )

    assert scenario_response.status_code == 200
    assert first.status_code == 200 and second.status_code == 200
    results = [request["json"]["result"] for request in CapturingAsyncClient.requests]
    assert results == ([expected_callback_result, "SUCCESS"] if expected_callback_result else ["SUCCESS"])


def test_ecs_mock_generates_new_random_command_delay_for_each_command(monkeypatch) -> None:
    sleep_calls: list[float] = []
    random_delays = iter([2.25, 7.75])

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(ecs_mock_server.random, "uniform", lambda _start, _end: next(random_delays))
    monkeypatch.setattr(ecs_mock_server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", None)

    with TestClient(ecs_mock_server.app) as client:
        for suffix in ("1", "2"):
            response = client.post(
                "/api/v1/device/command",
                json=_command_payload(f"CMD-DELAYED-{suffix}"),
            )
            assert response.status_code == 200

    assert sleep_calls == [2.25, 7.75]


def test_ecs_mock_does_not_expose_command_delay_mutation_endpoint() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/devices/ROBOT-ARM-01/command-delay",
            json={"delay_seconds": 1.25},
        )

    assert response.status_code == 404
