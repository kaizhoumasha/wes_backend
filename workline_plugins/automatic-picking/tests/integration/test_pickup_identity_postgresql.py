"""6B：真实表/Command 端口；设备参数、可靠取出与扫码关联仅为离线测试输入。"""

import asyncio
import subprocess
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.application.work_repository import WorkRepository
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from test_work_and_task_progress_postgresql import seed, workflow
from wes_plugin_sdk import PickingCellSource, PickingMaterialAccept, PickingTargetRackSlot

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import InboundEvidence, MaterialExecution
from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from tests.support.postgresql_heavy import migrated_database, run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
SOURCE = PickingCellSource("CELL-7", 7, 2, 1, 20.0, 15.0)
OTHER = PickingCellSource("CELL-2", 2, 3, 1, 18.0, 12.0)
TARGET = PickingTargetRackSlot("TARGET", "A", "S1", 1, 1)


@pytest_asyncio.fixture
async def database():
    async with migrated_database() as value:
        yield value


async def evidence(db, *, now, line_id, data, operation, material_id=None, operation_id=None):
    operation_id = operation_id or new_uuid7()
    payload = {"operation_id": operation_id, "timestamp": 1, "code": "DECIDED", "data": data}
    row = InboundEvidence(
        kind="WMS_RESULT",
        source_identity=operation + ":" + operation_id,
        normalized_payload=payload,
        payload_digest=canonical_json_digest(payload),
        operation=operation,
        operation_id=operation_id,
        workline_id=line_id,
        material_execution_id=material_id,
        received_at=now,
        processed_at=now,
        apply_status="APPLIED",
    )
    db.add(row)
    await db.flush()
    return row


class FixtureObservations:
    """只解释本文件构造的测试 Evidence；未装配生产，不声称设备成功合同已确认。"""

    ready = True

    async def read_in_session(self, db, *, work, source, pickup):
        from automatic_picking.application.pickup_repository import SourceObservation

        removed = None
        if pickup is not None:
            removed = await db.scalar(
                select(InboundEvidence.id).where(
                    InboundEvidence.command_code == pickup.command_code,
                    InboundEvidence.source_identity == "fixture-removed:" + pickup.pickup_id,
                )
            )
        return SourceObservation(source_ready=self.ready, removed_evidence_id=removed)


@pytest_asyncio.fixture
async def prepared(database):
    _, sessions = database
    task, line, feed, scan, now = await seed(sessions)
    async with sessions.begin() as db:
        live = await db.get(WorkLine, line.id)
        live.is_active = True
        device = Device(device_code="OFFLINE-SOURCE", device_name="6B 离线设备", work_line_id=line.id)
        db.add(device)
        await db.flush()
        live.config = {"device_bindings": {"ROBOT_ARM": device.device_code}}
        live.device_contracts = {
            device.device_code: {
                "device_id": device.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 30000,
                "status_max_age_ms": 30000,
            }
        }
        ready = await evidence(
            db,
            now=now,
            line_id=line.id,
            data={"result": "READY", **asdict(SOURCE)},
            operation="outbound.bin.work_plan@v1",
        )
        ready.picking_task_id = task.id
        work = AutomaticPickingWork(
            workline_id=line.id,
            picking_task_id=task.id,
            feed_id=feed.id,
            bin_code="A123456789",
            scan2_evidence_id=scan.id,
            scanned_at=123,
            operation_id=ready.operation_id,
            phase="READY",
            response_evidence_id=ready.id,
            authorization_evidence_id=ready.id,
        )
        db.add(work)
        await db.flush()
    return N(
        sessions=sessions,
        task=task,
        line=line,
        feed=feed,
        now=now,
        work=work,
        ready=ready,
        observations=FixtureObservations(),
    )


def components(p):
    from automatic_picking.application.pickup_repository import PickupRepository
    from automatic_picking.application.source_flow import AutomaticPickingSourceFlow

    repository = PickupRepository(p.observations)

    async def request(db, *, work, pickup_id):
        return DeviceCommandRequest(
            device_code="OFFLINE-SOURCE",
            workline_id=work.workline_id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=pickup_id,
            material_execution_id=None,
            contract_key="FIXTURE_ONLY",
            contract_version="1",
            task_type="FIXTURE_PICK",
            params={"fixture_only": True, "cell_index": work.source.cell_index},
            deadline_at=p.now + timedelta(seconds=10),
        )

    commands = DeviceCommandService(session_factory=p.sessions, clock=lambda: p.now)
    return repository, commands, AutomaticPickingSourceFlow(repository, commands, request)


