"""7A：普通目标就绪是明确给定的离线前提，不定义生产设备映射或扫码合同。"""

import asyncio
import subprocess
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.handlers.material_flow import MaterialDecidedFact
from sqlalchemy import func, select, text
from test_material_flow_postgresql import flow as material_flow
from test_material_flow_postgresql import initial, processor, result
from test_pickup_identity_postgresql import TARGET, components, database, prepared

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader
from src.app.workline.models import WorkLine
from tests.support.postgresql_heavy import run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def accepted(prepared, request):
    p = prepared
    p.pickup_id, p.scan_id, p.material_id, p.decide = await initial(p)
    p.accept_id = await result(p, p.decide)
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        target = Device(device_code="OFFLINE-TARGET", device_name="7A 离线目标", work_line_id=p.line.id)
        db.add(target)
        await db.flush()
        line = await db.get(WorkLine, p.line.id)
        line.config = {"device_bindings": {"ROBOT_ARM": "OFFLINE-SOURCE", "FIXTURE_TARGET": target.device_code}}
        line.device_contracts = {
            **line.device_contracts,
            target.device_code: {
                "device_id": target.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 30000,
                "status_max_age_ms": 30000,
            },
        }
    p.target_ready, p.observation_changes, p.request_changes = True, {}, {}
    if getattr(request, "param", True):
        await removed(p)
    return p


class FixtureTargetReader:
    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, workline_id, material_execution_id, target):
        from automatic_picking.application.target_preparation import ReadyTarget

        if not self.p.target_ready:
            return None
        return ReadyTarget(
            **{
                "workline_id": self.p.line.id,
                "material_execution_id": self.p.material_id,
                "target": TARGET,
                **self.p.observation_changes,
            }
        )


def request(p, *, workline_id, pickup_id, material_execution_id, target, now):
    return DeviceCommandRequest(
        **{
            "device_code": "OFFLINE-TARGET",
            "workline_id": workline_id,
            "execution_ref_type": WORKLINE_BUSINESS_REF_TYPE,
            "execution_ref_id": "put:" + pickup_id,
            "material_execution_id": material_execution_id,
            "contract_key": "FIXTURE_ONLY",
            "contract_version": "1",
            "task_type": "FIXTURE_PUT",
            "params": {"fixture_only": True, "target": asdict(target)},
            "deadline_at": now + timedelta(seconds=10),
            **p.request_changes,
        }
    )


def flow(p, *, repository=None):
    from automatic_picking.application.target_preparation import AutomaticPickingTargetFlow

    return AutomaticPickingTargetFlow(
        repository or components(p)[0],
        DeviceCommandService(session_factory=p.sessions, clock=lambda: p.now),
        MaterialDecideResultReader(),
        FixtureTargetReader(p),
        lambda **kw: request(p, **kw),
    )


async def empty(*_):
    return []


async def advance(p, application=None):
    driver = AutomaticPickingBatchDriver(
        N(list_current=empty),
        N(list_recoverable=empty),
        None,
        None,
        None,
        target_flow=application or flow(p),
        clock=lambda: p.now,
    )
    async with p.sessions.begin() as db:
        return await driver.advance_completed_in_session(db, p.line)


async def current_pickup(p, db):
    return await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == p.pickup_id))


async def test_batch_creates_and_links_one_put_without_report_or_material_close(accepted):
    p = accepted
    assert await advance(p) == 1
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        put = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.put_command_code))
        assert put.command_code != pickup.command_code and put.material_execution_id == p.material_id
        original = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.command_code))
        assert original.material_execution_id is None and original.execution_ref_id == p.pickup_id
        assert put.execution_ref_id == "put:" + p.pickup_id
        assert put.params == {
            "fixture_only": True,
            "target": {
                "rack_id": "TARGET",
                "rack_face": "A",
                "slot_id": "S1",
                "rack_layer": 1,
                "rack_column": 1,
            },
        }
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        assert (await db.get(MaterialExecution, p.material_id)).status == "HOLD"
        assert (await db.get(InboundEvidence, p.accept_id)).published_at is not None
    assert await advance(p) == 0


