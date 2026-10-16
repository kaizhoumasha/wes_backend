from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest

from src.app.execution.plugin_binding import BusinessEvidenceDisposition


@pytest.mark.parametrize(
    "role,raw,expected",
    [
        ("SCAN1", "A123456789-B", "A123456789"),
        ("SCAN2", "A123456789-C", "A123456789"),
        ("SCAN1", "A123456789-C", None),
        ("SCAN2", "A123456789-B", None),
        ("SCAN2", "A１２３４５６７８９-C", None),
        ("SCAN2", "A123456789", None),
        ("SCAN1", " A123456789-B", None),
        ("SCAN2", None, None),
    ],
)
def test_scan_uses_confirmed_orientation_without_loosening_identity(role, raw, expected):
    from automatic_picking.application.scan_flow import normal_bin_code

    assert normal_bin_code(raw, role) == expected


@pytest.mark.asyncio
async def test_scan2_admits_its_own_bin_without_scan1_or_fifo_gate():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    feed = N(id=8)
    task = N(id=9)
    entry = N(work_admitted=False)
    feeds = N(
        admission_ready=AsyncMock(return_value=True),
        entry=AsyncMock(return_value=None),
        scan_candidates=AsyncMock(return_value=[feed]),
        record_entry=AsyncMock(return_value=entry),
    )
    works = N(current_work=AsyncMock(return_value=None))
    flow = N(repository=works, admit_in_session=AsyncMock(return_value=N(id=10)))
    consumer = AutomaticPickingScanFlow(
        works, feeds, flow, None, tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=task))
    )
    result = await consumer.apply_scan_in_session(
        None,
        line=N(id=7),
        evidence=N(id=30),
        role="SCAN2",
        raw="A123456789-C",
        timestamp=123,
        now=datetime(2026, 10, 16),
    )
    assert result == "WORK_ADMITTED"
    assert flow.admit_in_session.call_args.kwargs["scanned_at"] == 123
    assert flow.admit_in_session.call_args.kwargs["bin_code"] == "A123456789"
    feeds.record_entry.assert_awaited_once()
    assert entry.work_admitted


@pytest.mark.asyncio
async def test_unexpected_bin_leaves_evidence_without_work_or_command():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    flow = N(repository=N(current_work=AsyncMock(return_value=None)), admit_in_session=AsyncMock())
    feeds = N(
        admission_ready=AsyncMock(return_value=True),
        scan_candidates=AsyncMock(return_value=[]),
        record_entry=AsyncMock(),
    )
    commands = N(create_command_in_session=AsyncMock())
    consumer = AutomaticPickingScanFlow(
        flow.repository,
        feeds,
        flow,
        commands,
        tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=N(id=9))),
    )
    result = await consumer.apply_scan_in_session(
        None,
        line=N(id=7),
        evidence=N(id=30),
        role="SCAN2",
        raw="A123456789-C",
        timestamp=123,
        now=datetime(2026, 10, 16),
    )
    assert result == "RECONCILING"
    flow.admit_in_session.assert_not_awaited()
    commands.create_command_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_open_work_rescan_does_not_depend_on_parent_executing_state():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    works = N(current_work=AsyncMock(return_value=N(id=10)))
    tasks = N(get_executing_for_workline_for_update=AsyncMock())
    flow = N(admit_in_session=AsyncMock())
    consumer = AutomaticPickingScanFlow(works, None, flow, None, tasks=tasks)
    assert (
        await consumer.apply_scan_in_session(
            None,
            line=N(id=7),
            evidence=N(id=30),
            role="SCAN2",
            raw="A123456789-C",
            timestamp=124,
            now=datetime(2026, 10, 16),
        )
        == "WORK_RESCAN"
    )
    tasks.get_executing_for_workline_for_update.assert_not_awaited()
    flow.admit_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan1_waits_for_matching_transport_fact_before_forward_command():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    feed = N(id=8)
    feeds = N(
        admission_ready=AsyncMock(return_value=True),
        scan_candidates=AsyncMock(return_value=[feed]),
        bin_delivered=AsyncMock(return_value=False),
        entry=AsyncMock(return_value=None),
        record_entry=AsyncMock(),
    )
    works = N(current_work=AsyncMock(return_value=None))
    commands = N(create_command_in_session=AsyncMock())
    consumer = AutomaticPickingScanFlow(
        works, feeds, None, commands, tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=N(id=9)))
    )
    assert (
        await consumer.apply_scan_in_session(
            None,
            line=N(id=7),
            evidence=N(id=30),
            role="SCAN1",
            raw="A123456789-B",
            timestamp=123,
            now=datetime(2026, 10, 16),
        )
        == "WAIT_FOR_FEED"
    )
    commands.create_command_in_session.assert_not_awaited()
    feeds.record_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan1_replay_after_segment_closed_keeps_original_command():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    feeds = N(
        admission_ready=AsyncMock(return_value=True),
        scan_candidates=AsyncMock(return_value=[N(id=8)]),
        entry=AsyncMock(return_value=N(scan1_command_code="original-command")),
        bin_delivered=AsyncMock(return_value=False),
        record_entry=AsyncMock(),
    )
    commands = N(create_command_in_session=AsyncMock())
    consumer = AutomaticPickingScanFlow(
        N(current_work=AsyncMock(return_value=None)),
        feeds,
        None,
        commands,
        tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=N(id=9))),
    )
    assert (
        await consumer.apply_scan_in_session(
            None,
            line=N(id=7),
            evidence=N(id=31),
            role="SCAN1",
            raw="A123456789-B",
            timestamp=124,
            now=datetime(2026, 10, 16),
        )
        == "ENTRY_RECORDED"
    )
    commands.create_command_in_session.assert_not_awaited()
    feeds.bin_delivered.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan1_creates_one_bound_forward_command_and_rescan_reuses_it():
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
    from automatic_picking.definition import DEFINITION

    entry = N(scan1_command_code=None)
    feeds = N(
        admission_ready=AsyncMock(return_value=True),
        scan_candidates=AsyncMock(return_value=[N(id=8)]),
        entry=AsyncMock(return_value=entry),
        bin_delivered=AsyncMock(return_value=True),
        record_entry=AsyncMock(return_value=entry),
    )
    commands = N(create_command_in_session=AsyncMock(return_value=N(command_code="CMD-1")))
    worklines = N(
        get_binding_for_command_creation=AsyncMock(
            return_value=N(contract_key="roller", contract_version="1", command_timeout_ms=1000)
        )
    )
    line = N(
        id=7, config={"device_bindings": {role.role_key: "BOUND-" + role.role_key for role in DEFINITION.device_roles}}
    )
    consumer = AutomaticPickingScanFlow(
        N(current_work=AsyncMock(return_value=None)),
        feeds,
        None,
        commands,
        tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=N(id=9))),
        worklines=worklines,
    )
    for evidence_id in (30, 31):
        assert (
            await consumer.apply_scan_in_session(
                None,
                line=line,
                evidence=N(id=evidence_id),
                role="SCAN1",
                raw="A123456789-B",
                timestamp=123,
                now=datetime(2026, 10, 16),
            )
            == "ENTRY_RECORDED"
        )
    commands.create_command_in_session.assert_awaited_once()
    request = commands.create_command_in_session.call_args.args[1]
    assert (request.device_code, request.task_type, request.execution_ref_id) == (
        "BOUND-SCAN1",
        "MOVE_FORWARD",
        "automatic-picking:30:SCAN1",
    )
    assert entry.scan1_command_code == "CMD-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,suffix", [("SCAN1", "-B"), ("SCAN2", "-C")])
