"""drain 的 batch 接线与显式离线缓冲输入边界。"""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver


@pytest.mark.asyncio
async def test_drain_owner_is_reached_without_an_executing_task():
    batch = AutomaticPickingBatchDriver(
        N(list_current=AsyncMock(return_value=[])),
        N(list_recoverable=AsyncMock(return_value=[])),
        None,
        None,
        None,
    )
    batch.drain_flow = N(
        advance_in_session=AsyncMock(return_value=1), prepare_return_motion_in_session=AsyncMock(return_value=0)
    )
    line = N(id=7)
    assert await batch.advance_completed_in_session(object(), line) == 1
    batch.drain_flow.advance_in_session.assert_awaited_once()


@pytest.mark.parametrize("location,capacity", [("", 1), (" ", 1), (None, 1), ("BUFFER", True), ("BUFFER", 0)])
def test_buffer_location_and_capacity_must_be_explicit(location, capacity):
    from automatic_picking.application.drain_flow import MatchedDrainBuffer

    with pytest.raises(ValueError):
        MatchedDrainBuffer(location, capacity)