async def test_concurrent_batches_and_restart_reuse_one_put_after_old_accept_replay(accepted):
    p = accepted
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 1]
    async with p.sessions() as db:
        code = (await current_pickup(p, db)).put_command_code
        published = (await db.get(InboundEvidence, p.accept_id)).published_at
    assert await processor(p).process_batch() == 0
    p.now += timedelta(days=1)
    p.target_ready = False
    p.request_changes = {"device_code": "MAPPING_CHANGED"}
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code == code
        assert (await db.get(InboundEvidence, p.accept_id)).published_at == published
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2


@pytest.mark.parametrize("stage", ["create", "link"])
async def test_put_and_pickup_link_roll_back_together_then_recover(accepted, monkeypatch, stage):
    p = accepted
    application = flow(p)
    create = application.commands.create_command_in_session

    async def crash(*args, **kwargs):
        await create(*args, **kwargs)
        raise RuntimeError("crash after PUT creation")

    if stage == "create":
        monkeypatch.setattr(application.commands, "create_command_in_session", crash)
    with pytest.raises(RuntimeError, match="crash"):
        async with p.sessions.begin() as db:
            await application.advance_in_session(db, line_id=p.line.id, now=p.now)
            raise RuntimeError("crash after link")
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
        assert (await db.get(MaterialExecution, p.material_id)).status == "HOLD"
    assert await advance(p) == 1


async def test_unknown_target_defers_without_redecide_then_recovers_current_fact(accepted):
    p = accepted
    p.target_ready = False
    assert await advance(p) == 0
    p.target_ready = True
    assert await advance(p) == 1
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"workline_id": 999},
        {"material_execution_id": 999},
        {"target": replace(TARGET, slot_id="WRONG")},
    ],
)
async def test_readiness_for_another_target_or_material_cannot_create_put(accepted, changes):
    p = accepted
    p.observation_changes = changes
    with pytest.raises(ValueError, match="target identity"):
        await advance(p)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None


@pytest.mark.parametrize(
    "changes",
    [
        {"workline_id": 999},
        {"material_execution_id": None},
        {"execution_ref_id": "WRONG"},
        {"execution_ref_type": "WRONG"},
    ],
)
async def test_request_factory_cannot_change_workline_material_or_step_identity(accepted, changes):
    p = accepted
    p.request_changes = changes
    with pytest.raises(ValueError, match="request must retain"):
        await advance(p)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


@pytest.mark.parametrize("status", ["PENDING", "ACKNOWLEDGED", "RECONCILING", "SUCCEEDED"])
async def test_linked_put_retains_original_identity_and_does_not_report_or_close(accepted, status):
    p = accepted
    await advance(p)
    async with p.sessions.begin() as db:
        code = (await current_pickup(p, db)).put_command_code
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        command.status = status
    p.now += timedelta(days=1)
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code == code
        assert (await db.get(MaterialExecution, p.material_id)).status == "HOLD"
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


async def test_parent_completion_does_not_replace_current_requirement(accepted):
    p = accepted
    async with p.sessions.begin() as db:
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    assert await advance(p) == 1


async def test_cached_direct_cancel_blocks_new_put(accepted):
    from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelEvent
    from src.app.wms_integration.outbound_picking.services.picking_task_cancel import PickingTaskCancelService
    from src.core.uuid7 import new_uuid7

    p = accepted
    async with p.sessions.begin() as db:
        member = await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)
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
        assert (await PickingTaskCancelService(p.sessions).record(event, received_at=p.now)).code == "RECEIVED"
        assert member.cancelled_evidence_id is None
        assert await flow(p).advance_in_session(db, line_id=p.line.id, now=p.now) == 0
        assert member.cancelled_evidence_id is not None
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None