async def test_cancelled_submitted_bin_waits_for_original_terminal_then_resumes(role, suffix):
    import wes_plugin_sdk as sdk
    from automatic_picking.application.feed_repository import FeedRepository
    from automatic_picking.application.scan_flow import AutomaticPickingScanFlow

    feed = N(id=8, picking_task_id=9, response_evidence_id=31, source_member_id=11)
    entry = N(work_admitted=False, scan1_command_code="original-command")

    class Feeds(FeedRepository):
        delivered = False

        async def list_admission_pending(self, db, line_id):
            return [feed]

        async def entry(self, db, feed_id, code):
            return None

        async def record_entry(self, db, feed, code, evidence_id):
            return entry

        async def inbound(self, db, feed):
            return (
                None,
                sdk.BinInboundBatchReady(
                    (sdk.BinInboundBatchMember("A123456789", sdk.TransportRackBinSlot("R1", "A", "1")),)
                ),
                N(published_at=datetime(2026, 10, 16)),
            )

        async def bin_delivered(self, db, feed, code):
            return self.delivered

        async def delivery_pending(self, db, feed, code):
            return not self.delivered

    from automatic_picking.definition import DEFINITION

    from src.app.execution.models import InboundEvidence

    feeds = Feeds()
    line = N(
        id=7,
        is_active=True,
        plugin_key=DEFINITION.plugin_key,
        plugin_version=DEFINITION.plugin_version,
        config={"device_bindings": {r.role_key: r.role_key for r in DEFINITION.device_roles}},
    )
    evidence = N(
        id=30,
        workline_id=7,
        kind="DEVICE_EVENT",
        apply_status="APPLIED",
        device_code=role,
        normalized_payload={
            "device_code": role,
            "contract_key": "TEST-SCAN",
            "contract_version": "1",
            "source_event_id": "original-scan",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 123,
            "is_debug": False,
            "data": {"bin_code": "A123456789" + suffix},
        },
    )
    db = N(
        get=AsyncMock(
            side_effect=lambda model, *_args, **_kwargs: (
                evidence if model is InboundEvidence else N(cancelled_evidence_id=99)
            )
        )
    )
    works = N(current_work=AsyncMock(return_value=None))
    work_flow = N(admit_in_session=AsyncMock())
    consumer = AutomaticPickingScanFlow(
        works,
        feeds,
        work_flow,
        None,
        tasks=N(get_executing_for_workline_for_update=AsyncMock(return_value=N(id=9))),
        worklines=N(get_for_authority_update=AsyncMock(return_value=line)),
    )
    application = await consumer.apply_in_session(db, 30, workline_id=7)
    assert application.disposition == "DEFERRED" and application.retry_after_ms == 1000
    work_flow.admit_in_session.assert_not_awaited()
    feeds.delivered = True
    result = await consumer.apply_scan_in_session(
        db,
        line=N(id=7),
        evidence=N(id=30),
        role=role,
        raw="A123456789" + suffix,
        timestamp=123,
        now=datetime(2026, 10, 16),
    )
    assert result == ("ENTRY_RECORDED" if role == "SCAN1" else "WORK_ADMITTED")