async def consume(p, auth=None):
    async with p.sessions.begin() as db:
        return await components(p)[2].consume_in_session(
            db, work_id=p.work.id, authorization_evidence_id=auth or p.ready.id
        )


async def counts(p):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    async with p.sessions() as db:
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (AutomaticPickingPickup, DeviceCommand)
            ]
        )


async def accept_input(p, pickup_id, next_source=OTHER, *, removed=True):
    """扫码、单盘和取出由 fixture 显式关联，绝不推导生产扫码协议。"""
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    async with p.sessions.begin() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        scan = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="fixture-scan:" + pickup_id,
            device_code="OFFLINE-SOURCE",
            command_code=pickup.command_code,
            normalized_payload={},
            payload_digest="d" * 64,
            workline_id=p.line.id,
            received_at=p.now,
            apply_status="APPLIED",
        )
        db.add(scan)
        await db.flush()
        material = MaterialExecution(
            execution_code=new_uuid7(),
            material_trace_id="fixture:" + pickup_id,
            workline_id=p.line.id,
            admission_evidence_id=scan.id,
            admission_received_at=p.now,
            last_transition_evidence_id=scan.id,
            last_transition_reason="FIXTURE_SCAN",
            status_changed_at=p.now,
        )
        db.add(material)
        await db.flush()
        pickup.material_execution_id = material.id
        if removed:
            db.add(
                InboundEvidence(
                    kind="DEVICE_RESULT",
                    source_identity="fixture-removed:" + pickup_id,
                    device_code="OFFLINE-SOURCE",
                    command_code=pickup.command_code,
                    normalized_payload={},
                    payload_digest="e" * 64,
                    workline_id=p.line.id,
                    received_at=p.now,
                    apply_status="APPLIED",
                )
            )
        data = {"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}}
        if next_source is not None:
            data["next_source_action"] = asdict(next_source)
        result = await evidence(
            db,
            now=p.now,
            line_id=p.line.id,
            data=data,
            operation="outbound.material.decide@v1",
            material_id=material.id,
        )
        return N(
            pickup_id=pickup_id,
            material_execution_id=material.id,
            evidence_id=result.id,
            outcome=PickingMaterialAccept(TARGET, next_source),
        )


async def handoff(p, accepted):
    async with p.sessions.begin() as db:
        return await components(p)[2].handoff_in_session(db, work_id=p.work.id, **vars(accepted))


async def test_migration_adds_pickup_and_current_work_references(database):
    _, sessions = database
    async with sessions() as db:
        assert await db.scalar(text("SELECT to_regclass('wes_biz.automatic_picking_pickups')")) is not None
        columns = set(
            (
                await db.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='wes_biz' AND table_name='automatic_picking_works'"
                    )
                )
            ).all()
        )
        assert {"consumed_pickup_id", "top_removed_evidence_id"} <= columns


async def test_plugin_source_models_match_migrated_schema(database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    _, sessions = database

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                # harness 的 search_path 含 wes_biz，排除同表以默认 schema 再次反射。
                "include_name": lambda name, kind, _parents: kind != "schema" or name == "wes_biz",
                "include_object": lambda _obj, name, kind, _reflected, _compare_to: (
                    kind != "table" or name in {"automatic_picking_works", "automatic_picking_pickups"}
                ),
            },
        )
        return compare_metadata(context, AutomaticPickingPickup.metadata)

    async with sessions() as db:
        connection = await db.connection()
        differences = await connection.run_sync(compare)
        assert not differences, "\n".join(map(str, differences))


@pytest.mark.parametrize("stage", ["command", "pickup", "consume"])
async def test_creation_failure_rolls_back_authorization_pickup_and_real_command(prepared, monkeypatch, stage):
    p = prepared
    repo, commands, flow = components(p)
    owner, name = {
        "command": (commands, "create_command_in_session"),
        "pickup": (repo, "add_pickup"),
        "consume": (repo, "consume_authorization"),
    }[stage]
    original = getattr(owner, name)

    async def fail_after(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("crash:" + stage)

    monkeypatch.setattr(owner, name, fail_after)
    with pytest.raises(RuntimeError, match="crash"):
        async with p.sessions.begin() as db:
            await flow.consume_in_session(db, work_id=p.work.id, authorization_evidence_id=p.ready.id)
    assert await counts(p) == (0, 0)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id is None
    assert await consume(p) is not None
    assert await counts(p) == (1, 1)


async def test_concurrent_consumers_and_restart_reuse_original_command(prepared):
    p = prepared
    ids = await asyncio.wait_for(asyncio.gather(*(consume(p) for _ in range(5))), timeout=15)
    assert len(set(ids)) == 1 and ids[0] is not None
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand))
        frozen = (command.command_code, command.payload_digest, command.params, command.execution_ref_id)
    assert await consume(p) == ids[0]
    assert await counts(p) == (1, 1)
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand))
        assert (command.command_code, command.payload_digest, command.params, command.execution_ref_id) == frozen
        assert command.execution_ref_id == ids[0] and command.material_execution_id is None


