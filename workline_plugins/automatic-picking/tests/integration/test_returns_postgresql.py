"""8A：点2映射/SCAN3精确关联只由显式离线输入给定，不派发设备。"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.feed_repository import FeedRepository
from automatic_picking.application.plugin import build_plugin
from automatic_picking.application.return_flow import AutomaticPickingReturnFlow, MatchedScan3
from automatic_picking.application.return_model import AutomaticPickingReturn
from automatic_picking.application.return_repository import ReturnRepository
from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.application.work_repository import WorkRepository
from automatic_picking.definition import DEFINITION
from sqlalchemy import delete, func, select
from test_work_and_task_progress_postgresql import database, seed, workflow

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.execution.plugin_binding import StaticPluginBinding
from src.app.execution.services import DecisionApplier, FactProcessor, MaterialExecutionService, WmsConfirmationService
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class FixtureScan3Reader:
    """只解释测试已给定的精确关联，不定义生产扫码报文。"""

    async def read_in_session(self, db, *, evidence_id):
        row = await db.get(InboundEvidence, evidence_id)
        value = row.normalized_payload["data"].get("fixture")
        return MatchedScan3(evidence_id=evidence_id, **value) if value else None


def components(p, *, repository=None, mapper=None):
    commands = DeviceCommandService(session_factory=p.sessions, clock=lambda: p.now)

    async def request(db, *, line, work, execution_ref_id):
        return DeviceCommandRequest(
            device_code="OFFLINE-SCAN2",
            workline_id=work.workline_id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=execution_ref_id,
            material_execution_id=None,
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            task_type="FIXTURE_EXIT",
            params={"fixture_only": True, "bin_code": work.bin_code},
            deadline_at=p.now + timedelta(seconds=10),
        )

    exit_flow = AutomaticPickingReturnFlow(
        repository or ReturnRepository(), commands, workflow().reader, mapper or request, FixtureScan3Reader()
    )
    batch = AutomaticPickingBatchDriver(
        WorkRepository(), FeedRepository(), workflow(), None, None, exit_flow=exit_flow, clock=lambda: p.now
    )
    scan = AutomaticPickingScanFlow(
        WorkRepository(), FeedRepository(), workflow(), commands, batch_driver=batch, exit_flow=exit_flow
    )
    return commands, exit_flow, batch, scan


def processor(p, *, scan=None, evidences=None):
    commands, _, _, default_scan = components(p)
    binding = build_plugin(scan_flow=scan or default_scan, batch_driver=None, works=WorkRepository()).runtime_binding
    return FactProcessor(
        session_factory=p.sessions,
        evidence_repository=evidences,
        plugin_binding=StaticPluginBinding((binding,)),
        decision_applier=DecisionApplier(
            device_command_service=commands,
            wms_confirmation_service=WmsConfirmationService(),
            transport_service=object(),
            material_execution_service=MaterialExecutionService(),
            clock=lambda: p.now,
        ),
        clock=lambda: p.now,
    )


@pytest_asyncio.fixture
async def pending(database):
    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    async with sessions.begin() as db:
        live = await db.get(WorkLine, line.id)
        live.is_active = True
        # 离线前提：投料已交接，本片只从当前点2 Work 开始。
        (await db.get(AutomaticPickingFeed, feed.id)).archived_at = now
        devices = [
            Device(device_code=code, device_name="8A离线设备", work_line_id=line.id)
            for code in ("OFFLINE-SCAN2", "OFFLINE-SCAN3")
        ]
        db.add_all(devices)
        await db.flush()
        live.config = {
            "device_bindings": {
                **{r.role_key: r.role_key for r in DEFINITION.device_roles},
                "SCAN2": devices[0].device_code,
                "SCAN3": devices[1].device_code,
            }
        }
        live.device_contracts = {
            d.device_code: {
                "device_id": d.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 10000,
                "status_max_age_ms": 10000,
            }
            for d in devices
        }
        work = await workflow().admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code="A123456789",
            evidence_id=scan.id,
            scanned_at=123,
            now=now,
        )
        confirmation = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == work.operation_id))
        payload = {
            "operation_id": work.operation_id,
            "code": "DECIDED",
            "timestamp": 124,
            "data": {"result": "NO_WORK"},
        }
        result = InboundEvidence(
            kind="WMS_RESULT",
            source_identity="result:" + work.operation_id,
            operation=confirmation.operation,
            operation_id=work.operation_id,
            payload_digest=canonical_json_digest(payload),
            normalized_payload=payload,
            received_at=now,
            apply_status="APPLIED",
            workline_id=line.id,
            picking_task_id=task.id,
        )
        db.add(result)
        await db.flush()
        confirmation.status, confirmation.completed_at = "COMPLETED", now
        confirmation.response_evidence_id, confirmation.response_result = result.id, "NO_WORK"
    return N(sessions=sessions, task=task, line=line, feed=feed, scan=scan, now=now, work=work, result=result)


async def scan3(p, **changes):
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        value = {
            "work_id": p.work.id,
            "workline_id": p.line.id,
            "bin_code": p.work.bin_code,
            "command_code": work.exit_command_code,
            "device_code": "OFFLINE-SCAN3",
            "timestamp": 125,
        }
        value.update(changes)
        event_id = new_uuid7()
        payload = {
            "device_code": "OFFLINE-SCAN3",
            "contract_key": "FIXTURE_ONLY",
            "contract_version": "1",
            "source_event_id": event_id,
            "event_type": "SCAN_COMPLETED",
            "timestamp": 125,
            "is_debug": False,
            "data": {"bin_code": "fixture-raw", "fixture": value},
        }
        row = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="scan3:" + event_id,
            payload_digest=normalize_payload(payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1],
            normalized_payload=payload,
            received_at=p.now,
            apply_status="APPLIED",
            workline_id=p.line.id,
            device_code="OFFLINE-SCAN3",
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            source_event_id=event_id,
        )
        db.add(row)
        await db.flush()
        return row.id


async def test_no_work_release_and_first_scan3_handoff_are_durable(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == work.exit_command_code))
        assert work.phase == "EXIT_PENDING" and command is not None and command.status == "PENDING"
        assert (await db.get(InboundEvidence, p.result.id)).published_at is not None
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
    eid = await scan3(p)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        current = await db.scalar(select(AutomaticPickingReturn))
        assert work.phase == "CLOSED"
        assert (current.source_work_id, current.scan3_evidence_id, current.entry_command_code) == (
            p.work.id,
            eid,
            work.exit_command_code,
        )
        assert (await db.get(InboundEvidence, eid)).published_at is not None
        assert (await db.scalar(select(DeviceCommand))).status == "PENDING"


async def advance(p, *, exit_flow=None):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        batch = components(p)[2]
        if exit_flow is not None:
            batch.exit_flow = exit_flow
        return await batch.advance_completed_in_session(db, line)


async def legacy(p):
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        task = await db.get(PickingTask, p.task.id)
        await workflow().advance_in_session(db, task=task, work=work, now=p.now)
        result = await db.get(InboundEvidence, p.result.id)
        result.published_at, result.decision_digest = p.now, canonical_json_digest({"legacy": True})
        task.status = "EXECUTION_COMPLETED"


async def state(p):
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        return (
            work.phase if work else None,
            work.exit_command_code if work else None,
            await db.scalar(select(func.count()).select_from(DeviceCommand)),
            await db.scalar(select(func.count()).select_from(AutomaticPickingReturn)),
        )


async def test_existing_exit_pending_recovers_without_executing_task_and_preserves_command(pending):
    p = pending
    await legacy(p)
    assert await advance(p) == 1
    initial = await state(p)
    assert initial[0] == "EXIT_PENDING" and initial[2:] == (1, 0)
    p.now += timedelta(days=1)

    def fail_mapper(**kwargs):
        raise AssertionError("recovery must not rebuild request or deadline")

    assert await advance(p, exit_flow=components(p, mapper=fail_mapper)[1]) == 0
    assert await state(p) == initial
    eid = await scan3(p)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"
    async with p.sessions() as db:
        assert (await db.scalar(select(AutomaticPickingReturn))).scan3_evidence_id == eid


async def test_concurrent_batch_processors_and_repeated_scans_keep_one_command_and_first_handoff(pending):
    p = pending
    await legacy(p)
    async with asyncio.timeout(15):
        assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 1]
    first, second = await scan3(p), await scan3(p)
    async with asyncio.timeout(15):
        assert sum(await asyncio.gather(processor(p).process_batch(), processor(p).process_batch(), advance(p))) == 2
    before = await state(p)
    assert before[0] == "CLOSED" and before[2:] == (1, 1)
    async with p.sessions() as db:
        row = await db.scalar(select(AutomaticPickingReturn))
        assert row.scan3_evidence_id == first
        for eid in (first, second):
            assert (await db.get(InboundEvidence, eid)).published_at is not None
    assert await processor(p).process_batch() == 0 and await advance(p) == 0
    assert await state(p) == before


@pytest.mark.parametrize("point", ["command", "link"])
async def test_no_work_result_command_link_and_publication_rollback_then_recover(pending, point):
    p = pending
    commands, exit_flow, _, scan = components(p)
    if point == "command":
        original = commands.create_command_in_session

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fixture crash after command")

        commands.create_command_in_session = crash
    else:
        original = exit_flow.advance_in_session

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fixture crash after link")

        exit_flow.advance_in_session = crash
    assert await processor(p, scan=scan).process_batch() == 0
    assert await state(p) == ("PLAN_PENDING", None, 0, 0)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, p.result.id)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[2:] == (1, 0)


async def test_handoff_close_and_publication_rollback_then_recover(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)

    class CrashRepository(ReturnRepository):
        async def add_handoff(self, *args, **kwargs):
            await super().add_handoff(*args, **kwargs)
            raise RuntimeError("fixture crash after handoff")

    scan = components(p, repository=CrashRepository())[3]
    assert await processor(p, scan=scan).process_batch() == 0
    assert (await state(p))[0::3] == ("EXIT_PENDING", 0)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0::3] == ("CLOSED", 1)


async def test_scan3_claim_lease_expires_and_recovers_without_notification(pending):
    from src.app.execution.repositories import inbound_evidence_repository

    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)
    async with p.sessions.begin() as db:
        claimed = await inbound_evidence_repository.claim_decision_batch(
            db, now=p.now, claim_token="dead-8a", claim_expires_at=p.now + timedelta(seconds=30), limit=100
        )
        assert [row.id for row in claimed] == [eid]
    assert await processor(p).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


@pytest.mark.parametrize(
    "changes",
    [
        {"work_id": 999999},
        {"workline_id": 999999},
        {"bin_code": "A000000007"},
        {"command_code": "other-command"},
        {"device_code": "SCAN2"},
        {"timestamp": 122},
    ],
)
async def test_mismatched_scan3_never_closes_work(pending, changes):
    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p, **changes)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0::3] == ("EXIT_PENDING", 0)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"


@pytest.mark.parametrize(
    "state_value", ["PENDING", "DISPATCHING", "ACKNOWLEDGED", "RECONCILING", "TIMED_OUT", "SUCCEEDED"]
)
async def test_scan3_arrival_handoff_preserves_original_command_lifecycle(pending, state_value):
    p = pending
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand))
        command.status = state_value
        original = (command.command_code, command.payload_digest, command.deadline_at)
    await scan3(p)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand))
        assert (command.command_code, command.payload_digest, command.deadline_at) == original
        assert command.status == state_value
        assert (await db.get(AutomaticPickingWork, p.work.id)).phase == "CLOSED"


async def test_definite_command_failure_enters_reconciliation_without_replacement(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        (await db.scalar(select(DeviceCommand))).status = "FAILED"
    assert await advance(p) == 1
    assert (await state(p))[0] == "RECONCILING"
    assert await advance(p) == 0 and (await state(p))[2] == 1
    await scan3(p)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_missing_exact_scan3_association_defers_and_same_evidence_recovers(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)
    scan = components(p)[3]

    class MissingReader:
        async def read_in_session(self, db, **kwargs):
            return None

    scan.exit_flow.scan3_reader = MissingReader()
    assert await processor(p, scan=scan).process_batch() == 1
    async with p.sessions() as db:
        row = await db.get(InboundEvidence, eid)
        assert row.apply_status == "APPLIED" and row.published_at is None and row.decision_next_attempt_at is not None
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_return_survives_closed_work_and_request_history_cleanup_and_old_scan_cannot_close_new_round(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)
    assert await processor(p).process_batch() == 1
    replay = await scan3(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.work.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_([p.scan.id, p.result.id])))
        task = await db.get(PickingTask, p.task.id)
        task.status = "EXECUTION_COMPLETED"
        current_scan = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="new-scan2:" + new_uuid7(),
            payload_digest="c" * 64,
            normalized_payload={},
            received_at=p.now,
            published_at=p.now,
            decision_digest="c" * 64,
            apply_status="APPLIED",
            device_code="OFFLINE-SCAN2",
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            workline_id=p.line.id,
        )
        db.add(current_scan)
        await db.flush()
        new_work = AutomaticPickingWork(
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            feed_id=p.feed.id,
            bin_code=p.work.bin_code,
            scan2_evidence_id=current_scan.id,
            scanned_at=999,
            operation_id=new_uuid7(),
            phase="PLAN_PENDING",
        )
        db.add(new_work)
        await db.flush()
        new_id = new_work.id
    assert await processor(p).process_batch() == 1
    assert await advance(p) == 0
    async with p.sessions() as db:
        row = await db.scalar(select(AutomaticPickingReturn))
        assert (row.source_work_id, row.scan3_evidence_id) == (p.work.id, eid)
        assert (await db.get(AutomaticPickingWork, new_id)).phase == "PLAN_PENDING"
        assert (await db.get(InboundEvidence, replay)).published_at is not None
        summary = await WorkRepository().get_unfinished_workload_summary(db, p.line.id)
        assert summary["count"] >= 2
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
    async with p.sessions.begin() as db:
        assert await WorkRepository().archive_open_work(db, workline_id=p.line.id, archived_at=p.now) >= 2
        assert (await db.scalar(select(AutomaticPickingReturn))).archived_at == p.now


async def test_wrong_exit_mapper_cannot_cross_line_or_become_a_material_command(pending):
    p = pending
    _, flow, _, scan = components(p)
    original = flow.request_mapper

    async def wrong(db, **kwargs):
        return replace(await original(db, **kwargs), material_execution_id=999)

    flow.request_mapper = wrong
    assert await processor(p, scan=scan).process_batch() == 0
    assert await state(p) == ("PLAN_PENDING", None, 0, 0)


async def test_result_callback_before_ack_does_not_close_work_without_scan3(pending):
    from src.app.device.contracts import EcsCommandResultReport
    from src.app.device.services.device_evidence_service import DeviceEvidenceService

    p = pending
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand))
        command.status = "DISPATCHING"
        command_code = command.command_code
    receiver = DeviceEvidenceService(session_factory=p.sessions)
    report = EcsCommandResultReport.model_validate(
        {
            "command_code": command_code,
            "device_code": "OFFLINE-SCAN2",
            "result": "SUCCESS",
            "finish_time": 125,
            "data": {},
        }
    )
    receipt = await receiver.accept_result(report)
    assert await receiver.process_one() is True
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand))
        assert command.status == "SUCCEEDED" and command.ack_received_at is None
        assert command.result_evidence_id == receipt.evidence_id
    assert await processor(p).process_batch() == 1
    assert await advance(p) == 0 and (await state(p))[0] == "EXIT_PENDING"
    await scan3(p)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_existing_exit_pending_crash_after_creation_has_no_partial_command(pending):
    p = pending
    await legacy(p)
    flow = components(p)[1]
    original = flow.advance_in_session

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("fixture batch crash")

    flow.advance_in_session = crash
    with pytest.raises(RuntimeError, match="batch crash"):
        await advance(p, exit_flow=flow)
    assert await state(p) == ("EXIT_PENDING", None, 0, 0)
    assert await advance(p) == 1
    assert (await state(p))[2:] == (1, 0)


async def test_empty_migration_roundtrip_and_existing_exit_pending_upgrade(pending, database):
    from tests.support.postgresql_heavy import run_alembic

    p = pending
    await legacy(p)
    url, _ = database
    run_alembic("downgrade", "55adfc1a25a7", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert await advance(p) == 1
    import subprocess

    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "55adfc1a25a7", database_url=url)
    assert "cannot discard automatic exit" in error.value.stderr
    assert (await state(p))[0] == "EXIT_PENDING"
    await scan3(p)
    assert await processor(p).process_batch() == 1
    with pytest.raises(subprocess.CalledProcessError):
        run_alembic("downgrade", "55adfc1a25a7", database_url=url)


@pytest.mark.parametrize("phase", ["READY", "WAIT", "CLOSED", "RECONCILING"])
async def test_8a_does_not_create_ordinary_end_of_material_exit(pending, phase):
    p = pending
    await legacy(p)
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        work.phase = phase
        if phase == "WAIT":
            work.retry_at = p.now + timedelta(days=1)
    assert await advance(p) == 0
    assert (await state(p))[2:] == (0, 0)


async def test_return_root_and_work_exit_match_migrated_schema(database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    _, sessions = database

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, kind, _parents: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare_to: (
                    kind != "table" or name in {"automatic_picking_works", "automatic_picking_returns"}
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingReturn.metadata)

    async with sessions() as db:
        connection = await db.connection()
        assert await connection.run_sync(compare) == []


async def test_return_alone_recovers_after_all_closed_front_station_history_is_cleaned(pending):
    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)
    assert await processor(p).process_batch() == 1
    replay = await scan3(p)
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.work.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_([p.scan.id, p.result.id])))
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    assert await advance(p) == 0 and await processor(p).process_batch() == 1
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingWork)) == 0
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingFeed)) == 0
        assert (await WorkRepository().get_unfinished_workload_summary(db, p.line.id))["count"] == 1
        row = await db.scalar(select(AutomaticPickingReturn))
        assert row.scan3_evidence_id == eid and row.source_work_id == p.work.id
        assert (await db.get(InboundEvidence, replay)).published_at is not None
        assert (await db.scalar(select(DeviceCommand))).command_code == row.entry_command_code


async def test_crash_after_scan3_publication_rolls_back_handoff_and_close(pending):
    from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository

    p = pending
    assert await processor(p).process_batch() == 1
    eid = await scan3(p)

    class CrashPublication(InboundEvidenceRepository):
        async def flush(self, db):
            row = await db.get(InboundEvidence, eid)
            if row.published_at is not None:
                raise RuntimeError("fixture crash after publication")
            await super().flush(db)

    assert await processor(p, evidences=CrashPublication()).process_batch() == 0
    assert (await state(p))[0::3] == ("EXIT_PENDING", 0)
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0::3] == ("CLOSED", 1)
