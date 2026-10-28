from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.feed_repository import FeedRepository
from wes_plugin_sdk import wms_operations

from src.utils.canonical_json import canonical_json_digest

NOW = datetime(2026, 10, 16)


@pytest.mark.asyncio
async def test_target_recovery_precedes_current_work_filter_without_task_activation():
    target = N(advance_in_session=AsyncMock(return_value=1))
    driver = AutomaticPickingBatchDriver(
        N(list_current=AsyncMock(return_value=[])),
        N(list_recoverable=AsyncMock(return_value=[])),
        None,
        None,
        None,
        target_flow=target,
        clock=lambda: NOW,
    )
    assert await driver.advance_completed_in_session(None, N(id=7)) == 1
    target.advance_in_session.assert_awaited_once_with(None, line_id=7, now=NOW)


@pytest.mark.asyncio
async def test_material_recovery_runs_without_current_works_or_new_task_activation():
    material = N(advance_in_session=AsyncMock(return_value=1))
    driver = AutomaticPickingBatchDriver(
        N(list_current=AsyncMock(return_value=[])),
        N(list_recoverable=AsyncMock(return_value=[])),
        None,
        None,
        None,
        material_flow=material,
        clock=lambda: NOW,
    )
    assert await driver.advance_completed_in_session(None, N(id=7)) == 1
    material.advance_in_session.assert_awaited_once_with(None, line_id=7, now=NOW)


@pytest.mark.asyncio
async def test_pending_work_only_binds_original_result_before_fact_publication():
    work = N(picking_task_id=12, phase="PLAN_PENDING", operation_id="original")
    works = N(list_current=AsyncMock(return_value=[work]), bind_result_context=AsyncMock(return_value=1))
    feeds = N(list_recoverable=AsyncMock(return_value=[]))
    flow = N(
        reader=N(by_operation_id=AsyncMock(return_value=N(status="COMPLETED", evidence_id=44))),
        advance_in_session=AsyncMock(),
    )
    tasks = N(get_by_id_for_update=AsyncMock(return_value=N(id=12, workline_id=7, status="COMPLETED")))
    driver = AutomaticPickingBatchDriver(works, feeds, flow, None, None, tasks=tasks, clock=lambda: NOW)
    assert await driver.advance_completed_in_session(None, N(id=7)) == 1
    works.bind_result_context.assert_awaited_once_with(None, work, 44)
    flow.advance_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_feed_request_is_per_original_member_and_independent_of_other_rack_arrival():
    members = [
        N(id=1, cancelled_evidence_id=None, plan_revision=2, rack_id="R1", rack_face="A"),
        N(id=2, cancelled_evidence_id=None, plan_revision=3, rack_id="R2", rack_face="B"),
    ]
    feeds = N(
        source_members=AsyncMock(return_value=members),
        by_member=AsyncMock(return_value=None),
        open_for_face=AsyncMock(return_value=None),
        source_ready=AsyncMock(side_effect=[False, True]),
        channel_busy=AsyncMock(return_value=False),
        add_feed=AsyncMock(),
    )
    requests = N(create_in_session=AsyncMock())
    driver = AutomaticPickingBatchDriver(
        None,
        feeds,
        None,
        None,
        requests,
        uuid_factory=lambda: "019f3405-2200-7b01-8b01-000000000002",
        clock=lambda: NOW,
    )
    task = N(id=12, task_id="TASK-1", status="EXECUTING", plan_blocked_evidence_id=None)
    assert await driver.advance_in_session(None, N(id=7), task) == 1
    assert feeds.add_feed.call_args.kwargs["source_member_id"] == 2
    intent = requests.create_in_session.call_args.args[1]
    assert (intent.task_id, intent.rack_id, intent.rack_face) == ("TASK-1", "R2", "B")
    assert not hasattr(intent, "plan_revision")


