from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.drain_flow import AutomaticPickingDrainFlow, MatchedDrainBuffer
from automatic_picking.application.return_allocation_flow import MatchedReturnLocation
from automatic_picking.application.transport_readers import ReturnBufferReader


@pytest.mark.asyncio
async def test_buffer_uses_own_outlet_and_manual_batch_bound():
    line = N(position_bindings={"OUTLET": {"location_id": "OWN-OUTLET"}, "FIVE_RACK": {"capacity": 99}})
    result = await ReturnBufferReader().read_in_session(N(get=AsyncMock(return_value=line)), workline_id=8)
    assert result == MatchedDrainBuffer("OWN-OUTLET", 4)


@pytest.mark.asyncio
async def test_initial_drain_keeps_fifth_fifo_member_for_next_request():
    rows = [N(id=i, bin_code=f"A{i:09d}", allocation_id=None) for i in range(1, 6)]

    async def location(db, *, return_id):
        return MatchedReturnLocation(return_id, 8, f"A{return_id:09d}", "OWN-OUTLET")

    flow = AutomaticPickingDrainFlow(
        N(returns=N(list_fifo=AsyncMock(return_value=rows)), allocations=N(qualified=AsyncMock(return_value=True))),
        None,
        None,
        None,
        N(read_in_session=AsyncMock(return_value=MatchedDrainBuffer("OWN-OUTLET", 4))),
        N(read_in_session=location),
    )
    assert await flow._candidates(object(), N(id=8)) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_rotation_candidate_reader_leaves_existing_action_to_original_owner(monkeypatch):
    from automatic_picking.application.transport_readers import SourceRackRotationReader

    current = N(
        closed_at=object(),
        inbound_client_request_id="original-ingress",
        departure_operation_id=None,
        rotation_transport_task_id="original-rotation",
    )
    monkeypatch.setattr(
        "automatic_picking.application.transport_readers.ReturnMotionRepository.current",
        AsyncMock(return_value=[current]),
    )
    db = N(get=AsyncMock(return_value=N(is_active=True)))
    assert await SourceRackRotationReader().read_in_session(db, workline_id=8) == ()
    db.get.assert_awaited_once()