@pytest.mark.parametrize("stage", ["accept", "handoff"])
async def test_accept_target_and_handoff_roll_back_together(prepared, monkeypatch, stage):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    p = prepared
    accepted = await accept_input(p, await consume(p))
    repo, _, flow = components(p)
    name = "record_accept" if stage == "accept" else "handoff_authorization"
    original = getattr(repo, name)

    async def fail_after(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("crash:" + stage)

    monkeypatch.setattr(repo, name, fail_after)
    with pytest.raises(RuntimeError, match="crash"):
        async with p.sessions.begin() as db:
            await flow.handoff_in_session(db, work_id=p.work.id, **vars(accepted))
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        pickup = await db.scalar(select(AutomaticPickingPickup))
        assert work.authorization_evidence_id == p.ready.id and pickup.accept_evidence_id is None
    assert await handoff(p, accepted)
    assert not await handoff(p, accepted)


@pytest.mark.parametrize("next_source", [SOURCE, OTHER, None])
async def test_handoff_restores_from_current_work_after_closed_pickup_history_is_removed(prepared, next_source):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    p = prepared
    m1 = await consume(p)
    accepted = await accept_input(p, m1, next_source)
    assert await handoff(p, accepted)
    async with p.sessions.begin() as db:
        # M1 的 PUT/报告闭合是本片的显式前提，不声称任务 7 已实现。
        old = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == m1))
        command_code = old.command_code
        await db.execute(delete(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == m1))
        await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code == command_code))
    assert not await handoff(p, accepted)  # 无需查询已清理的 M1。
    assert await consume(p, p.ready.id) is None
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.authorization_evidence_id == accepted.evidence_id
        assert work.top_removed_evidence_id is not None
    m2 = await consume(p, accepted.evidence_id)
    assert (m2 is None) == (next_source is None)
    if m2 is not None:
        assert m2 != m1 and await consume(p, accepted.evidence_id) == m2
        assert await counts(p) == (1, 1)


async def test_work_batch_owner_recovers_lost_notification_without_waiting_for_m1_target(prepared):
    p = prepared
    m1 = await consume(p)
    accepted = await accept_input(p, m1)
    assert await handoff(p, accepted)
    # 新实例没有进程内游标；M1 没有 PUT/报告，也未关闭。
    driver = AutomaticPickingBatchDriver(
        WorkRepository(), N(list_recoverable=_empty), workflow(), None, None, source_flow=components(p)[2]
    )
    async with p.sessions.begin() as db:
        line = await db.scalar(select(WorkLine).where(WorkLine.id == p.line.id).with_for_update())
        assert await driver.advance_completed_in_session(db, line) == 1
    async with p.sessions.begin() as db:
        line = await db.scalar(select(WorkLine).where(WorkLine.id == p.line.id).with_for_update())
        assert await driver.advance_completed_in_session(db, line) == 0
    assert await counts(p) == (2, 2)


async def _empty(*_args):
    return []


async def test_unknown_top_defers_then_recovers_matching_durable_fact(prepared):
    p = prepared
    m1 = await consume(p)
    accepted = await accept_input(p, m1, removed=False)
    assert await handoff(p, accepted)
    assert await consume(p, accepted.evidence_id) is None
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    async with p.sessions.begin() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup))
        db.add(
            InboundEvidence(
                kind="DEVICE_RESULT",
                source_identity="fixture-removed:" + m1,
                device_code="OFFLINE-SOURCE",
                command_code=pickup.command_code,
                normalized_payload={},
                payload_digest="e" * 64,
                workline_id=p.line.id,
                received_at=p.now,
                apply_status="APPLIED",
            )
        )
    assert await consume(p, accepted.evidence_id) not in (None, m1)


async def test_direct_member_cancellation_stops_only_new_consumption(prepared):
    p = prepared
    async with p.sessions.begin() as db:
        member = await db.scalar(select(PickingTaskBinSourceRack))
        member.cancelled_evidence_id = p.task.issued_evidence_id
    assert await consume(p) is None and await counts(p) == (0, 0)


