from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_scan_device_heartbeats_marks_stale_devices_offline() -> None:
    from src.celery_app.tasks.workline import device_heartbeat_scanner

    db = SimpleNamespace(commit=AsyncMock())
    mock_device_service = SimpleNamespace(mark_stale_heartbeats_offline=AsyncMock(return_value=2))

    with patch(
        "src.app.device.services.device_service",
        mock_device_service,
    ):
        result = await device_heartbeat_scanner._scan(db, threshold_seconds=120, limit=50)

    assert result == {"scanned": 2, "marked_offline": 2}
    mock_device_service.mark_stale_heartbeats_offline.assert_awaited_once_with(
        db,
        threshold_seconds=120,
        limit=50,
        auto_commit=False,
    )
    db.commit.assert_awaited_once()
