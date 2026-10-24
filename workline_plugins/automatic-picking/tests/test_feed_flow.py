from datetime import datetime
from types import SimpleNamespace as N

import pytest
import wes_plugin_sdk as sdk

NOW = datetime(2026, 10, 16)


class Repo:
    def __init__(self):
        self.state = None
        self.scanned = False
        self.busy = False
        self.cancelled = False

    async def source_cancelled(self, db, feed):
        return self.cancelled

    async def chunk_state(self, db, feed):
        return self.state

    async def chunk_delivered(self, db, feed, bins):
        return True

    async def chunk_entered(self, db, feed, bins):
        return self.scanned

    async def channel_busy(self, db, line_id):
        return self.busy


class Creator:
    def __init__(self):
        self.calls = []

    async def create(self, db, **values):
        self.calls.append(values)
        return N(transport_task_id=f"T-{len(self.calls)}")


def ready():
    return sdk.BinInboundBatchReady(
        tuple(
            sdk.BinInboundBatchMember(f"A{i:09}", sdk.TransportRackBinSlot("RACK", "A", f"S{i}")) for i in range(1, 6)
        )
    )


@pytest.mark.asyncio
async def test_frozen_batch_splits_four_then_one_only_after_published_success_and_scan():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    flow = AutomaticPickingFeedFlow(repo, creator)
    feed = N(
        id=4,
        workline_id=7,
        picking_task_id=12,
        operation_id="batch-1",
        response_evidence_id=31,
        offset=0,
        transport_task_id=None,
        phase="ALLOCATED",
    )
    line = N(id=7, position_bindings={"INLET": {"location_id": "IN-1"}})
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    assert [m.bin_code for m in creator.calls[0]["moves"]] == ["A000000001", "A000000002", "A000000003", "A000000004"]
    repo.state = N(status="ACCEPTED", outcome_version=1, published_outcome_version=1)
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    repo.state = N(status="SUCCEEDED", outcome_version=2, published_outcome_version=1)
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    repo.state.published_outcome_version = 2
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    assert len(creator.calls) == 1 and feed.offset == 0
    repo.scanned = True
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    assert feed.offset == 4 and len(creator.calls) == 2
    assert [m.bin_code for m in creator.calls[1]["moves"]] == ["A000000005"]
    await flow.advance_in_session(None, line=line, feed=feed, ready=ready())
    assert feed.phase == "FEED_COMPLETE" and feed.offset == 5


@pytest.mark.asyncio
async def test_unknown_chunk_keeps_identity_and_reconciling_remains_visible():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    feed = N(
        id=4,
        workline_id=7,
        picking_task_id=12,
        operation_id="batch-1",
        response_evidence_id=31,
        offset=0,
        transport_task_id="original",
        phase="FEEDING",
    )
    repo.state = N(status="UNKNOWN", outcome_version=1, published_outcome_version=1)
    flow = AutomaticPickingFeedFlow(repo, creator)
    await flow.advance_in_session(None, line=N(id=7), feed=feed, ready=ready())
    assert creator.calls == [] and feed.transport_task_id == "original"


@pytest.mark.asyncio
async def test_cancelled_source_keeps_inflight_identity_but_does_not_start_unsubmitted_tail():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    repo.cancelled = True
    feed = N(
        id=4,
        workline_id=7,
        picking_task_id=12,
        operation_id="batch-1",
        response_evidence_id=31,
        offset=0,
        transport_task_id=None,
        phase="ALLOCATED",
    )
    flow = AutomaticPickingFeedFlow(repo, creator)
    assert await flow.advance_in_session(None, line=N(id=7), feed=feed, ready=ready()) == 1
    assert creator.calls == [] and feed.phase == "CANCELLED"


@pytest.mark.asyncio
async def test_last_chunk_finishes_on_known_published_transport_without_scan():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    repo.state = N(status="SUCCEEDED", outcome_version=2, published_outcome_version=2)
    feed = N(id=4, offset=4, transport_task_id="last", phase="FEEDING")
    assert (
        await AutomaticPickingFeedFlow(repo, creator).advance_in_session(None, line=N(id=7), feed=feed, ready=ready())
        == 1
    )
    assert feed.phase == "FEED_COMPLETE" and feed.offset == 5 and feed.transport_task_id == "last"
    assert creator.calls == []


@pytest.mark.asyncio
async def test_cancelled_inflight_chunk_waits_for_terminal_then_closes_only_unsent_tail():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    repo.cancelled = True
    feed = N(id=4, offset=0, transport_task_id="original", phase="FEEDING")
    repo.state = N(status="ACCEPTED", outcome_version=0, published_outcome_version=0)
    flow = AutomaticPickingFeedFlow(repo, creator)
    assert await flow.advance_in_session(None, line=N(id=7), feed=feed, ready=ready()) == 0
    assert feed.transport_task_id == "original" and feed.offset == 0
    repo.state = N(status="SUCCEEDED", outcome_version=1, published_outcome_version=1)
    assert await flow.advance_in_session(None, line=N(id=7), feed=feed, ready=ready()) == 1
    assert feed.phase == "CANCELLED" and feed.offset == 4 and feed.transport_task_id == "original"
    assert creator.calls == []


@pytest.mark.asyncio
async def test_definitively_rejected_chunk_exits_to_reconciliation_without_new_identity():
    from automatic_picking.application.feed_flow import AutomaticPickingFeedFlow

    repo, creator = Repo(), Creator()
    repo.state = N(status="REJECTED", outcome_version=1, published_outcome_version=1)
    feed = N(id=4, offset=0, transport_task_id="rejected-original", phase="FEEDING")
    assert (
        await AutomaticPickingFeedFlow(repo, creator).advance_in_session(None, line=N(id=7), feed=feed, ready=ready())
        == 1
    )
    assert feed.phase == "RECONCILING" and feed.transport_task_id == "rejected-original"
    assert creator.calls == []