async def test_cached_member_is_refreshed_after_real_cancel_commits(prepared):
    from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelEvent
    from src.app.wms_integration.outbound_picking.services.picking_task_cancel import PickingTaskCancelService

    p = prepared
    async with p.sessions.begin() as db:
        member = await db.scalar(select(PickingTaskBinSourceRack))
        assert member.cancelled_evidence_id is None
        event = PickingTaskCancelEvent.model_validate(
            {
                "operation": "outbound.picking_task.cancel@v1",
                "operation_id": new_uuid7(),
                "timestamp": 2,
                "data": {
                    "task_id": p.task.task_id,
                    "cancel_scope": "PLAN_MEMBERS",
                    "bin_source_racks": [
                        {
                            "plan_revision": member.plan_revision,
                            "rack_id": member.rack_id,
                            "rack_face": [member.rack_face],
                        }
                    ],
                },
            }
        )
        result = await PickingTaskCancelService(p.sessions).record(event, received_at=p.now)
        assert result.code == "RECEIVED"
        # 保留原引用，使取消前的 identity-map 快照确实存在。
        assert member.cancelled_evidence_id is None
        assert (
            await components(p)[2].consume_in_session(db, work_id=p.work.id, authorization_evidence_id=p.ready.id)
            is None
        )
    assert await counts(p) == (0, 0)


async def test_schema_rejects_second_pickup_for_same_authorization(prepared):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup

    p = prepared
    await consume(p)
    with pytest.raises(IntegrityError, match="ux_automatic_pickup_authorization"):
        async with p.sessions.begin() as db:
            repo, commands, flow = components(p)
            fact = await repo.source_for_update(db, p.work.id)
            other_id = new_uuid7()
            other = await commands.create_command_in_session(
                db, await flow.request_factory(db, work=fact, pickup_id=other_id)
            )
            db.add(
                AutomaticPickingPickup(
                    pickup_id=other_id,
                    work_id=p.work.id,
                    authorization_evidence_id=p.ready.id,
                    command_code=other.command_code,
                )
            )
            await db.flush()


async def test_empty_migration_downgrade_and_upgrade(database):
    url, sessions = database
    run_alembic("downgrade", "c106df25b129", database_url=url)
    async with sessions() as db:
        assert await db.scalar(text("SELECT to_regclass('wes_biz.automatic_picking_pickups')")) is None
    run_alembic("upgrade", "head", database_url=url)
    async with sessions() as db:
        assert await db.scalar(text("SELECT to_regclass('wes_biz.automatic_picking_pickups')")) is not None


async def test_upgrade_preserves_existing_ready_work_and_downgrade_preserves_active_pickup(prepared, database):
    p = prepared
    url, _ = database
    run_alembic("downgrade", "c106df25b129", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert (work.phase, work.authorization_evidence_id, work.consumed_pickup_id, work.top_removed_evidence_id) == (
            "READY",
            p.ready.id,
            None,
            None,
        )
    pickup_id = await consume(p)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "c106df25b129", database_url=url)
    assert "downgrade refused" in error.value.stderr
    assert await consume(p) == pickup_id and await counts(p) == (1, 1)


async def test_concurrent_accept_replay_and_successor_consumption_keep_one_current_authorization(prepared):
    p = prepared
    m1 = await consume(p)
    accepted = await accept_input(p, m1)
    results = await asyncio.wait_for(asyncio.gather(*(handoff(p, accepted) for _ in range(4))), timeout=15)
    assert results.count(True) == 1
    ids = await asyncio.wait_for(asyncio.gather(*(consume(p, accepted.evidence_id) for _ in range(4))), timeout=15)
    assert len(set(ids)) == 1 and ids[0] not in (None, m1)
    assert not await handoff(p, accepted)
    assert await consume(p, p.ready.id) is None
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.authorization_evidence_id == accepted.evidence_id and work.consumed_pickup_id == ids[0]
    assert await counts(p) == (2, 2)


async def test_postgresql_consumer_waits_for_owner_lock_then_reuses_committed_pickup(prepared):
    p = prepared
    pid_queue = asyncio.Queue()

    async def competitor():
        async with p.sessions.begin() as db:
            await pid_queue.put(await db.scalar(text("SELECT pg_backend_pid()")))
            return await components(p)[2].consume_in_session(
                db, work_id=p.work.id, authorization_evidence_id=p.ready.id
            )

    task = None
    try:
        async with p.sessions.begin() as db:
            await db.scalar(select(WorkLine).where(WorkLine.id == p.line.id).with_for_update())
            owner_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            first = await components(p)[2].consume_in_session(
                db, work_id=p.work.id, authorization_evidence_id=p.ready.id
            )
            task = asyncio.create_task(competitor())
            contender_pid = await asyncio.wait_for(pid_queue.get(), timeout=5)

            async def blocked_by_owner():
                async with p.sessions() as observer:
                    while True:
                        blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": contender_pid})
                        if owner_pid in blockers:
                            return
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(blocked_by_owner(), timeout=5)
            assert not task.done()
        assert await asyncio.wait_for(task, timeout=5) == first
        assert await counts(p) == (1, 1)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("case", ["source_not_ready", "reconciling", "archived"])
