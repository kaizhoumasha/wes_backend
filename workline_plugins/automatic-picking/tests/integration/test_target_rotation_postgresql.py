"""ROTATE ACCEPT 的离线目标准备；位置、参数、权威结果均由 fixture 明示。"""

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from automatic_picking.application.scan_flow import AutomaticPickingScanFlow
from automatic_picking.application.work_repository import WorkRepository
from sqlalchemy import func, select, text
from test_material_flow_postgresql import initial, processor, result
from test_pickup_identity_postgresql import TARGET, components, database, prepared
from test_target_put_postgresql import advance, current_pickup
from wes_plugin_sdk import TransportRackPosition

from src.app.execution.models import InboundEvidence, MaterialExecution, PositionProjection, TransportDecisionBinding
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
POSITION = {"kind": "RACK_POSITION", "location_code": "OFFLINE-TARGET"}


async def test_pickup_retains_original_target_rotation(prepared):
    assert "target_rotation_transport_task_id" in AutomaticPickingPickup.model_fields


@pytest_asyncio.fixture
async def rotating(prepared, request):
    p = prepared
    p.pickup_id, p.scan_id, p.material_id, p.decide = await initial(p)
    data = getattr(request, "param", "ROTATE")
    if type(data) is str:
        data = {
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
            "target_preparation": {"mode": data},
        }
    p.accept_id = await result(p, p.decide, data=data)
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        # 为显式前置到位占用原因果序号；新动作必须分配后继 token。
        token = await db.scalar(text("SELECT nextval('wes_biz.transport_decision_binding_causal_token_seq')"))
        # 当前权威在位作为明确离线前提，不解释现场设备映射。
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id=TARGET.rack_id,
                workline_id=p.line.id,
                position_json=POSITION,
                arrival_face="B",
                source_operation_id=p.decide.operation_id,
                source_transport_task_id="offline-original-arrival",
                source_causal_token=token,
                source_effect_phase="FINAL_RESULT",
            )
        )
    p.observation_changes = {}
    p.observation_missing = False
    return p


def application(p, *, creator=None):
    from automatic_picking.application.target_rotation import AutomaticPickingTargetRotationFlow, MatchedTargetRotation
    from automatic_picking.application.target_rotation_repository import TargetRotationRepository

    class Reader:
        async def read_in_session(self, db, *, workline_id, material_execution_id, target):
            if p.observation_missing:
                return None
            return MatchedTargetRotation(
                **{
                    "workline_id": p.line.id,
                    "material_execution_id": p.material_id,
                    "accept_evidence_id": p.accept_id,
                    "target": TARGET,
                    "position": TransportRackPosition(POSITION["location_code"]),
                    "current_face": "B",
                    **p.observation_changes,
                }
            )

    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    return AutomaticPickingTargetRotationFlow(
        TargetRotationRepository(components(p)[0].physical_reader),
        creator or ReliableRackTransportCreator(service),
        MaterialDecideResultReader(),
        Reader(),
    )


async def ready(p, flow=None):
    async with p.sessions.begin() as db:
        return await (flow or application(p)).read_in_session(
            db, workline_id=p.line.id, material_execution_id=p.material_id, target=TARGET
        )


async def transport(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        return await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == pickup.target_rotation_transport_task_id)
        )


async def outcome(p, *, status="SUCCEEDED", face="A", location="OFFLINE-TARGET", publish=True):
    from automatic_picking.application.plugin import build_plugin
    from test_return_allocation_postgresql import processor as scan_processor

    from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher
    from src.core.uuid7 import new_uuid7
    from src.utils.timezone import timezone

    task = await transport(p)
    service = TransportService(p.sessions, TransportRepository(), N(), result_timeout=timedelta(seconds=420))
    data = {
        "transport_task_id": task.transport_task_id,
        "kind": "RACK_ROTATE",
        "outcome_revision": 1,
        "rack_id": TARGET.rack_id,
        "status": status,
    }
    if status == "SUCCEEDED":
        data.update(final_position={**POSITION, "location_code": location}, arrival_face=face)
    else:
        data.update(failure_code="OFFLINE_UNKNOWN", position_unknown=True)
    operation_id = new_uuid7()
    message = {"operation": "transport.task.resulted@v1", "operation_id": operation_id, "timestamp": 1, "data": data}
    ack = await service.record_callback(
        operation_id=operation_id,
        operation=message["operation"],
        message=message,
        payload=data,
        rejection_reason_code=None,
    )
    assert ack["code"] == "RECEIVED"
    assert await service.process_pending_evidence(100) == 1
    flow = application(p)
    batch = AutomaticPickingBatchDriver(None, None, None, None, None, target_flow=flow)
    scan = AutomaticPickingScanFlow(WorkRepository(), None, None, components(p)[1], batch_driver=batch)
    plugin = build_plugin(scan_flow=scan, batch_driver=batch, works=WorkRepository())
    publisher = InstalledPluginTransportOutcomePublisher(
        p.sessions,
        (plugin,),
        queue_gateway=N(enqueue_execution_facts=lambda: None, enqueue_picking_task_plans=lambda: None),
    )
    p.now = timezone.now_for_db()
    if publish:
        assert await service.publish_pending_outcomes(100, publisher) == 1
        assert await scan_processor(p, scan=scan).process_batch() >= 1
    return service, publisher, scan


