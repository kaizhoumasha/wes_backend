"""8B：精确 Return 关联显式给定，只保存首次 SCAN4 和 FIFO，不放行设备。"""

import asyncio
import subprocess
from datetime import timedelta

import pytest
import pytest_asyncio
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.return_model import AutomaticPickingReturn
from automatic_picking.application.return_repository import ReturnRepository
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from test_returns_postgresql import components, database, pending, processor, scan3

from src.app.device.contracts import DeviceCommandRequest
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from tests.support.postgresql_heavy import run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class FixtureScan4Reader:
    """只解释测试给定关联，不为设备合同添加 Return ID 字段。"""

    async def read_in_session(self, db, *, evidence_id):
        from automatic_picking.application.return_flow import MatchedScan4

        row = await db.get(InboundEvidence, evidence_id)
        value = row.normalized_payload["data"].get("fixture")
        return MatchedScan4(evidence_id=evidence_id, **value) if value else None


def application(p, *, repository=None):
    _, flow, _, scan = components(p, repository=repository)
    flow.scan4_reader = FixtureScan4Reader()
    return scan


@pytest_asyncio.fixture
async def arrived(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    await scan3(p)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        row = await db.scalar(select(AutomaticPickingReturn))
        p.return_id = row.id
    return p


async def scan4(p, *, return_id=None, timestamp=1000, matched_timestamp=None, **changes):
    async with p.sessions.begin() as db:
        row = await db.get(AutomaticPickingReturn, return_id or p.return_id)
        if row is None:
            row = await db.get(AutomaticPickingReturn, p.return_id)
        value = {
            "return_id": return_id or row.id,
            "workline_id": row.workline_id,
            "bin_code": row.bin_code,
            "device_code": "SCAN4",
            "timestamp": timestamp,
        }
        value.update(changes)
        if matched_timestamp is not None:
            value["timestamp"] = matched_timestamp
        event_id = new_uuid7()
        payload = {
            "device_code": "SCAN4",
            "contract_key": "FIXTURE_ONLY",
            "contract_version": "1",
            "source_event_id": event_id,
            "event_type": "SCAN_COMPLETED",
            "timestamp": timestamp,
            "is_debug": False,
            "data": {"bin_code": "fixture-raw", "fixture": value},
        }
        event = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="scan4:" + event_id,
            normalized_payload=payload,
            payload_digest=normalize_payload(payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1],
            received_at=p.now,
            apply_status="APPLIED",
            workline_id=p.line.id,
            device_code="SCAN4",
            contract_key="FIXTURE_ONLY",
            contract_version="1",
        )
        db.add(event)
        await db.flush()
        return event.id


async def test_first_scan4_fact_fifo_projection_and_publication_commit_together(arrived):
    p = arrived
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    async with p.sessions() as db:
        evidence = await db.get(InboundEvidence, eid)
        assert evidence.published_at is not None
        current = await db.get(AutomaticPickingReturn, p.return_id)
        assert (current.scan4_evidence_id, current.scan4_event_time) == (eid, 1000)
        fifo = await ReturnRepository().list_fifo(db, p.line.id)
        assert [row.id for row in fifo] == [p.return_id]
        assert current.archived_at is None
        assert (await db.scalar(select(DeviceCommand))).status == "PENDING"
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        assert await ReturnRepository().list_fifo(db, 99999999) == []


async def first(p, return_id=None):
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, return_id or p.return_id)
        return row.scan4_evidence_id, row.scan4_event_time


async def advance(p):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await components(p)[2].advance_completed_in_session(db, line)


async def other_return(p, *, bin_code="A000000002", line_id=None, archived=False):
    # 独立回程的前段事实已给定，不借用真实派发；与8A现有 root 使用不同 execution/Command/Evidence。
    async with p.sessions.begin() as db:
        original = await db.get(AutomaticPickingReturn, p.return_id)
        command = await db.scalar(
            select(DeviceCommand).where(DeviceCommand.command_code == original.entry_command_code)
        )
        handle = await components(p)[0].create_command_in_session(
            db,
            DeviceCommandRequest(
                device_code=command.device_code,
                workline_id=command.workline_id,
                execution_ref_type=command.execution_ref_type,
                execution_ref_id="fixture-return-" + new_uuid7(),
                material_execution_id=None,
                contract_key=command.contract_key,
                contract_version=command.contract_version,
                task_type=command.task_type,
                params={"fixture_only": True, "bin_code": bin_code},
                deadline_at=command.deadline_at,
            ),
        )
        event = await db.get(InboundEvidence, original.scan3_evidence_id)
        clone_event = InboundEvidence(
            **{**event.model_dump(exclude={"id"}), "source_identity": "fixture-scan3:" + new_uuid7()}
        )
        db.add(clone_event)
        await db.flush()
        row = AutomaticPickingReturn(
            workline_id=line_id or p.line.id,
            bin_code=bin_code,
            source_work_id=1000000 + clone_event.id,
            entry_command_code=handle.command_code,
            scan3_evidence_id=clone_event.id,
            archived_at=p.now if archived else None,
        )
        db.add(row)
        await db.flush()
        return row.id


