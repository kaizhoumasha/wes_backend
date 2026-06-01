import importlib
from typing import ClassVar

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
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"code": 200, "message": "OK"}


def setup_function() -> None:
    CapturingAsyncClient.requests.clear()
    ecs_mock_server.reset_mock_state()


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
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-001",
                "task_type": "MOVE_FORWARD",
                "params": {"slot": "A01"},
            },
        )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["trace_id"] == "ECS-MOCK-CMD-ECS-001"
    assert CapturingAsyncClient.requests == [
        {
            "url": ecs_mock_server.WES_RESULT_CALLBACK_URL,
            "json": {
                "command_code": "CMD-ECS-001",
                "device_code": "RS-CONVEYOR-01",
                "result": "SUCCESS",
                "finish_time": CapturingAsyncClient.requests[0]["json"]["finish_time"],
                "data": {
                    "device_code": "RS-CONVEYOR-01",
                    "task_type": "MOVE_FORWARD",
                    "accepted_params": {"slot": "A01"},
                },
            },
            "headers": CapturingAsyncClient.requests[0]["headers"],
        }
    ]
    assert CapturingAsyncClient.requests[0]["headers"]["X-App-ID"]


def test_ecs_mock_rejects_unknown_device_code() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "UNKNOWN",
                "command_code": "CMD-ECS-404",
                "task_type": "MOVE_FORWARD",
            },
        )

    assert response.status_code == 400
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_rejects_command_for_event_only_device(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "CAMERA-CONVEYOR-01",
                "command_code": "CMD-ECS-WRONG-DEVICE",
                "task_type": "MOVE_FORWARD",
            },
        )

    assert response.status_code == 400
    assert "Unsupported task_type" in response.json()["detail"]
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_pick_and_put_success_callback_contains_measurement(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK",
                "task_type": "PICK_AND_PUT",
                "params": {"business_key": "PKG-CAP001-LOT-A-001"},
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["reel_diameter"] == "178.0"
    assert callback_data["reel_thickness"] == "15.0"
    assert callback_data["measurement_result"] == "OK"


def test_ecs_mock_rejects_event_for_command_only_device(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json={
                "device_code": "RS-CONVEYOR-01",
                "event_type": "SCAN_COMPLETED",
                "data": {"barcode": "PKG-001"},
            },
        )

    assert response.status_code == 400
    assert "Unsupported event_type" in response.json()["detail"]
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_callbacks_failed_result_for_fail_scenario(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/RS-CONVEYOR-01/scenario", json={"scenario": "fail"})
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-FAIL",
                "task_type": "MOVE_FORWARD",
            },
        )

    assert scenario_response.status_code == 200
    assert response.status_code == 200
    assert CapturingAsyncClient.requests[0]["json"]["result"] == "FAILED"
    assert CapturingAsyncClient.requests[0]["json"]["error_detail"] == {
        "code": "ECS_MOCK_SCENARIO_FAILED",
        "message": "ECS Mock 故障注入失败",
    }


def test_ecs_mock_fail_scenario_preserves_command_error_code(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/RS-INPUT-ARM-01/scenario", json={"scenario": "fail"})
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-FAIL",
                "task_type": "PICK_AND_PUT",
                "params": {"error_code": "PICK_FAILED"},
            },
        )

    assert scenario_response.status_code == 200
    assert response.status_code == 200
    assert CapturingAsyncClient.requests[0]["json"]["error_detail"]["code"] == "PICK_FAILED"


def test_ecs_mock_fail_scenario_only_applies_to_next_command(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/RS-CONVEYOR-01/scenario", json={"scenario": "fail"})
        first_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-FAIL-ONCE",
                "task_type": "MOVE_FORWARD",
            },
        )
        second_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-SUCCESS-AFTER-FAIL",
                "task_type": "MOVE_FORWARD",
            },
        )
        status_response = client.get("/api/v1/device/status", params={"device_code": "RS-CONVEYOR-01"})

    assert scenario_response.status_code == 200
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert [request["json"]["result"] for request in CapturingAsyncClient.requests] == ["FAILED", "SUCCESS"]
    assert status_response.json()["state"]["scenario"] == "success"


def test_ecs_mock_timeout_scenario_acknowledges_without_callback(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/RS-CONVEYOR-01/scenario", json={"scenario": "timeout"})
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-TIMEOUT",
                "task_type": "MOVE_FORWARD",
            },
        )

    assert scenario_response.status_code == 200
    assert response.status_code == 200
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_timeout_scenario_only_applies_to_next_command(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        scenario_response = client.post("/api/v1/mock/devices/RS-CONVEYOR-01/scenario", json={"scenario": "timeout"})
        first_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-TIMEOUT-ONCE",
                "task_type": "MOVE_FORWARD",
            },
        )
        second_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-SUCCESS-AFTER-TIMEOUT",
                "task_type": "MOVE_FORWARD",
            },
        )
        status_response = client.get("/api/v1/device/status", params={"device_code": "RS-CONVEYOR-01"})

    assert scenario_response.status_code == 200
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert [request["json"]["command_code"] for request in CapturingAsyncClient.requests] == [
        "CMD-ECS-SUCCESS-AFTER-TIMEOUT"
    ]
    assert CapturingAsyncClient.requests[0]["json"]["result"] == "SUCCESS"
    assert status_response.json()["state"]["scenario"] == "success"