async def assert_no_followup(p):
    from src.app.execution.models import WmsConfirmation

    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.movement_confirmation_id is None
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.material.movement_report@v1")
            )
            == 0
        )
        assert (
            await db.scalar(select(func.count()).select_from(TransportTask).where(TransportTask.kind == "RACK_ROTATE"))
            == 1
        )


async def test_unique_creation_restart_and_success_before_ack_stop_at_ready(rotating):
    p = rotating
    assert await advance(p, application(p)) == 1
    original = await transport(p)
    assert original.status == "PENDING" and original.request_json["rcs_template_id"] == "CTU02"
    assert await ready(p) is None
    p.observation_missing = True
    assert await advance(p, application(p)) == 0  # 重建恢复先保留原动作，不再读取准备参数。
    service, publisher, _ = await outcome(p)
    observed = await ready(p)
    assert observed.target == TARGET and observed.material_execution_id == p.material_id
    assert await advance(p, application(p)) == 0
    assert await service.publish_pending_outcomes(100, publisher) == 0
    assert (await transport(p)).transport_task_id == original.transport_task_id
    await assert_no_followup(p)


async def test_concurrent_batch_scans_create_and_link_one_original(rotating):
    p = rotating
    assert sorted(await asyncio.gather(*(advance(p, application(p)) for _ in range(4)))) == [0, 0, 0, 1]
    await assert_no_followup(p)


@pytest.mark.parametrize("crash", ["after_transport", "after_link"])
async def test_transaction_crash_rolls_back_binding_transport_and_link(rotating, crash):
    p = rotating
    flow = application(p)
    if crash == "after_transport":
        original = flow.transports

        class Crash:
            async def create_rotate(self, *args, **kwargs):
                await original.create_rotate(*args, **kwargs)
                raise RuntimeError("offline crash")

        flow.transports = Crash()
    with pytest.raises(RuntimeError, match="offline crash"):
        async with p.sessions.begin() as db:
            await flow.advance_in_session(db, line_id=p.line.id, now=p.now)
            raise RuntimeError("offline crash")
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).target_rotation_transport_task_id is None
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0
        assert await db.scalar(select(func.count()).select_from(TransportDecisionBinding)) == 0
    assert await advance(p, application(p)) == 1
    await assert_no_followup(p)


@pytest.mark.parametrize("changed", ["identity", "target", "position", "face", "unknown"])
async def test_wrong_or_unknown_creation_inputs_never_create(rotating, changed):
    p = rotating
    if changed == "identity":
        p.observation_changes = {"accept_evidence_id": p.accept_id + 100}
    elif changed == "target":
        p.observation_changes = {"target": replace(TARGET, rack_id="OTHER")}
    elif changed == "position":
        p.observation_changes = {"position": TransportRackPosition("WRONG")}
    elif changed == "face":
        p.observation_changes = {"current_face": "A"}
    else:
        async with p.sessions.begin() as db:
            projection = await db.scalar(select(PositionProjection))
            projection.position_unknown = True
    if changed in {"identity", "target"}:
        with pytest.raises(ValueError, match="identity"):
            await advance(p, application(p))
    else:
        assert await advance(p, application(p)) == 0
    assert await transport(p) is None


@pytest.mark.parametrize(
    "status,face,location",
    [
        ("FAILED", "A", "OFFLINE-TARGET"),
        ("SUCCEEDED", "B", "OFFLINE-TARGET"),
        ("SUCCEEDED", "A", "WRONG"),
    ],
)
async def test_wrong_result_or_unknown_never_ready_or_recreate(rotating, status, face, location):
    p = rotating
    await advance(p, application(p))
    original = (await transport(p)).transport_task_id
    await outcome(p, status=status, face=face, location=location)
    assert await ready(p) is None
    assert await advance(p, application(p)) == 0
    assert (await transport(p)).transport_task_id == original
    await assert_no_followup(p)


async def test_unpublished_result_waits_for_factprocessor_and_old_fact_cannot_release(rotating):
    from test_return_allocation_postgresql import processor as scan_processor

    p = rotating
    await advance(p, application(p))
    service, publisher, scan = await outcome(p, publish=False)
    assert await ready(p) is None
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await ready(p) is None  # Transport 发布不等于插件事实发布。
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    assert await ready(p) is not None
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask))
        task.outcome_version += 1
    assert await ready(p) is None
    assert await advance(p, application(p)) == 0
    await assert_no_followup(p)


