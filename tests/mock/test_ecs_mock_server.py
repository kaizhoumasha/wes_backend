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
        "task_type": task_type,
        "priority": 1,
        "timeout": 30_000,
        "timestamp": 1_786_579_200_000,
        "params": {},
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
    assert response.json() == {"code": 200, "message": "Accepted"}
    callback = CapturingAsyncClient.requests[0]
    assert callback["url"] == ecs_mock_server.WES_RESULT_CALLBACK_URL
    assert callback["json"]["result"] == "SUCCESS"
    assert callback["json"]["data"] == {
        "device_code": "ROBOT-ARM-01",
        "task_type": "PICK_AND_PLACE",
        "accepted_params": {"object_key": "PKG-001"},
    }
    assert callback["headers"]["X-App-ID"]


def test_ecs_mock_supports_rough_sorter_placement_command(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json=_command_payload(
                "CMD-RS-PLACEMENT-001",
                task_type="PICK_AND_PUT",
                device_code="RS-MOCK-PLACEMENT-01",
                params={"target_code": "OUTLET-1"},
            ),
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Accepted"
    callback = CapturingAsyncClient.requests[0]["json"]
    assert callback["result"] == "SUCCESS"
    assert callback["device_code"] == "RS-MOCK-PLACEMENT-01"
    assert callback["data"]["accepted_params"] == {"target_code": "OUTLET-1"}


def test_ecs_mock_supports_first_onsite_scanner_command(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json=_command_payload(
                "CMD-STATION-SCAN1-001",
                task_type="MOVE_FORWARD",
                device_code="STATION_SCAN1",
                params={
                    "source": {
                        "location_id": "STATION_SCAN1",
                        "location_type": "SCAN_PLATFORM",
                    }
                },
            ),
        )

    assert response.status_code == 200
    callback = CapturingAsyncClient.requests[0]["json"]
    assert callback["device_code"] == "STATION_SCAN1"
    assert callback["result"] == "SUCCESS"


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
            ),
        )

    assert unknown.status_code == 404
    assert unknown.json() == {"code": 404, "message": "DEVICE_NOT_FOUND"}
    assert unsupported.status_code == 422
    assert unsupported.json() == {
        "code": 422,
        "message": "ANNEX_VALIDATION_FAILED",
    }
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_fixed_endpoints_return_closed_error_envelopes() -> None:
    with TestClient(ecs_mock_server.app) as client:
        invalid = client.post("/api/v1/device/command", json={"device_code": "ROBOT-ARM-01", "extra": True})
        mismatch = client.post(
            "/api/v1/device/command",
            json=_command_payload("CMD-MISMATCH", task_type="UNSUPPORTED"),
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
    }
    assert missing.status_code == 404
    assert missing.json() == {"code": 404, "message": "DEVICE_NOT_FOUND"}
    assert busy.status_code == 429
    assert busy.headers["Retry-After"] == "5"
    assert busy.json() == {"code": 429, "message": "CAPACITY_EXCEEDED"}


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
        {"timestamp": 1.5},
        {"priority": 11},
    ],
)
def test_ecs_mock_rejects_tokens_outside_frozen_wire(invalid: dict) -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/device/command", json={**_command_payload("CMD-TOKEN"), **invalid})
    assert response.status_code == 400


def test_ecs_mock_invalid_envelope_does_not_echo_trace_id() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={**_command_payload("CMD-INVALID-TRACE"), "trace_id": "TRACE-VALID"},
        )
    assert response.status_code == 400
    assert response.json() == {"code": 400, "message": "INVALID_ENVELOPE"}


def test_ecs_mock_accepts_frozen_wire_token_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                **_command_payload("C" * 160),
                "task_type": "P" * 160,
            },
        )
    assert response.status_code == 422
    assert response.json()["message"] == "ANNEX_VALIDATION_FAILED"


def test_ecs_mock_max_command_code_uses_result_wire_without_external_identity(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json=_command_payload("C" * 160),
        )

    assert response.status_code == 200
    result = CapturingAsyncClient.requests[0]["json"]
    assert result["command_code"] == "C" * 160
    assert "source_event_id" not in result


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
        duplicate = client.post("/api/v1/device/command", json=payload)
        conflict = client.post("/api/v1/device/command", json={**payload, "params": {"changed": True}})

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json() == {"code": 200, "message": "Accepted"}
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"
    assert len(CapturingAsyncClient.requests) == 1


