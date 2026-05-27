from unittest.mock import AsyncMock

import pytest

from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor


@pytest.mark.asyncio
async def test_process_batch_returns_empty_stats_on_zero_limit() -> None:
    processor = InboxBatchProcessor(write_back_service=AsyncMock())
    db = AsyncMock()
    result = await processor.process_batch(db, limit=0)
    assert result == {"processed": 0, "success": 0, "failed": 0, "skipped": 0}
    db.commit.assert_not_called()
