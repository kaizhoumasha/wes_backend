import builtins
import importlib
import importlib.util
from typing import ClassVar

from fastapi.testclient import TestClient

from src.app.runtime.orchestration.sandbox_catalog_bridge import rough_sorter_scan_completed_payload
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


def test_ecs_mock_catalog_loads_shared_catalog_without_src_package(monkeypatch) -> None:
    module_path = ecs_mock_server.DOCKER_APP_ROOT / "tests" / "mock" / "ecs_mock_catalog.py"
    spec = importlib.util.spec_from_file_location("isolated_ecs_mock_catalog", module_path)
    assert spec is not None
    assert spec.loader is not None

    original_import = builtins.__import__

    def import_without_src(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src" or name.startswith("src."):
            raise ModuleNotFoundError("No module named 'src'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_src)
    monkeypatch.syspath_prepend(str(module_path.parent))
    isolated_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_module)

    data = isolated_module.default_success_data(
        "RS-INPUT-ARM-01",
        "PICK_AND_PUT",
        {"six_in_one": {"HHPN": "IC001", "LotCode": "LOT-I"}},
    )

    assert data["reel_diameter"] == "330.0"
    assert data["reel_thickness"] == "24.0"


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


def test_ecs_mock_status_returns_single_device_runtime_contract() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status", params={"device_code": "RS-CONVEYOR-01"})

    assert response.status_code == 200
    body = response.json()
    assert body["device"]["device_code"] == "RS-CONVEYOR-01"
    assert body["state"]["device_code"] == "RS-CONVEYOR-01"
    assert body["state"]["mode"] == "AUTO"
    assert body["state"]["status"] == "IDLE"
    assert body["state"]["current_command_id"] is None


def test_ecs_mock_status_returns_all_device_runtime_contracts() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status")

    assert response.status_code == 200
    states = response.json()["devices"]
    assert {item["device"]["device_code"] for item in states} == set(ecs_mock_server.MOCK_ECS_DEVICES)
    assert all(item["state"]["mode"] == "AUTO" for item in states)
    assert all("current_command_id" in item["state"] for item in states)


def test_ecs_mock_status_rejects_unknown_device_code_with_contract_error() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status", params={"device_code": "UNKNOWN"})

    assert response.status_code == 400
    assert "Unknown device_code" in response.json()["detail"]


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


def test_ecs_mock_pick_and_put_measurement_depends_on_command_material(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-IC",
                "task_type": "PICK_AND_PUT",
                "params": {
                    "business_key": "PKG-IC001-LOT-I-001",
                    "six_in_one": {
                        "HHPN": "IC001",
                        "MfrPN": "V0003-IC-QFN",
                        "Qty": "1200",
                        "DateCode": "20260411",
                        "LotCode": "LOT-I",
                        "PkgID": "PKG-IC001-LOT-I-001",
                    },
                },
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["reel_diameter"] == "330.0"
    assert callback_data["reel_thickness"] == "24.0"
    assert callback_data["measurement_result"] == "OK"


def test_ecs_mock_pick_and_put_returns_ng_for_unknown_material(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-UNKNOWN",
                "task_type": "PICK_AND_PUT",
                "params": {
                    "business_key": "PKG-UNKNOWN-LOT-X-001",
                    "six_in_one": {
                        "HHPN": "UNKNOWN",
                        "MfrPN": "V9999-UNKNOWN",
                        "Qty": "1200",
                        "DateCode": "20260411",
                        "LotCode": "LOT-X",
                        "PkgID": "PKG-UNKNOWN-LOT-X-001",
                    },
                },
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["measurement_result"] == "NG"
    assert callback_data["measurement_error_code"] == "MATERIAL_NOT_SUPPORTED"
    assert "reel_diameter" not in callback_data
    assert "reel_thickness" not in callback_data


def test_ecs_mock_pick_and_put_returns_ng_for_known_material_unknown_lot(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-BAD-LOT",
                "task_type": "PICK_AND_PUT",
                "params": {
                    "business_key": "PKG-IC001-LOT-X-001",
                    "six_in_one": {
                        "HHPN": "IC001",
                        "MfrPN": "V0003-IC-QFN",
                        "Qty": "1200",
                        "DateCode": "20260411",
                        "LotCode": "LOT-X",
                        "PkgID": "PKG-IC001-LOT-X-001",
                    },
                },
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["measurement_result"] == "NG"
    assert callback_data["measurement_error_code"] == "MATERIAL_INVENTORY_NOT_ALLOWED"
    assert "reel_diameter" not in callback_data
    assert "reel_thickness" not in callback_data


def test_ecs_mock_pick_and_put_returns_ng_for_explicit_empty_material_fields(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-EMPTY-LOT",
                "task_type": "PICK_AND_PUT",
                "params": {
                    "business_key": "PKG-CAP001-EMPTY-LOT-001",
                    "six_in_one": {
                        "HHPN": "CAP001",
                        "MfrPN": "V0001-CAP-0402",
                        "Qty": "100",
                        "DateCode": "20260409",
                        "LotCode": "",
                        "PkgID": "PKG-CAP001-EMPTY-LOT-001",
                    },
                },
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["measurement_result"] == "NG"
    assert callback_data["measurement_error_code"] == "MATERIAL_INVENTORY_NOT_ALLOWED"
    assert "reel_diameter" not in callback_data
    assert "reel_thickness" not in callback_data


def test_ecs_mock_pick_and_put_returns_ng_for_explicit_null_six_in_one(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-PICK-NULL-SIX-IN-ONE",
                "task_type": "PICK_AND_PUT",
                "params": {
                    "business_key": "PKG-NULL-SIX-IN-ONE-001",
                    "six_in_one": None,
                },
            },
        )

    assert response.status_code == 200
    callback_data = CapturingAsyncClient.requests[0]["json"]["data"]
    assert callback_data["measurement_result"] == "NG"
    assert callback_data["measurement_error_code"] == "MATERIAL_NOT_SUPPORTED"
    assert "reel_diameter" not in callback_data
    assert "reel_thickness" not in callback_data


def test_ecs_mock_lists_received_commands_latest_first(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(ecs_mock_server, "COMMAND_EXECUTION_DELAY_SECONDS", 0)

    with TestClient(ecs_mock_server.app) as client:
        first = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "command_code": "CMD-ECS-FIRST",
                "task_type": "PICK_AND_PUT",
                "params": {"business_key": "PKG-001"},
            },
        )
        second = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-SECOND",
                "task_type": "MOVE_FORWARD",
                "params": {"business_key": "PKG-001"},
            },
        )
        response = client.get("/api/v1/mock/commands")
        filtered = client.get("/api/v1/mock/commands", params={"device_code": "RS-CONVEYOR-01"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [command["command_code"] for command in data["commands"]] == [
        "CMD-ECS-SECOND",
        "CMD-ECS-FIRST",
    ]
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["commands"][0]["task_type"] == "MOVE_FORWARD"


def test_ecs_mock_event_scan_completed_callbacks_real_payload(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    payload = {
        "device_code": "RS-INPUT-ARM-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1780296263000,
        "data": rough_sorter_scan_completed_payload()["data"],
    }

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json=payload,
        )

    assert response.status_code == 200
    response_data = response.json()["data"]
    assert response_data["wes_http_status"] == 200
    assert response_data["wes_response"] == {"code": 200, "message": "OK"}
    callback_payload = CapturingAsyncClient.requests[0]["json"]
    assert callback_payload == payload
    assert callback_payload["data"] == rough_sorter_scan_completed_payload()["data"]
    assert callback_payload["data"]["HHPN"] == "CAP001"
    assert callback_payload["data"]["LotCode"] == "LOT-A"
    assert callback_payload["data"]["PkgID"] == "PKG-CAP001-LOT-A-001"


def test_ecs_mock_event_accepts_custom_real_payload_data(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    before_ms = ecs_mock_server._now_ms()

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "event_type": "SCAN_COMPLETED",
                "data": {
                    **rough_sorter_scan_completed_payload()["data"],
                    "PkgID": "PKG-CUSTOM-001",
                    "location": "CUSTOM-INLET",
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Event delivered"
    callback_payload = CapturingAsyncClient.requests[0]["json"]
    assert response.json()["data"]["wes_http_status"] == 200
    assert before_ms <= callback_payload["timestamp"] <= ecs_mock_server._now_ms()
    callback_data = callback_payload["data"]
    assert callback_data["PkgID"] == "PKG-CUSTOM-001"
    assert callback_data["location"] == "CUSTOM-INLET"
    assert callback_data["HHPN"] == "CAP001"


def test_ecs_mock_event_storage_retry_callbacks_real_payload(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)
    payload = {
        "device_code": "RS-INPUT-ARM-01",
        "event_type": "ROUGH_SORTER_STORAGE_RETRY",
        "data": ecs_mock_server.ROUGH_SORTER_STORAGE_RETRY_DATA,
    }

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["data"]["wes_http_status"] == 200
    callback_payload = CapturingAsyncClient.requests[0]["json"]
    assert callback_payload["device_code"] == "RS-INPUT-ARM-01"
    assert callback_payload["event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert isinstance(callback_payload["timestamp"], int)
    assert callback_payload["data"]["rack_operation"]["status"] == "ARRIVED"
    assert callback_payload["data"]["active_bin_rack"]["rack_id"] == "RACK-CALLBACK"


def test_ecs_mock_event_allows_platform_start_control_event(monkeypatch) -> None:
    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", CapturingAsyncClient)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "event_type": "WORKLINE_START_REQUESTED",
                "data": {"operator": "debug"},
            },
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Event delivered"
    callback_payload = CapturingAsyncClient.requests[0]["json"]
    assert callback_payload["device_code"] == "RS-INPUT-ARM-01"
    assert callback_payload["event_type"] == "WORKLINE_START_REQUESTED"
    assert callback_payload["data"] == {"operator": "debug"}


def test_ecs_mock_event_openapi_exposes_real_callback_payload_examples() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    content = response.json()["paths"]["/api/v1/mock/event"]["post"]["requestBody"]["content"]["application/json"]
    assert set(content["examples"]) == {
        "rough_sorter_scan_completed",
        "rough_sorter_storage_retry",
    }
    scan_example = content["examples"]["rough_sorter_scan_completed"]["value"]
    assert scan_example["device_code"] == "RS-INPUT-ARM-01"
    assert scan_example["event_type"] == "SCAN_COMPLETED"
    assert scan_example["data"] == rough_sorter_scan_completed_payload()["data"]
    assert "timestamp" not in scan_example
    assert "preset" not in scan_example
    assert "data_overrides" not in scan_example

    retry_example = content["examples"]["rough_sorter_storage_retry"]["value"]
    assert retry_example["device_code"] == "RS-INPUT-ARM-01"
    assert retry_example["event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert retry_example["data"]["idempotency_key"]
    assert "timestamp" not in retry_example
    assert "preset" not in retry_example
    assert "data_overrides" not in retry_example


def test_ecs_mock_event_rejects_preset_payload() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json={
                "preset": "rough_sorter.scan_completed",
            },
        )

    assert response.status_code == 422
    assert CapturingAsyncClient.requests == []


def test_ecs_mock_event_returns_wes_callback_failure(monkeypatch) -> None:
    class RejectingAsyncClient(CapturingAsyncClient):
        async def post(self, url: str, *, json: dict, headers: dict | None = None):
            self.requests.append({"url": url, "json": json, "headers": headers or {}})
            return RejectingResponse()

    class RejectingResponse:
        status_code = 400
        text = '{"code": "DEVICE_EVENT_REJECTED", "message": "unsupported event"}'

        def json(self) -> dict:
            return {"code": "DEVICE_EVENT_REJECTED", "message": "unsupported event"}

    monkeypatch.setattr(ecs_mock_server.httpx, "AsyncClient", RejectingAsyncClient)

    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/event",
            json={
                "device_code": "RS-INPUT-ARM-01",
                "event_type": "SCAN_COMPLETED",
                "data": rough_sorter_scan_completed_payload()["data"],
            },
        )

    assert response.status_code == 200
    assert response.json()["code"] == 502
    assert response.json()["message"] == "WES callback failed"
    assert response.json()["data"]["wes_http_status"] == 400
    assert response.json()["data"]["wes_response"] == {
        "code": "DEVICE_EVENT_REJECTED",
        "message": "unsupported event",
    }


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


def test_ecs_mock_does_not_assign_command_delay_before_device_receives_command() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.get("/api/v1/device/status")

    assert response.status_code == 200
    delays = [item["state"]["command_delay_seconds"] for item in response.json()["devices"]]
    assert delays == [None for _ in delays]


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
        first_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-DELAYED-1",
                "task_type": "MOVE_FORWARD",
            },
        )
        second_response = client.post(
            "/api/v1/device/command",
            json={
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-DELAYED-2",
                "task_type": "MOVE_FORWARD",
            },
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert sleep_calls == [2.25, 7.75]
    assert [request["json"]["command_code"] for request in CapturingAsyncClient.requests] == [
        "CMD-ECS-DELAYED-1",
        "CMD-ECS-DELAYED-2",
    ]


def test_ecs_mock_does_not_expose_command_delay_mutation_endpoint() -> None:
    with TestClient(ecs_mock_server.app) as client:
        response = client.post(
            "/api/v1/mock/devices/RS-CONVEYOR-01/command-delay",
            json={"delay_seconds": 1.25},
        )

    assert response.status_code == 404
