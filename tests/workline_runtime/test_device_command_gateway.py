import importlib
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.device.models.device import DeviceStatus
from src.app.workline.services.device_command_gateway import DeviceCommandGateway, _DeviceCommandGovernanceError

command_repository_module = importlib.import_module("src.app.device.repositories.command_repository")
gateway_module = importlib.import_module("src.app.workline.services.device_command_gateway")


class FakeAckResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}


class CapturingAsyncClient:
    requests: ClassVar[list[dict]] = []
    status_response: ClassVar[object] = FakeAckResponse()
    status_side_effect: ClassVar[Exception | None] = None
    post_side_effect: ClassVar[Exception | None] = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs):
        self.requests.append({"method": "GET", "url": url, "timeout": kwargs.get("timeout")})
        if self.status_side_effect is not None:
            raise self.status_side_effect
        return self.status_response

    async def post(self, url: str, *, json: dict, **kwargs):
        self.requests.append({"method": "POST", "url": url, "json": json, "timeout": kwargs.get("timeout")})
        if self.post_side_effect is not None:
            raise self.post_side_effect
        return FakeAckResponse()


class FakeStatusResponse:
    def __init__(self, payload: object, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class NullCommandRepository:
    async def get_by_command_code(self, _db, _command_code):
        return None


class SameSessionCommandRepository:
    async def get_by_command_code(self, _db, _command_code):
        return SimpleNamespace(
            id=321,
            status=None,
            sent_at=None,
            ack_received_at=None,
            ack_code=None,
            ack_message=None,
        )


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_missing_config() -> None:
    gateway = DeviceCommandGateway()
    db = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = None
    db.execute.return_value = query_result
    outbox = type("Outbox", (), {"target_code": "MISSING_CONFIG"})()
    success = await gateway.dispatch(db, outbox)
    assert success is False


@pytest.mark.asyncio
async def test_dispatch_adds_top_level_device_code_to_command_body(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(
            return_value=type(
                "Device",
                (),
                {
                    "id": 100,
                    "device_code": "RS-CONVEYOR-01",
                    "host": "mock_ecs",
                    "port": 8010,
                    "protocol": "HTTP",
                    "callback_path": "/api/v1/device/command",
                    "device_status": DeviceStatus.IDLE,
                    "current_command_id": None,
                    "maintenance_mode": False,
                    "capabilities_json": {"supports_command_types": ["MOVE_FORWARD"]},
                },
            )()
        ),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-001",
            "payload_json": {
                "command_code": "CMD-GW-001",
                "task_type": "MOVE_FORWARD",
                "params": {"slot": "A01"},
            },
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert CapturingAsyncClient.requests == [
        {
            "method": "GET",
            "url": "http://mock_ecs:8010/api/v1/device/status?device_code=RS-CONVEYOR-01",
            "timeout": 2.0,
        },
        {
            "method": "POST",
            "url": "http://mock_ecs:8010/api/v1/device/command",
            "json": {
                "command_code": "CMD-GW-001",
                "task_type": "MOVE_FORWARD",
                "params": {"slot": "A01"},
                "device_code": "RS-CONVEYOR-01",
            },
            "timeout": 10.0,
        },
    ]


def _dispatchable_device() -> object:
    return type(
        "Device",
        (),
        {
            "id": 100,
            "device_code": "RS-CONVEYOR-01",
            "host": "mock_ecs",
            "port": 8010,
            "protocol": "HTTP",
            "callback_path": "/api/v1/device/command",
            "device_status": DeviceStatus.IDLE,
            "current_command_id": None,
            "maintenance_mode": False,
            "capabilities_json": {"supports_command_types": ["MOVE_FORWARD"]},
        },
    )()


@pytest.mark.parametrize(
    ("status_response", "side_effect"),
    [
        (FakeStatusResponse({}, status_code=503, text="not ready"), None),
        (FakeStatusResponse(ValueError("bad json")), None),
        (FakeStatusResponse({"state": {"status": "IDLE"}}), None),
        (
            FakeStatusResponse({"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}),
            httpx.TimeoutException("status timeout"),
        ),
        (
            FakeStatusResponse({"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}),
            httpx.ConnectError("status connection failed"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_realtime_status_unavailable_raises_precheck_wait_error(
    monkeypatch,
    status_response: FakeStatusResponse,
    side_effect: Exception | None,
) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = status_response
    CapturingAsyncClient.status_side_effect = side_effect
    CapturingAsyncClient.post_side_effect = None
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(return_value=_dispatchable_device()),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-STATUS",
            "payload_json": {"command_code": "CMD-GW-STATUS", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
        await gateway.dispatch(db, outbox)

    assert exc_info.value.code == "DEVICE_STATUS_PRECHECK_WAIT"
    assert exc_info.value.device_id == 100
    assert exc_info.value.device_code == "RS-CONVEYOR-01"
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET"]
    assert CapturingAsyncClient.requests[0]["timeout"] == 2.0
    detail = exc_info.value.detail
    assert detail["device_code"] == "RS-CONVEYOR-01"
    assert detail["status_url"] == "http://mock_ecs:8010/api/v1/device/status?device_code=RS-CONVEYOR-01"
    assert "error_kind" in detail


@pytest.mark.parametrize(
    ("status_response", "expected_mode", "expected_status", "expected_current_command_id"),
    [
        (
            FakeStatusResponse({"state": {"mode": "MANUAL", "status": "IDLE", "current_command_id": None}}),
            "MANUAL",
            "IDLE",
            None,
        ),
        (
            FakeStatusResponse({"state": {"mode": "AUTO", "status": "RUNNING", "current_command_id": None}}),
            "AUTO",
            "RUNNING",
            None,
        ),
        (
            FakeStatusResponse({"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": "CMD-OTHER"}}),
            "AUTO",
            "IDLE",
            "CMD-OTHER",
        ),
    ],
)
@pytest.mark.asyncio
async def test_realtime_status_busy_raises_device_busy_governance_error(
    monkeypatch,
    status_response: FakeStatusResponse,
    expected_mode: str,
    expected_status: str,
    expected_current_command_id: str | None,
) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = status_response
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(return_value=_dispatchable_device()),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-STATUS",
            "payload_json": {"command_code": "CMD-GW-STATUS", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
        await gateway.dispatch(db, outbox)

    assert exc_info.value.code == "DEVICE_BUSY"
    assert exc_info.value.device_id == 100
    assert exc_info.value.device_code == "RS-CONVEYOR-01"
    detail = exc_info.value.detail
    assert detail["device_code"] == "RS-CONVEYOR-01"
    assert detail["observed_mode"] == expected_mode
    assert detail["observed_status"] == expected_status
    assert detail["observed_current_command_id"] == expected_current_command_id
    assert f"mode={expected_mode}" in str(exc_info.value)
    assert f"status={expected_status}" in str(exc_info.value)
    assert f"current_command_id={expected_current_command_id}" in str(exc_info.value)
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET"]
    assert CapturingAsyncClient.requests[0]["timeout"] == 2.0


@pytest.mark.asyncio
async def test_realtime_status_same_command_busy_uses_governance_error_without_post(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": "CMD-GW-STATUS"}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(return_value=_dispatchable_device()),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-STATUS",
            "payload_json": {"command_code": "CMD-GW-STATUS", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
        await gateway.dispatch(db, outbox)

    assert exc_info.value.code == "DEVICE_BUSY"
    assert exc_info.value.detail["observed_current_command_id"] == "CMD-GW-STATUS"
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET"]


@pytest.mark.asyncio
async def test_realtime_status_ready_allows_command_dispatch(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(return_value=_dispatchable_device()),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-READY",
            "payload_json": {"command_code": "CMD-GW-READY", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_realtime_status_ready_ignores_stale_local_occupancy_projection(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    stale_device = _dispatchable_device()
    stale_device.device_status = DeviceStatus.RUNNING
    stale_device.current_command_id = 999
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=stale_device))
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-READY",
            "payload_json": {"command_code": "CMD-GW-READY", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale_status", "maintenance_mode"),
    [
        (DeviceStatus.OFFLINE, False),
        (DeviceStatus.ERROR, False),
        (DeviceStatus.MAINTENANCE, False),
        (DeviceStatus.MAINTENANCE, True),
    ],
)
async def test_realtime_status_ready_ignores_stale_local_runtime_status(
    monkeypatch, stale_status: DeviceStatus, maintenance_mode: bool
) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    stale_device = _dispatchable_device()
    stale_device.device_status = stale_status
    stale_device.error_code = stale_status.value
    stale_device.maintenance_mode = maintenance_mode
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=stale_device))
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-READY",
            "payload_json": {"command_code": "CMD-GW-READY", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_realtime_status_ready_ignores_same_session_local_command_projection(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    same_command_device = _dispatchable_device()
    same_command_device.device_status = DeviceStatus.RUNNING
    same_command_device.current_command_id = 321
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=same_command_device))
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", SameSessionCommandRepository)
    monkeypatch.setattr("src.app.device.services.device_service.mark_command_dispatched", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.app.workline.services.runtime_reconciliation_service."
        "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
        AsyncMock(return_value=True),
    )

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-READY",
            "payload_json": {"command_code": "CMD-GW-READY", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_dispatch_realtime_status_uses_capability_override(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    device = _dispatchable_device()
    device.capabilities_json = {
        "supports_command_types": ["MOVE_FORWARD"],
        "status_path": "/vendor/status",
    }
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=device))
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-STANDARD-STATUS",
            "payload_json": {"command_code": "CMD-GW-STANDARD-STATUS", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert CapturingAsyncClient.requests[0]["url"] == ("http://mock_ecs:8010/vendor/status?device_code=RS-CONVEYOR-01")


@pytest.mark.asyncio
async def test_dispatch_command_post_timeout_after_realtime_status_uses_ack_timeout(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = httpx.TimeoutException("ack timeout")
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=_dispatchable_device())
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "RS-CONVEYOR-01",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-GW-ACK-TIMEOUT",
            "payload_json": {"command_code": "CMD-GW-ACK-TIMEOUT", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    with pytest.raises(RuntimeError, match="OUTBOX_ACK_TIMEOUT"):
        await gateway.dispatch(db, outbox)

    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]
    assert CapturingAsyncClient.requests[0]["timeout"] == 2.0
    assert CapturingAsyncClient.requests[1]["timeout"] == 10.0


@pytest.mark.asyncio
async def test_dispatch_url_encodes_device_code_with_special_characters(monkeypatch) -> None:
    CapturingAsyncClient.requests.clear()
    CapturingAsyncClient.status_response = FakeStatusResponse(
        {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}
    )
    CapturingAsyncClient.status_side_effect = None
    CapturingAsyncClient.post_side_effect = None
    device = _dispatchable_device()
    # The outbox target_code or device_code contains special characters
    device.device_code = "AGV & 01 #B"
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=device))
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)

    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type(
        "Outbox",
        (),
        {
            "id": 1,
            "target_code": "AGV & 01 #B",
            "target_type": "DEVICE",
            "dispatch_key": "device-command:CMD-URL-ENCODE",
            "payload_json": {"command_code": "CMD-URL-ENCODE", "task_type": "MOVE_FORWARD"},
            "session_id": 10,
        },
    )()

    success = await gateway.dispatch(db, outbox)

    assert success is True
    assert CapturingAsyncClient.requests[0]["method"] == "GET"
    # Ensure it is properly URL-encoded (%20, %26, %23)
    assert CapturingAsyncClient.requests[0]["url"] == (
        "http://mock_ecs:8010/api/v1/device/status?device_code=AGV%20%26%2001%20%23B"
    )
    assert CapturingAsyncClient.requests[1]["timeout"] == 10.0
