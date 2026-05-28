from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.services.device_command_gateway import DeviceCommandGateway


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
