"""通用 ECS Mock 外部设备协议测试。"""

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
            json={
                "device_code": "ROBOT-ARM-01",
                "command_code": "CMD-ECS-001",
                "task_type": "PICK_AND_PLACE",
                "params": {"object_key": "PKG-001"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"code": 200, "message": "Accepted", "trace_id": "ECS-MOCK-CMD-ECS-001"}
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
            json={"device_code": "UNKNOWN", "command_code": "CMD-404", "task_type": "PICK_AND_PLACE"},
        )
        unsupported = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "CAMERA-CONVEYOR-01",
                "command_code": "CMD-UNSUPPORTED",
                "task_type": "PICK_AND_PLACE",
            },
        )

    assert unknown.status_code == 400
    assert unsupported.status_code == 400
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_status_returns_generic_device_contracts() -> None:
    with TestClient(ecs_mock_server.app) as client:
        single = client.get("/api/v1/device/status", params={"device_code": "ROBOT-ARM-01"})
        all_devices = client.get("/api/v1/device/status")
        unknown = client.get("/api/v1/device/status", params={"device_code": "UNKNOWN"})

    assert single.status_code == 200
    assert single.json()["state"]["status"] == "IDLE"
    assert {item["device"]["device_code"] for item in all_devices.json()["devices"]} == set(
        ecs_mock_server.MOCK_ECS_DEVICES
    )
    assert unknown.status_code == 400


def test_ecs_mock_lists_received_commands_latest_first(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        for command_code, task_type in (("CMD-FIRST", "PICK_AND_PLACE"), ("CMD-SECOND", "MOVE")):
            response = client.post(
                "/api/v1/device/command",
                json={
                    "device_code": "ROBOT-ARM-01",
                    "command_code": command_code,
                    "task_type": task_type,
                },
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
    assert CapturingAsyncClient.requests[0]["json"] == payload


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
            json={
                "device_code": "ROBOT-ARM-01",
                "command_code": "CMD-FAULT",
                "task_type": "PICK_AND_PLACE",
            },
        )
        second = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "ROBOT-ARM-01",
                "command_code": "CMD-AFTER-FAULT",
                "task_type": "PICK_AND_PLACE",
            },
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
                json={
                    "device_code": "ROBOT-ARM-01",
                    "command_code": f"CMD-DELAYED-{suffix}",
                    "task_type": "PICK_AND_PLACE",
                },
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