async def test_existing_put_obligation_survives_direct_cancel_and_mapper_unavailable(accepted, monkeypatch):
    p = accepted
    await advance(p)
    application = flow(p)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("existing PUT must use its frozen identity")

    monkeypatch.setattr(application.material_reader, "by_id", forbidden)
    monkeypatch.setattr(application.target_reader, "read_in_session", forbidden)

    def forbidden_mapping(**_):
        raise AssertionError("must not remap")

    application.request_factory = forbidden_mapping
    async with p.sessions.begin() as db:
        member = await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)
        member.cancelled_evidence_id = p.task.issued_evidence_id
    assert await advance(p, application) == 0


@pytest.mark.parametrize("drift", ["accept", "trace", "request", "unpublished"])
async def test_unmatched_persisted_accept_or_material_never_creates_put(accepted, drift):
    from src.utils.canonical_json import canonical_json_digest

    p = accepted
    async with p.sessions.begin() as db:
        if drift == "accept":
            (await current_pickup(p, db)).accept_evidence_id = p.ready.id
        elif drift == "trace":
            (await db.get(MaterialExecution, p.material_id)).material_trace_id = "another-trace"
        elif drift == "unpublished":
            (await db.get(InboundEvidence, p.accept_id)).published_at = None
        else:
            confirmation = await db.get(WmsConfirmation, p.decide.id)
            payload = {
                **confirmation.request_payload,
                "data": {**confirmation.request_payload["data"], "task_id": "ANOTHER"},
            }
            confirmation.request_payload, confirmation.request_digest = payload, canonical_json_digest(payload)
    with pytest.raises(ValueError):
        await advance(p)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


async def test_wrong_linked_command_identity_is_not_replaced_with_new_put(accepted):
    p = accepted
    async with p.sessions.begin() as db:
        row = await current_pickup(p, db)
        row.put_command_code = row.command_code
    with pytest.raises(ValueError, match="PUT Command identity"):
        await advance(p)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


async def test_null_material_cursor_uses_exact_initial_accept_identity(accepted):
    p = accepted
    async with p.sessions.begin() as db:
        (await current_pickup(p, db)).material_confirmation_id = None
    assert await advance(p) == 1


@pytest.mark.parametrize("preparation", [{"mode": "ROTATE"}, {"mode": "REPLACE"}])
async def test_nonordinary_target_is_left_for_target_preparation_slice(accepted, preparation):
    from src.utils.canonical_json import canonical_json_digest

    p = accepted
    # 建立有效的已确认输入，而非生产实现 ROTATE/REPLACE。
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, p.accept_id)
        payload = {
            **evidence.normalized_payload,
            "data": {
                **evidence.normalized_payload["data"],
                "target_preparation": preparation,
            },
        }
        evidence.normalized_payload, evidence.payload_digest = payload, canonical_json_digest(payload)
    assert await advance(p) == 0


async def removed(p):
    from src.utils.canonical_json import canonical_json_digest

    async with p.sessions.begin() as db:
        row = await current_pickup(p, db)
        db.add(
            InboundEvidence(
                kind="DEVICE_RESULT",
                source_identity="fixture-removed:" + p.pickup_id,
                device_code="OFFLINE-SOURCE",
                command_code=row.command_code,
                normalized_payload={},
                payload_digest=canonical_json_digest({}),
                workline_id=p.line.id,
                received_at=p.now,
                processed_at=p.now,
                apply_status="APPLIED",
            )
        )


