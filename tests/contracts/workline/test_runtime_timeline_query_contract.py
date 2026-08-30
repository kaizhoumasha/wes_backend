"""WorklineTimeline 单调序号 owner 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.repositories.timeline_sequence_repository import TimelineSequenceRepository
from src.app.runtime.orchestration.services.trace.timeline_sequence_service import add_timeline_with_sequence


@pytest.mark.asyncio
async def test_timeline_sequence_repository_allocates_after_current_maximum() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 7)))

    allocated = await TimelineSequenceRepository().allocate_many(
        db,
        session_id=101,
        count=3,
        lock_already_held=True,
    )

    assert allocated == (8, 9, 10)
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeline_service_adds_preallocated_target_row_without_legacy_query_owner() -> None:
    db = SimpleNamespace(add=lambda row: added.append(row))
    timeline = SimpleNamespace(session_id=101, seq_no=None)
    added: list[object] = []

    assigned = await add_timeline_with_sequence(db, timeline, seq_no=11)

    assert assigned == 11
    assert timeline.seq_no == 11
    assert added == [timeline]