async def test_repeated_scans_keep_first_fact_and_order_even_with_older_rescan_time(arrived):
    p = arrived
    first_id = await scan4(p, timestamp=1000)
    assert await processor(p, scan=application(p)).process_batch() == 1
    second_id = await scan4(p, timestamp=900)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (first_id, 1000)
    assert await processor(p, scan=application(p)).process_batch() == 0
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, second_id)).published_at is not None


async def test_fifo_uses_first_scan4_time_across_tasks_not_return_id_or_received_at(arrived):
    p = arrived
    later_id = p.return_id
    earlier_id = await other_return(p)
    a = await scan4(p, timestamp=2000)
    b = await scan4(p, return_id=earlier_id, timestamp=1000)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, a)).received_at = p.now - timedelta(days=1)
        (await db.get(InboundEvidence, b)).received_at = p.now + timedelta(days=1)
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    assert await processor(p, scan=application(p)).process_batch() == 2
    async with p.sessions() as db:
        fifo = await ReturnRepository().list_fifo(db, p.line.id)
        assert [row.id for row in fifo] == [earlier_id, later_id]
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2


async def test_concurrent_processors_record_one_first_fact_without_batch_advancement(arrived):
    p = arrived
    eid = await scan4(p)
    async with asyncio.timeout(15):
        result = await asyncio.gather(
            processor(p, scan=application(p)).process_batch(),
            processor(p, scan=application(p)).process_batch(),
            advance(p),
        )
    assert sum(result) == 1
    assert await first(p) == (eid, 1000)
    assert await advance(p) == 0


@pytest.mark.parametrize("point", ["projection", "publication"])
async def test_first_fact_projection_and_publication_rollback_together(arrived, point):
    from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository

    p = arrived
    eid = await scan4(p)

    class CrashProjection(ReturnRepository):
        async def record_scan4(self, *args, **kwargs):
            await super().record_scan4(*args, **kwargs)
            if point == "projection":
                raise RuntimeError("fixture crash after projection")

    class CrashPublication(InboundEvidenceRepository):
        async def flush(self, db):
            if point == "publication" and (await db.get(InboundEvidence, eid)).published_at is not None:
                raise RuntimeError("fixture crash after publication")
            await super().flush(db)

    assert (
        await processor(
            p, scan=application(p, repository=CrashProjection()), evidences=CrashPublication()
        ).process_batch()
        == 0
    )
    assert await first(p) == (None, None)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)


async def test_worker_claim_crash_recovers_from_lease_without_notification(arrived):
    from src.app.execution.repositories import inbound_evidence_repository

    p = arrived
    eid = await scan4(p)
    async with p.sessions.begin() as db:
        rows = await inbound_evidence_repository.claim_decision_batch(
            db, now=p.now, claim_token="dead-8b", claim_expires_at=p.now + timedelta(seconds=30), limit=100
        )
        assert [row.id for row in rows] == [eid]
    assert await processor(p, scan=application(p)).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)


async def test_existing_8a_return_is_discovered_without_work_or_executing_task(arrived):
    p = arrived
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.work.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_([p.scan.id, p.result.id])))
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"

    class DiscoveryTrace(ReturnRepository):
        def __init__(self):
            self.discovered = []

        async def list_current(self, db, line_id):
            rows = await super().list_current(db, line_id)
            self.discovered.extend(row.id for row in rows)
            return rows

    repository = DiscoveryTrace()
    batch = components(p, repository=repository)[2]
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        assert await batch.advance_completed_in_session(db, line) == 0
    assert repository.discovered == [p.return_id]
    async with p.sessions() as db:
        roots = await components(p)[2].discover_returns_in_session(db, p.line)
        assert [row.id for row in roots] == [p.return_id]
        assert await ReturnRepository().list_fifo(db, p.line.id) == []
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)
    await scan4(p, timestamp=900)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert [row.id for row in await ReturnRepository().list_fifo(db, p.line.id)] == [p.return_id]
        assert (await db.scalar(select(DeviceCommand))).status == "PENDING"