@pytest.mark.parametrize("accepted", [False], indirect=True)
async def test_m2_source_proceeds_while_m1_put_waits_but_unknown_top_never_advances(accepted):
    p = accepted
    assert await advance(p) == 0
    async with p.sessions.begin() as db:
        assert await components(p)[2].advance_in_session(db, work_id=p.work.id) == 0
    await removed(p)
    assert await advance(p) == 1

    async def source():
        async with p.sessions.begin() as db:
            return await components(p)[2].advance_in_session(db, work_id=p.work.id)

    assert sorted(await asyncio.gather(source(), source())) == [0, 1]
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 2
        m1 = await current_pickup(p, db)
        put = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == m1.put_command_code))
        assert put.status == "PENDING" and (await db.get(MaterialExecution, p.material_id)).status == "HOLD"
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
    # M2 尚无本盘最终决定/可靠取出事实，不得继续消耗旧 M1 授权去取 M3。
    async with p.sessions.begin() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        m2 = work.consumed_pickup_id
        snapshot = await MaterialDecideResultReader().by_id(
            db, material_execution_id=p.material_id, confirmation_id=p.decide.id
        )
        await material_flow(p).apply_in_session(
            db,
            fact=MaterialDecidedFact(
                fact_id="replay:" + str(p.accept_id),
                evidence_id=str(p.accept_id),
                fact_version="1.0",
                material_execution_id=p.pickup_id,
                pickup_id=p.pickup_id,
                work_id=p.work.id,
                result=snapshot.outcome.result,
            ),
        )
        assert work.consumed_pickup_id == m2
        assert (
            await components(p)[2].consume_in_session(db, work_id=p.work.id, authorization_evidence_id=p.accept_id)
            == work.consumed_pickup_id
        )
    assert await advance(p) == 0


async def test_upgrade_restores_existing_accept_and_downgrade_preserves_put(accepted, database):
    p = accepted
    url, _ = database
    run_alembic("downgrade", "548db61057e6", database_url=url)
    async with p.sessions() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='wes_biz' AND table_name='automatic_picking_pickups' AND column_name='put_command_code'"
                )
            )
            == 0
        )
    run_alembic("upgrade", "head", database_url=url)
    assert await advance(p) == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "548db61057e6", database_url=url)
    assert "cannot discard active PUT Command reference" in error.value.stderr


async def test_empty_put_reference_migration_is_reversible(database):
    url, _ = database
    run_alembic("downgrade", "548db61057e6", database_url=url)
    run_alembic("upgrade", "head", database_url=url)


@pytest.mark.parametrize("accepted", [False], indirect=True)
async def test_put_waits_for_own_removed_fact_then_recovers_once(accepted):
    p = accepted
    assert await advance(p) == 0
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
    await removed(p)
    assert sorted(await asyncio.gather(advance(p), advance(p))) == [0, 1]


@pytest.mark.parametrize(
    "changes",
    [
        {"command_code": "FOREIGN-COMMAND"},
        {"workline_id": 999999},
        {"kind": "DEVICE_EVENT"},
        {"apply_status": "PENDING"},
    ],
)
async def test_foreign_or_unapplied_removed_fact_cannot_create_put(accepted, changes):
    from automatic_picking.application.pickup_repository import SourceObservation

    p = accepted
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == "fixture-removed:" + p.pickup_id)
        )
        if "workline_id" in changes:
            foreign_line = WorkLine(
                line_code="OTHER-7A",
                line_name="7A 外来工作线",
                line_type=p.line.line_type,
                plugin_key="automatic-picking",
            )
            db.add(foreign_line)
            await db.flush()
            changes = {"workline_id": foreign_line.id}
        for key, value in changes.items():
            setattr(fact, key, value)
        fact_id = fact.id

    class WrongObservation:
        async def read_in_session(self, _db, **_kwargs):
            return SourceObservation(True, fact_id)

    p.observations = WrongObservation()
    with pytest.raises(ValueError, match="removed fact differs from Pickup source Command"):
        await advance(p)
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).put_command_code is None
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1


async def test_put_reads_m1_source_after_work_top_has_advanced_to_m2(accepted):
    p = accepted
    async with p.sessions.begin() as db:
        assert await components(p)[2].advance_in_session(db, work_id=p.work.id) == 1
    assert await advance(p) == 1
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.consumed_pickup_id != p.pickup_id and work.top_removed_evidence_id is None
        assert (await current_pickup(p, db)).put_command_code is not None
