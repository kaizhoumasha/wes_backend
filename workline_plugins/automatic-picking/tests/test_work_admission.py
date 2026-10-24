from datetime import datetime, timedelta
from types import SimpleNamespace as N

import pytest
import wes_plugin_sdk as sdk

NOW = datetime(2026, 10, 16)


class Requests:
    def __init__(self):
        self.calls = []

    async def create_in_session(self, db, intent, **owner):
        self.calls.append((intent, owner))


class Results:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def by_operation_id(self, db, **identity):
        return self.snapshot


class WorkRows:
    def __init__(self):
        self.row = None

    async def current_work(self, db, line_id, bin_code):
        return self.row

    async def add_work(self, db, **values):
        self.row = N(id=4, response_evidence_id=None, authorization_evidence_id=None, retry_at=None, **values)
        return self.row


def make_flow(snapshot=None):
    from automatic_picking.application.flow import AutomaticPickingWorkFlow

    requests, rows = Requests(), WorkRows()
    flow = AutomaticPickingWorkFlow(
        rows, requests, Results(snapshot), uuid_factory=lambda: "019f3405-2200-7b01-8b01-000000000002"
    )
    return flow, rows, requests


@pytest.mark.asyncio
async def test_scan2_freezes_original_operation_and_deduplicates_rescan():
    flow, _rows, requests = make_flow()
    task, feed = N(id=12, task_id="TASK-1"), N(id=3)
    first = await flow.admit_in_session(
        None, line_id=7, task=task, feed=feed, bin_code="A000000001", evidence_id=31, scanned_at=123, now=NOW
    )
    again = await flow.admit_in_session(
        None, line_id=7, task=task, feed=feed, bin_code="A000000001", evidence_id=32, scanned_at=124, now=NOW
    )
    assert first is again and first.scan2_evidence_id == 31
    intent, owner = requests.calls[0]
    assert len(requests.calls) == 1 and owner["picking_task_id"] == 12
    assert intent.scanned_at == 123 and intent.bin_code == "A000000001"


@pytest.mark.parametrize(
    "result,phase",
    [
        (sdk.BinWorkPlanReady("CELL-1", 1, 2, 1, 20.0, 15.0), "READY"),
        (sdk.BinWorkPlanNoWork(), "EXIT_PENDING"),
        (sdk.BinWorkPlanWait(1000), "WAIT"),
    ],
)
@pytest.mark.asyncio
async def test_committed_result_is_recovered_by_new_flow_without_notification(result, phase):
    flow, _rows, requests = make_flow()
    task, feed = N(id=12, task_id="TASK-1"), N(id=3)
    work = await flow.admit_in_session(
        None, line_id=7, task=task, feed=feed, bin_code="A000000001", evidence_id=31, scanned_at=123, now=NOW
    )
    snapshot = N(
        status="COMPLETED",
        intent=requests.calls[0][0],
        outcome=sdk.BinWorkPlanOutcome(result),
        evidence_id=41,
        completed_at=NOW,
    )
    from automatic_picking.application.flow import AutomaticPickingWorkFlow

    restarted = AutomaticPickingWorkFlow(_rows, requests, Results(snapshot))
    assert await restarted.advance_in_session(None, task=task, work=work, now=NOW) == 1
    assert work.phase == phase and work.response_evidence_id == 41
    assert work.authorization_evidence_id == (41 if phase == "READY" else None)
    assert len(requests.calls) == 1


@pytest.mark.asyncio
async def test_wait_uses_one_new_identity_only_after_persisted_due_time():
    flow, _rows, requests = make_flow()
    task = N(id=12, task_id="TASK-1")
    work = await flow.admit_in_session(
        None, line_id=7, task=task, feed=N(id=3), bin_code="A000000001", evidence_id=31, scanned_at=123, now=NOW
    )
    work.phase, work.retry_at, work.operation_id = (
        "WAIT",
        NOW + timedelta(seconds=1),
        "019f3405-2200-7b01-8b01-000000000001",
    )
    assert await flow.advance_in_session(None, task=task, work=work, now=NOW) == 0
    assert await flow.advance_in_session(None, task=task, work=work, now=work.retry_at) == 1
    assert work.phase == "PLAN_PENDING" and len(requests.calls) == 2
    assert requests.calls[-1][0].scanned_at == 123
    await flow.advance_in_session(None, task=task, work=work, now=NOW + timedelta(seconds=2))
    assert len(requests.calls) == 2