def test_ecs_mock_omits_internal_trace_from_ack_and_result(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    payload = _command_payload("CMD-NO-TRACE")
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
        accepted = client.post("/api/v1/device/command", json=payload)

    assert rejected.status_code == 429
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "IDEMPOTENCY_CONFLICT"
    assert accepted.status_code == 200
    assert accepted.json() == {"code": 200, "message": "Accepted"}
    assert len(CapturingAsyncClient.requests) == 1


def test_ecs_mock_status_returns_fresh_generic_device_contracts(monkeypatch) -> None:
    ecs_mock_server.runtime_states["ROBOT-ARM-01"].updated_at = 1
    monkeypatch.setattr(ecs_mock_server, "_now_ms", lambda: 1_787_475_602_000)
    with TestClient(ecs_mock_server.app) as client:
        single = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        missing = client.get("/api/v1/device/status")
        unknown = client.get("/api/v1/device/status", params={"device_code": "UNKNOWN"})

    assert single.status_code == 200
    assert single.json()["devices"][0]["state"]["status"] == "IDLE"
    assert single.json()["devices"][0]["state"]["updated_at"] == 1_787_475_602_000
    assert "cache-control" not in single.headers
    assert missing.status_code == 200
    assert len(missing.json()["devices"]) == len(ecs_mock_server.MOCK_ECS_DEVICES)
    assert unknown.status_code == 404


def test_ecs_mock_status_rejects_invalid_device_token() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status", params={"device_code": "BAD@TOKEN"})
    assert response.status_code == 400
    assert response.json()["message"] == "INVALID_ENVELOPE"


def test_ecs_mock_scanner_status_metadata_matches_supplier_wire() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status", params={"device_code": "STATION_SCAN1"})

    assert response.status_code == 200
    device = response.json()["devices"][0]["device"]
    assert device["device_type"] == "SCANNER"
    assert device["role"] == "SCAN_STATION"


def test_default_ecs_mock_implements_whitepaper_command_and_callback_wire(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)
    command = {
        "device_code": "ROBOT-ARM-01",
        "command_code": "CMD-UNIFORM-001",
        "task_type": "PICK_AND_PLACE",
        "priority": 1,
        "timeout": 30_000,
        "timestamp": 1_786_579_200_000,
        "params": {"object_key": "PKG-001"},
    }

    with TestClient(ecs_mock_server.app) as client:
        status = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        ack = client.post("/api/v1/device/command", json=command)

    assert status.json() == {
        "devices": [
            {
                "device": {
                    "device_code": "ROBOT-ARM-01",
                    "device_name": "搬运机械臂",
                    "device_type": "ROBOTIC_ARM",
                    "role": "ROBOT_ARM",
                    "supported_commands": ["PICK_AND_PLACE", "MOVE"],
                    "supported_events": [],
                },
                "state": {
                    "device_code": "ROBOT-ARM-01",
                    "mode": "AUTO",
                    "status": "IDLE",
                    "is_online": True,
                    "current_command_code": None,
                    "scenario": "success",
                    "updated_at": status.json()["devices"][0]["state"]["updated_at"],
                },
            }
        ]
    }
    assert ack.json() == {"code": 200, "message": "Accepted"}
    callback = CapturingAsyncClient.requests[0]["json"]
    assert set(callback) == {
        "command_code",
        "device_code",
        "result",
        "finish_time",
        "data",
        "error_detail",
    }
    assert isinstance(callback["finish_time"], int)
    assert callback["error_detail"] is None


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


def test_ecs_mock_event_callbacks_preserve_device_defined_data(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    payload = {
        "device_code": "CAMERA-CONVEYOR-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_780_183_463_000,
        "data": {
            "device_defined": {"value": "opaque"},
        },
    }

    with TestClient(ecs_mock_server.app) as client:
        response = client.post("/api/v1/mock/event", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["wes_http_status"] == 200
    callback = CapturingAsyncClient.requests[0]["json"]
    assert callback == payload


def test_ecs_mock_event_openapi_keeps_business_data_opaque() -> None:
    with TestClient(ecs_mock_server.app) as client:
        content = client.get("/openapi.json").json()["paths"]["/api/v1/mock/event"]["post"]["requestBody"]["content"][
            "application/json"
        ]

    assert set(content["examples"]) == {"scan_completed"}
    assert content["examples"]["scan_completed"]["value"] == {
        "device_code": "CAMERA-CONVEYOR-01",
        "event_type": "SCAN_COMPLETED",
        "data": {"barcode": "BIN_104"},
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


@pytest.mark.parametrize(
    ("scenario", "expected"),
    (
        ("offline", {"mode": "AUTO", "status": "IDLE", "is_online": False, "current_command_code": None}),
        ("manual", {"mode": "MANUAL", "status": "IDLE", "is_online": True, "current_command_code": None}),
        (
            "busy",
            {
                "mode": "AUTO",
                "status": "RUNNING",
                "is_online": True,
                "current_command_code": "MOCK-SCENARIO-BUSY",
            },
        ),
    ),
)
def test_ecs_mock_runtime_scenarios_are_deterministic_for_preflight(scenario: str, expected: dict) -> None:
    with TestClient(ecs_mock_server.app) as client:
        configured = client.post(
            "/api/v1/mock/devices/ROBOT-ARM-01/scenario",
            json={"scenario": scenario},
        )
        status = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        reset = client.post(
            "/api/v1/mock/devices/ROBOT-ARM-01/scenario",
            json={"scenario": "success"},
        )

    assert configured.status_code == 200
    assert status.json()["devices"][0]["state"] == {
        "device_code": "ROBOT-ARM-01",
        "scenario": scenario,
        "updated_at": status.json()["devices"][0]["state"]["updated_at"],
        **expected,
    }
    assert reset.json()["data"] == {
        "device_code": "ROBOT-ARM-01",
        "mode": "AUTO",
        "status": "IDLE",
        "is_online": True,
        "current_command_code": None,
        "scenario": "success",
        "command_delay_seconds": None,
        "updated_at": reset.json()["data"]["updated_at"],
    }


def test_ecs_mock_rejects_scenario_change_while_real_command_owns_device() -> None:
    state = ecs_mock_server.runtime_states["ROBOT-ARM-01"]
    state.status = "RUNNING"
    state.current_command_code = "CMD-IN-FLIGHT"
    state.command_delay_seconds = 5.0

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/devices/ROBOT-ARM-01/scenario",
            json={"scenario": "offline"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Device has an active command: CMD-IN-FLIGHT"}
    assert state.status == "RUNNING"
    assert state.current_command_code == "CMD-IN-FLIGHT"
    assert state.scenario == "success"
    assert state.command_delay_seconds == 5.0


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