async def test_superseded_projection_and_broken_identity_do_not_release(rotating):
    p = rotating
    await advance(p, application(p))
    await outcome(p)
    assert await ready(p) is not None
    async with p.sessions.begin() as db:
        projection = await db.scalar(select(PositionProjection))
        projection.source_transport_task_id = "independent-later-motion"
    assert await ready(p) is None
    async with p.sessions.begin() as db:
        binding = await db.scalar(select(TransportDecisionBinding))
        binding.source_evidence_id = p.scan_id
    with pytest.raises(ValueError, match="identity"):
        await ready(p)


async def test_cancel_blocks_new_action_but_retains_original_for_recovery(rotating):
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = rotating
    async with p.sessions.begin() as db:
        member = await db.scalar(select(PickingTaskBinSourceRack))
        member.cancelled_evidence_id = p.scan_id
    assert await advance(p, application(p)) == 0
    async with p.sessions.begin() as db:
        member = await db.scalar(select(PickingTaskBinSourceRack))
        member.cancelled_evidence_id = None
    await advance(p, application(p))
    async with p.sessions.begin() as db:
        member = await db.scalar(select(PickingTaskBinSourceRack))
        member.cancelled_evidence_id = p.scan_id
    p.observation_missing = True
    assert await advance(p, application(p)) == 0
    await outcome(p)
    assert await ready(p) is not None  # 真实事实留存；未来 PUT 仍须独立检查业务准入。
    await assert_no_followup(p)


@pytest.mark.parametrize("rotating", ["REPLACE"], indirect=True)
async def test_replace_is_not_admitted_by_rotate_flow(rotating):
    assert await advance(rotating, application(rotating)) == 0
    assert await transport(rotating) is None


async def test_accept_not_published_does_not_create(rotating):
    p = rotating
    async with p.sessions.begin() as db:
        fact = await db.get(InboundEvidence, p.accept_id)
        fact.published_at = None
    with pytest.raises(ValueError, match="published ACCEPT"):
        await advance(p, application(p))
    assert await transport(p) is None


async def test_fact_replay_and_foreign_identity_are_isolated(rotating):
    import copy

    from src.utils.canonical_json import canonical_json_digest

    p = rotating
    await advance(p, application(p))
    await outcome(p)
    flow = application(p)
    async with p.sessions.begin() as db:
        task = await db.scalar(select(TransportTask))
        evidence = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == task.transport_task_id, InboundEvidence.kind == "TRANSPORT_RESULT"
            )
        )
        assert (
            await flow.apply_transport_result_in_session(db, line=p.line, evidence=evidence)
            == "TARGET_ROTATION_OBSERVED"
        )
        for key, value in (
            ("client_request_id", "foreign"),
            ("source_evidence_id", p.scan_id),
            ("outcome_version", task.outcome_version + 1),
            ("step", "foreign"),
        ):
            payload = {**copy.deepcopy(evidence.normalized_payload), key: value}
            changed = N(
                **{**evidence.__dict__, "normalized_payload": payload, "payload_digest": canonical_json_digest(payload)}
            )
            assert await flow.apply_transport_result_in_session(db, line=p.line, evidence=changed) == "RECONCILING"
    assert await ready(p) is not None
    await assert_no_followup(p)


async def test_schema_upgrade_preserves_existing_pickup_and_downgrade_protects_link(rotating, database):
    import subprocess

    from sqlalchemy import inspect

    from tests.support.postgresql_heavy import run_alembic

    p = rotating
    url, _ = database
    async with p.sessions() as db:
        row_id = (await current_pickup(p, db)).id
    run_alembic("downgrade", "c094b575576f", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.id == row_id and pickup.target_rotation_transport_task_id is None
        conn = await db.connection()

        def schema(sync):
            inspector = inspect(sync)
            return (
                inspector.get_columns("automatic_picking_pickups", schema="wes_biz"),
                inspector.get_foreign_keys("automatic_picking_pickups", schema="wes_biz"),
                inspector.get_unique_constraints("automatic_picking_pickups", schema="wes_biz"),
            )

        columns, fks, unique = await conn.run_sync(schema)
        assert any(c["name"] == "target_rotation_transport_task_id" and c["nullable"] for c in columns)
        assert any(
            f["constrained_columns"] == ["target_rotation_transport_task_id"]
            and f["referred_table"] == "transport_tasks"
            for f in fks
        )
        assert any(u["column_names"] == ["target_rotation_transport_task_id"] for u in unique)
    await advance(p, application(p))
    original = (await transport(p)).transport_task_id
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "c094b575576f", database_url=url)
    assert "cannot discard automatic target rotation handoff" in error.value.stderr
    assert (await transport(p)).transport_task_id == original
