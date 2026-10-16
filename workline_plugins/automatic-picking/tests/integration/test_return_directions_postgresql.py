"""8C：完整方向参数、精确关联和成功后置条件均为显式离线输入。"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
import pytest_asyncio
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.return_model import AutomaticPickingReturn
from automatic_picking.application.return_repository import ReturnRepository
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import delete, func, select
from test_return_fifo_postgresql import FixtureScan4Reader, arrived, scan4
from test_returns_postgresql import components, database, pending, processor

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsCommandResultReport
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def application(p, *, repository=None, mapper=None):
    _commands, flow, batch, scan = components(p, repository=repository)

    def request(*, current, role, execution_ref_id):
        return DeviceCommandRequest(
            device_code="OFFLINE-SCAN3" if role == "SCAN3" else "SCAN4",
            workline_id=current.workline_id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=execution_ref_id,
            material_execution_id=None,
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            task_type="MOVE_FORWARD",
            params={"fixture_only": True, "bin_code": current.bin_code, "role": role},
            deadline_at=p.now + timedelta(seconds=10),
        )

    flow.scan4_reader = FixtureScan4Reader()
    flow.direction_mapper = mapper or request
    flow.direction_reader = FixtureResultReader()
    return flow, batch, scan


async def advance(p, *, batch=None):
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id, with_for_update=True)
        return await (batch or application(p)[1]).advance_completed_in_session(db, line)


async def test_scan3_current_return_creates_one_frozen_forward_command(arrived):
    p = arrived
    assert await advance(p) == 1
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.device_code == "OFFLINE-SCAN3"))
        assert command.task_type == "MOVE_FORWARD"
        assert command.params == {"fixture_only": True, "bin_code": "A123456789", "role": "SCAN3"}


class FixtureResultReader:
    async def read_in_session(self, db, *, evidence_id):
        from automatic_picking.application.return_flow import MatchedReturnDirection

        evidence = await db.get(InboundEvidence, evidence_id)
        value = evidence.normalized_payload["data"].get("fixture")
        return MatchedReturnDirection(evidence_id=evidence_id, **value) if value else None


@pytest_asyncio.fixture
async def observed(arrived):
    p = arrived
    async with p.sessions.begin() as db:
        device = Device(device_code="SCAN4", device_name="8C offline", work_line_id=p.line.id)
        db.add(device)
        await db.flush()
        line = await db.get(WorkLine, p.line.id)
        line.device_contracts = {
            **line.device_contracts,
            "SCAN4": {
                "device_id": device.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 10000,
                "status_max_age_ms": 10000,
            },
        }
    p.scan4_id = await scan4(p)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    return p


async def callback(p, command_role="SCAN4", *, result="SUCCESS", at_outlet=True, **changes):
    async with p.sessions.begin() as db:
        current = await db.get(AutomaticPickingReturn, p.return_id)
        code = current.scan4_command_code if command_role == "SCAN4" else current.scan3_command_code
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        command.status = "DISPATCHING"  # 离线只给定已开始派发，不调用设备。
        value = {
            "return_id": current.id,
            "workline_id": current.workline_id,
            "bin_code": current.bin_code,
            "role": command_role,
            "command_code": code,
            "device_code": command.device_code,
            "at_outlet": at_outlet,
        }
        value.update(changes)
        report = EcsCommandResultReport(
            command_code=code,
            device_code=command.device_code,
            result=result,
            finish_time=2000,
            data={"fixture": value},
            error_detail={"code": "FIXTURE_FAILED", "msg": "offline"} if result == "FAILED" else None,
        )
    service = DeviceEvidenceService(session_factory=p.sessions)
    await service.accept_result(report)
    assert await service.process_one()
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        assert command.ack_received_at is None
        return command.result_evidence_id


async def test_scan4_success_before_ack_qualifies_only_current_return_and_keeps_first_order(observed):
    p = observed
    assert await advance(p) == 1
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.ready_evidence_id is None
        assert row.scan3_command_code is None  # 存量8B已到后点，不补发前段动作。
    eid = await callback(p)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.ready_evidence_id == eid, (await db.get(InboundEvidence, eid)).model_dump()
        assert (row.scan4_evidence_id, row.scan4_event_time) == (p.scan4_id, 1000)
        assert (await db.get(InboundEvidence, eid)).published_at is not None
    assert await advance(p) == 0


async def test_concurrent_batch_creates_exactly_one_direction(arrived):
    p = arrived
    assert sorted(await asyncio.gather(advance(p), advance(p), advance(p))) == [0, 0, 1]
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2


@pytest.mark.parametrize("after", [False, True])
async def test_command_creation_and_return_association_rollback_together(arrived, after):
    p = arrived

    class Crash(ReturnRepository):
        async def bind_direction(self, *args, **kwargs):
            if after:
                await super().bind_direction(*args, **kwargs)
            raise RuntimeError("fixture creation crash")

    with pytest.raises(RuntimeError, match="fixture creation crash"):
        await advance(p, batch=application(p, repository=Crash())[1])
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
        assert (await db.get(AutomaticPickingReturn, p.return_id)).scan3_command_code is None
    assert await advance(p) == 1


@pytest.mark.parametrize("point", ["qualification", "publication"])
async def test_qualification_and_publication_rollback_and_resume_original_result(observed, point):
    from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository

    p = observed
    assert await advance(p) == 1
    eid = await callback(p)

    class CrashQualification(ReturnRepository):
        async def qualify(self, *args, **kwargs):
            await super().qualify(*args, **kwargs)
            if point == "qualification":
                raise RuntimeError("fixture qualification crash")

    class CrashPublication(InboundEvidenceRepository):
        async def flush(self, db):
            if point == "publication" and (await db.get(InboundEvidence, eid)).published_at is not None:
                raise RuntimeError("fixture publication crash")
            await super().flush(db)

    assert (
        await processor(
            p, scan=application(p, repository=CrashQualification())[2], evidences=CrashPublication()
        ).process_batch()
        == 0
    )
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id is None
        assert (await db.get(InboundEvidence, eid)).published_at is None
    p.now += timedelta(seconds=2)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id == eid


@pytest.mark.parametrize(
    "mismatch",
    [
        {"return_id": 999999},
        {"workline_id": 999999},
        {"bin_code": "OTHER"},
        {"role": "SCAN3"},
        {"command_code": "OTHER"},
        {"device_code": "OTHER"},
        {"at_outlet": False},
    ],
)
async def test_result_identity_and_success_postcondition_are_required(observed, mismatch):
    p = observed
    assert await advance(p) == 1
    eid = await callback(p, **mismatch)
    await processor(p, scan=application(p)[2]).process_batch()
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id is None
    assert await advance(p) == 0


async def test_failed_direction_does_not_qualify_or_create_replacement(observed):
    p = observed
    assert await advance(p) == 1
    eid = await callback(p, result="FAILED")
    await processor(p, scan=application(p)[2]).process_batch()
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).apply_status == "RECONCILING"
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id is None
    assert await advance(p) == 0


async def test_unknown_scan3_does_not_block_authoritative_scan4_or_get_faked_success(arrived):
    p = arrived
    assert await advance(p) == 1
    async with p.sessions.begin() as db:
        current = await db.get(AutomaticPickingReturn, p.return_id)
        code = current.scan3_command_code
    # Reuse SCAN4 device setup from the fixture via explicit offline binding.
    async with p.sessions.begin() as db:
        device = Device(device_code="SCAN4", device_name="8C offline", work_line_id=p.line.id)
        db.add(device)
        await db.flush()
        line = await db.get(WorkLine, p.line.id)
        line.device_contracts = {
            **line.device_contracts,
            "SCAN4": {
                "device_id": device.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 10000,
                "status_max_age_ms": 10000,
            },
        }
    await scan4(p)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    assert await advance(p) == 1
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        assert command.status == "PENDING" and command.result_evidence_id is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 3


async def test_scan3_success_is_published_without_qualifying_return(arrived):
    p = arrived
    assert await advance(p) == 1
    eid = await callback(p, "SCAN3", at_outlet=False)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(InboundEvidence, eid)).published_at is not None
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id is None


async def test_concurrent_result_and_batch_replay_preserve_qualification(observed):
    p = observed
    assert await advance(p) == 1
    eid = await callback(p)
    await asyncio.gather(
        processor(p, scan=application(p)[2]).process_batch(),
        processor(p, scan=application(p)[2]).process_batch(),
        advance(p),
    )
    async with p.sessions.begin() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id == eid
        (await db.get(InboundEvidence, eid)).published_at = None
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id == eid
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


async def test_no_executing_or_cleanable_history_needed_to_resume_existing_8b(observed):
    p = observed
    async with p.sessions.begin() as db:
        await db.execute(delete(AutomaticPickingWork).where(AutomaticPickingWork.id == p.work.id))
        await db.execute(delete(AutomaticPickingFeed).where(AutomaticPickingFeed.id == p.feed.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation_id == p.work.operation_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_([p.scan.id, p.result.id])))
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    assert await advance(p) == 1
    eid = await callback(p)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingReturn, p.return_id)).ready_evidence_id == eid
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 0


async def test_missing_reader_is_durable_deferred_and_lease_expiry_restores_result(observed):
    from src.app.execution.repositories import inbound_evidence_repository

    p = observed
    assert await advance(p) == 1
    eid = await callback(p)
    flow, _, scan = application(p)
    flow.direction_reader = None
    assert await processor(p, scan=scan).process_batch() == 1
    async with p.sessions.begin() as db:
        row = await db.get(InboundEvidence, eid)
        assert row.apply_status == "APPLIED" and row.published_at is None and row.decision_next_attempt_at is not None
    p.now += timedelta(seconds=2)
    async with p.sessions.begin() as db:
        rows = await inbound_evidence_repository.claim_decision_batch(
            db, now=p.now, claim_token="dead-8c", claim_expires_at=p.now + timedelta(seconds=30), limit=100
        )
        assert [row.id for row in rows] == [eid]
    assert await processor(p, scan=application(p)[2]).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1


async def test_inactive_line_preserves_current_return_without_new_direction(arrived):
    p = arrived
    async with p.sessions.begin() as db:
        (await db.get(WorkLine, p.line.id)).is_active = False
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"device_code": "OFFLINE-SCAN2"},
        {"workline_id": 999999},
        {"material_execution_id": 1},
        {"execution_ref_type": "OTHER"},
        {"execution_ref_id": "OTHER"},
        {"contract_key": "OTHER"},
        {"task_type": "MOVE_LEFT"},
    ],
)
async def test_request_identity_mismatch_cannot_create_direction(arrived, bad):
    p = arrived
    original = application(p)[0].direction_mapper

    def wrong(**kwargs):
        return replace(original(**kwargs), **bad)

    with pytest.raises(ValueError, match="direction request differs"):
        await advance(p, batch=application(p, mapper=wrong)[1])
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


async def test_unpublished_scan4_keeps_original_fact_without_creating_either_direction(observed):
    p = observed
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, p.scan4_id)).published_at = None
    assert await advance(p) == 0
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert row.scan3_command_code is None and row.scan4_command_code is None
        assert row.ready_evidence_id is None


@pytest.mark.parametrize("purged", [False, True])
async def test_old_result_cannot_qualify_new_same_bin_return(observed, purged):
    from test_return_fifo_postgresql import other_return

    p = observed
    assert await advance(p) == 1
    eid = await callback(p)
    assert await processor(p, scan=application(p)[2]).process_batch() == 1
    async with p.sessions.begin() as db:
        (await db.get(AutomaticPickingReturn, p.return_id)).archived_at = p.now
        (await db.get(InboundEvidence, eid)).published_at = None
    new_id = await other_return(p, bin_code="A123456789")
    if purged:
        async with p.sessions.begin() as db:
            await db.execute(delete(AutomaticPickingReturn).where(AutomaticPickingReturn.id == p.return_id))
    await processor(p, scan=application(p)[2]).process_batch()
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, new_id)
        assert row.ready_evidence_id is None and row.scan4_evidence_id is None


async def test_migration_preserves_8b_facts_and_refuses_to_drop_direction_obligation(observed, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = observed
    url, _ = database
    run_alembic("downgrade", "d028d7c65eb7", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with p.sessions() as db:
        row = await db.get(AutomaticPickingReturn, p.return_id)
        assert (row.scan4_evidence_id, row.scan4_event_time) == (p.scan4_id, 1000)
        assert row.scan3_command_code is None and row.scan4_command_code is None and row.ready_evidence_id is None
    assert await advance(p) == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "d028d7c65eb7", database_url=url)
    assert "cannot discard current direction obligations or READY facts" in error.value.stderr