@pytest.mark.asyncio
async def test_new_revision_reuses_unclosed_face_batch_without_new_request():
    member = N(id=2, cancelled_evidence_id=None, plan_revision=3, rack_id="R1", rack_face="A")
    original = N(source_member_id=1, operation_id="original")
    feeds = N(
        source_members=AsyncMock(return_value=[member]),
        by_member=AsyncMock(return_value=None),
        open_for_face=AsyncMock(return_value=original),
        source_ready=AsyncMock(return_value=True),
        add_feed=AsyncMock(),
    )
    requests = N(create_in_session=AsyncMock())
    driver = AutomaticPickingBatchDriver(None, feeds, None, None, requests)
    task = N(id=12, task_id="TASK-1", status="EXECUTING", plan_blocked_evidence_id=None)
    assert await driver.advance_in_session(None, N(id=7), task) == 0
    requests.create_in_session.assert_not_awaited()
    feeds.add_feed.assert_not_awaited()
    assert (original.source_member_id, original.operation_id) == (1, "original")


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "operation_id", "task_id", "rack_id", "rack_face", "source_member_id"])
async def test_feed_result_uses_frozen_batch_identity_without_request_revision(mismatch):
    values = {"operation_id": "batch", "task_id": "TASK-1", "rack_id": "R1", "rack_face": "A"}
    payload = {"operation_id": "batch", "data": {key: value for key, value in values.items() if key != "operation_id"}}
    confirmation = N(
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        status="COMPLETED",
        response_evidence_id=41,
    )
    intent = wms_operations.outbound_bin_inbound_batch(**(values | ({mismatch: "other"} if mismatch in values else {})))
    evidence = N(id=41, apply_status="APPLIED")
    member = N(picking_task_id=12, plan_revision=93, rack_id="R1", rack_face="A")
    task = N(id=12, task_id="TASK-1")
    feed = N(operation_id="batch", workline_id=7, source_member_id=23, picking_task_id=12)
    frozen_feed = N(workline_id=7, source_member_id=22 if mismatch == "source_member_id" else 23, picking_task_id=12)
    outcome = N(result=object())
    reader = N(read_inbound=AsyncMock(return_value=(intent, outcome)))
    db = N(
        scalar=AsyncMock(side_effect=[frozen_feed, confirmation]), get=AsyncMock(side_effect=[evidence, member, task])
    )
    repository = FeedRepository(reader)
    if mismatch:
        with pytest.raises(ValueError, match="frozen plan member"):
            await repository.inbound(db, feed)
    else:
        assert await repository.inbound(db, feed) == (intent, outcome.result, evidence)
        assert member.plan_revision == 93


@pytest.mark.asyncio
async def test_unknown_original_work_request_never_creates_business_retry():
    work = N(picking_task_id=12, phase="PLAN_PENDING", operation_id="original")
    works = N(list_current=AsyncMock(return_value=[work]), bind_result_context=AsyncMock())
    flow = N(
        reader=N(by_operation_id=AsyncMock(return_value=N(status="DELIVERY_UNKNOWN"))), advance_in_session=AsyncMock()
    )
    driver = AutomaticPickingBatchDriver(
        works,
        N(list_recoverable=AsyncMock(return_value=[])),
        flow,
        None,
        None,
        tasks=N(get_by_id_for_update=AsyncMock(return_value=N(id=12, workline_id=7))),
    )
    assert await driver.advance_completed_in_session(None, N(id=7)) == 0
    works.bind_result_context.assert_not_awaited()
    flow.advance_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_feed_releases_batch_recovery_without_reading_closed_confirmation():
    work = N(picking_task_id=12, phase="SKIP_WAIT")
    feeds = N(
        list_recoverable=AsyncMock(return_value=[]),
        inbound=AsyncMock(side_effect=AssertionError("closed feed must not read confirmation")),
    )
    flow = N(advance_in_session=AsyncMock(return_value=1))
    driver = AutomaticPickingBatchDriver(
        N(list_current=AsyncMock(return_value=[work])),
        feeds,
        flow,
        None,
        None,
        tasks=N(get_by_id_for_update=AsyncMock(return_value=N(id=12, workline_id=7))),
        clock=lambda: NOW,
    )
    assert await driver.advance_completed_in_session(None, N(id=7)) == 1
    feeds.inbound.assert_not_awaited()
    flow.advance_in_session.assert_awaited_once()
