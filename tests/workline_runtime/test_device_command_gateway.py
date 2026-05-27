from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.device_command_gateway import DeviceCommandGateway


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_missing_config() -> None:
    gateway = DeviceCommandGateway()
    db = AsyncMock()
    outbox = type("Outbox", (), {"target_code": "MISSING_CONFIG"})()
    success = await gateway.dispatch(db, outbox)
    assert success is False