async def test_unexecutable_current_source_never_consumes_authorization(prepared, case):
    p = prepared
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        if case == "source_not_ready":
            p.observations.ready = False
        elif case == "reconciling":
            work.phase = "RECONCILING"
        else:
            work.archived_at = p.now
    assert await consume(p) is None and await counts(p) == (0, 0)


@pytest.mark.parametrize("case", ["different_material", "different_target", "different_workline"])
async def test_accept_cannot_handoff_from_mismatched_frozen_evidence(prepared, case):
    p = prepared
    accepted = await accept_input(p, await consume(p))
    if case == "different_material":
        accepted.material_execution_id += 1
    elif case == "different_target":
        accepted.outcome = PickingMaterialAccept(PickingTargetRackSlot("OTHER", "B", "S2", 2, 2), OTHER)
    else:
        async with p.sessions.begin() as db:
            row = await db.get(InboundEvidence, accepted.evidence_id)
            row.workline_id = None
    with pytest.raises(ValueError):
        await handoff(p, accepted)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.authorization_evidence_id == p.ready.id


async def test_foreign_command_fact_cannot_release_current_top(prepared):
    from automatic_picking.application.pickup_repository import SourceObservation

    p = prepared
    await consume(p)
    async with p.sessions.begin() as db:
        foreign = InboundEvidence(
            kind="DEVICE_RESULT",
            source_identity="fixture-foreign:" + new_uuid7(),
            device_code="OFFLINE-SOURCE",
            command_code="ANOTHER-COMMAND",
            normalized_payload={},
            payload_digest="f" * 64,
            workline_id=p.line.id,
            received_at=p.now,
            apply_status="APPLIED",
        )
        db.add(foreign)
        await db.flush()

    class ForeignObservation:
        async def read_in_session(self, _db, **_kwargs):
            return SourceObservation(True, foreign.id)

    p.observations = ForeignObservation()
    with pytest.raises(ValueError, match="current top Command"):
        await consume(p)
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).top_removed_evidence_id is None


async def test_old_ready_application_does_not_overwrite_successor_authorization(prepared):
    p = prepared
    accepted = await accept_input(p, await consume(p))
    assert await handoff(p, accepted)
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert await workflow().advance_in_session(db, task=p.task, work=work, now=p.now) == 0
        assert work.authorization_evidence_id == accepted.evidence_id


async def test_parent_terminal_status_does_not_replace_direct_requirement(prepared):
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = prepared
    async with p.sessions.begin() as db:
        task = await db.get(PickingTask, p.task.id)
        task.status = "EXECUTION_COMPLETED"
    assert await consume(p) is not None


async def test_current_authorization_and_removed_evidence_cannot_be_purged(prepared):
    p = prepared
    accepted = await accept_input(p, await consume(p))
    assert await handoff(p, accepted)
    async with p.sessions() as db:
        removed = (await db.get(AutomaticPickingWork, p.work.id)).top_removed_evidence_id
    for evidence_id in (accepted.evidence_id, removed):
        with pytest.raises(IntegrityError):
            async with p.sessions.begin() as db:
                await db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence_id))
    assert await consume(p, accepted.evidence_id) is not None


async def test_repeated_current_removed_fact_survives_closed_pickup_cleanup(prepared):
    from automatic_picking.application.pickup_model import AutomaticPickingPickup
    from automatic_picking.application.pickup_repository import SourceObservation

    p = prepared
    accepted = await accept_input(p, await consume(p))
    assert await handoff(p, accepted)
    async with p.sessions.begin() as db:
        old = await db.scalar(select(AutomaticPickingPickup))
        command_code = old.command_code
        await db.delete(old)
        await db.flush()
        await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code == command_code))

    class RetainedObservation(FixtureObservations):
        async def read_in_session(self, db, *, work, source, pickup):
            if pickup is None:
                return SourceObservation(True, work.top_removed_evidence_id)
            return await super().read_in_session(db, work=work, source=source, pickup=pickup)

    p.observations = RetainedObservation()
    assert await consume(p, accepted.evidence_id) is not None