async def test_same_time_on_distinct_returns_preserves_fact_and_refuses_invented_order(arrived):
    p = arrived
    other = await other_return(p)
    a = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    b = await scan4(p, return_id=other)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (a, 1000) and await first(p, other) == (b, 1000)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, b)).apply_status == "RECONCILING"
        assert await ReturnRepository().list_fifo(db, p.line.id) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"return_id": 99999999},
        {"workline_id": 9999},
        {"bin_code": "A000000099"},
        {"device_code": "SCAN3"},
        {"matched_timestamp": 999},
    ],
)
async def test_wrong_identity_cannot_bind_or_reorder_current_return(arrived, changes):
    p = arrived
    eid = await scan4(p, **changes)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (None, None)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"


async def test_missing_exact_reader_defers_and_same_evidence_recovers(arrived):
    p = arrived
    eid = await scan4(p)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        event = await db.get(InboundEvidence, eid)
        assert (
            event.apply_status == "APPLIED"
            and event.published_at is None
            and event.decision_next_attempt_at is not None
        )
    p.now += timedelta(seconds=2)
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)


@pytest.mark.parametrize("purged", [False, True])
async def test_archived_old_execution_cannot_admit_scan4_or_block_new_same_bin(arrived, purged):
    p = arrived
    old = await other_return(p, bin_code=p.work.bin_code, archived=True)
    bad = await scan4(p, return_id=old)
    if purged:
        async with p.sessions.begin() as db:
            await db.execute(delete(AutomaticPickingReturn).where(AutomaticPickingReturn.id == old))
    good = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 2
    if not purged:
        assert await first(p, old) == (None, None)
    assert await first(p) == (good, 1000)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, bad)).apply_status == "RECONCILING"
        assert [r.id for r in await ReturnRepository().list_fifo(db, p.line.id)] == [p.return_id]


async def test_no_cross_device_clock_assumption_is_added(arrived):
    p = arrived
    eid = await scan4(p, timestamp=1)  # SCAN3 fixture time=125; only SCAN4同源时间已获批可比较。
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1)


async def test_upgrade_keeps_existing_8a_return_unadmitted_and_refuses_lossy_downgrade(arrived, database):
    p = arrived
    url, _ = database
    run_alembic("downgrade", "bb07c44fb54f", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert await first(p) == (None, None)
    async with p.sessions() as db:
        assert [r.id for r in await components(p)[2].discover_returns_in_session(db, p.line)] == [p.return_id]
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "bb07c44fb54f", database_url=url)
    assert "cannot discard first SCAN4" in error.value.stderr
    assert await first(p) == (eid, 1000)


async def test_model_matches_migration_and_rejects_partial_or_reused_first_fact(arrived):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    p = arrived

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _parents: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare_to: (
                    kind != "table" or name == "automatic_picking_returns"
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingReturn.metadata)

    async with p.sessions() as db:
        assert await (await db.connection()).run_sync(compare) == []
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            (await db.get(AutomaticPickingReturn, p.return_id)).scan4_event_time = 1000
            await db.flush()
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    other = await other_return(p)
    with pytest.raises(IntegrityError):
        async with p.sessions.begin() as db:
            row = await db.get(AutomaticPickingReturn, other)
            row.scan4_evidence_id, row.scan4_event_time = eid, 1000
            await db.flush()


async def test_unpublished_first_fact_is_not_a_consumable_fifo(arrived):
    p = arrived
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, eid)).published_at = None
    async with p.sessions() as db:
        assert await ReturnRepository().list_fifo(db, p.line.id) is None
    assert await processor(p, scan=application(p)).process_batch() == 1
    assert await first(p) == (eid, 1000)
    async with p.sessions() as db:
        assert [r.id for r in await ReturnRepository().list_fifo(db, p.line.id)] == [p.return_id]


async def test_existing_first_evidence_cannot_be_reassociated_with_a_different_return(arrived):
    from dataclasses import replace

    p = arrived
    eid = await scan4(p)
    assert await processor(p, scan=application(p)).process_batch() == 1
    other = await other_return(p)

    class WrongReader(FixtureScan4Reader):
        async def read_in_session(self, db, *, evidence_id):
            value = await super().read_in_session(db, evidence_id=evidence_id)
            return replace(value, return_id=other, bin_code="A000000002")

    scan = application(p)
    scan.exit_flow.scan4_reader = WrongReader()
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, eid)).published_at = None
    assert await processor(p, scan=scan).process_batch() == 1
    assert await first(p) == (eid, 1000) and await first(p, other) == (None, None)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"
