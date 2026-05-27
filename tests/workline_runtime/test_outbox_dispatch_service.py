from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.outbox_dispatch_service import OutboxDispatchService


@pytest.mark.asyncio
async def test_dispatch_returns_empty_stats_on_zero_limit() -> None:
    service = OutboxDispatchService()
    db = AsyncMock()
    result = await service.dispatch(db, limit=0)
    assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    db.commit.assert_not_called()
