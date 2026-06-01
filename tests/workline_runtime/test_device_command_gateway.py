import importlib
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.device.models.device import DeviceStatus
from src.app.workline.services.device_command_gateway import DeviceCommandGateway

command_repository_module = importlib.import_module("src.app.device.repositories.command_repository")
gateway_module = importlib.import_module("src.app.workline.services.device_command_gateway")


class FakeAckResponse:
    status_code = 200
    text = ""


class CapturingAsyncClient:
    requests: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict):
        self.requests.append({"url": url, "json": json})
        return FakeAckResponse()


class NullCommandRepository:
    async def get_by_command_code(self, _db, _command_code):
        return None


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
            "url": "http://mock_ecs:8010/api/v1/device/command",
            "json": {
                "command_code": "CMD-GW-001",
                "task_type": "MOVE_FORWARD",
                "params": {"slot": "A01"},
                "device_code": "RS-CONVEYOR-01",
            },
        }
    ]
