"""同一业务批次只创建一个原身份 BIN_MOVE。"""

from types import SimpleNamespace

import pytest

from src.app.execution.services import reliable_rack_transport
from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot


class _Bindings:
    def __init__(self) -> None:
        self.binding = None

    async def lock_decision_identity(self, _db, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    async def get_by_decision_identity_for_update(self, _db, **_kwargs):  # type: ignore[no-untyped-def]
        return self.binding

    async def add(self, _db, binding):  # type: ignore[no-untyped-def]
        self.binding = binding
        return binding


class _Transport:
    def __init__(self) -> None:
        self.requests = []

    async def move_bins_in_session(self, _db, client_request_id, caller, moves, *, execution_authority):  # type: ignore[no-untyped-def]
        self.requests.append((client_request_id, caller, moves, execution_authority))
        return SimpleNamespace(transport_task_id="transport-1")


@pytest.mark.asyncio
async def test_bin_creator_keeps_original_transport_identity_on_replay() -> None:
    bindings = _Bindings()
    transport = _Transport()
    creator = reliable_rack_transport.ReliableBinTransportCreator(
        transport, binding_repository=bindings, uuid_factory=lambda: "019f0000-0000-7000-8000-000000000001"
    )
    moves = (BinMove("A000000001", RackBinSlot("R1", "90", "1"), HandoffPosition("CNV0301")),)
    fields = {
        "workline_id": 7,
        "source_evidence_id": 31,
        "correlation_id": "batch-1",
        "step": "MANUAL_PICKING_INBOUND_BATCH",
        "resource_fence_id": "batch-1",
        "moves": moves,
    }

    await creator.create(object(), **fields)
    await creator.create(object(), **fields)

    assert len(transport.requests) == 2
    assert {request[0] for request in transport.requests} == {"019f0000-0000-7000-8000-000000000001"}
    assert transport.requests[0][1].workline_id == "7"
    assert transport.requests[0][2] == moves
    assert transport.requests[0][3].workline_id == 7
    with pytest.raises(ValueError, match="binding conflict"):
        await creator.create(object(), **(fields | {"source_evidence_id": 32}))
