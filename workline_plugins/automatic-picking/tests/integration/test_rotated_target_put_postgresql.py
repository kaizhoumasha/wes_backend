"""ROTATE→7A PUT 接缝；完整参数/精确事实为显式离线输入，不派发。"""

import asyncio
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from sqlalchemy import func, select
from test_pickup_identity_postgresql import components, database, prepared
from test_target_put_postgresql import advance, current_pickup
from test_target_put_postgresql import flow as put_flow
from test_target_put_postgresql import removed as source_removed
from test_target_rotation_postgresql import application as rotation_flow
from test_target_rotation_postgresql import outcome, rotating, transport

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device
from src.app.execution.models import MaterialExecution, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.workline.models import WorkLine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def target(rotating):
    p = rotating
    async with p.sessions.begin() as db:
        device = Device(device_code="OFFLINE-TARGET", device_name="离线目标", work_line_id=p.line.id)
        db.add(device)
        await db.flush()
        line = await db.get(WorkLine, p.line.id)
        line.config = {"device_bindings": {"ROBOT_ARM": "OFFLINE-SOURCE", "FIXTURE_TARGET": device.device_code}}
        line.device_contracts = {
            **line.device_contracts,
            device.device_code: {
                "device_id": device.id,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "FIXTURE_ONLY",
                "contract_version": "1",
                "command_timeout_ms": 30000,
                "status_max_age_ms": 30000,
            },
        }
    p.target_ready, p.request_changes = True, {}
    return p


def combined(p):
    from automatic_picking.application.target_preparation import AutomaticPickingTargetFlow

    ordinary = put_flow(p)
    return AutomaticPickingTargetFlow(
        ordinary.repository,
        ordinary.commands,
        ordinary.material_reader,
        ordinary.target_reader,
        ordinary.request_factory,
        rotation_flow=rotation_flow(p),
    )


async def removed(p):
    from src.app.execution.models import InboundEvidence
    from src.utils.canonical_json import canonical_json_digest

    await source_removed(p)
    # 已可靠取出是本片输入，不将测试专用空载荷重送进源设备结果解析。
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == "fixture-removed:" + p.pickup_id)
        )
        fact.decision_digest = canonical_json_digest({"fixture_removed": p.pickup_id})
        fact.published_at = p.now


async def linked(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        return pickup.put_command_code


async def assert_stop_at_put(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.put_command_code))
        assert command.execution_ref_id == "put:" + p.pickup_id
        assert command.material_execution_id == p.material_id and command.status == "PENDING"
        assert command.params["target"] == {
            "rack_id": "TARGET",
            "rack_face": "A",
            "slot_id": "S1",
            "rack_layer": 1,
            "rack_column": 1,
        }
        assert pickup.put_result_evidence_id is None and pickup.movement_confirmation_id is None
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WmsConfirmation)
                .where(WmsConfirmation.operation == "outbound.material.movement_report@v1")
            )
            == 0
        )


async def test_published_rotation_ready_creates_one_put_for_own_removed_material(target):
    p = target
    await advance(p, rotation_flow(p))
    await outcome(p)
    await removed(p)
    assert await advance(p, combined(p)) == 1
    await assert_stop_at_put(p)


async def published_through_combined(p, flow):
    from test_return_allocation_postgresql import processor as scan_processor

    service, publisher, scan = await outcome(p, publish=False)
    scan.batch_driver.target_flow = flow
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    return service, publisher, scan


async def test_combined_batch_and_factprocessor_handoff_requires_published_rotation(target):
    from test_return_allocation_postgresql import processor as scan_processor

    p = target
    app = combined(p)
    await removed(p)
    assert await advance(p, app) == 1  # 只创建原 CTU02；普通 reader 即使返回 ready 也不能越过它。
    assert await linked(p) is None
    service, publisher, scan = await outcome(p, publish=False)
    scan.batch_driver.target_flow = app
    assert await advance(p, app) == 0 and await linked(p) is None
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await advance(p, app) == 0 and await linked(p) is None
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    assert await advance(p, app) == 1
    original = await linked(p)
    # 重放同一业务 Evidence 仍沿原 Action；不重新申请物料资格。
    from src.app.execution.models import InboundEvidence

    async with p.sessions.begin() as db:
        fact = await db.scalar(select(InboundEvidence).where(InboundEvidence.kind == "TRANSPORT_RESULT"))
        await scan.apply_in_session(db, fact.id, workline_id=p.line.id)
    assert await advance(p, combined(p)) == 0
    assert await linked(p) == original
    assert await service.publish_pending_outcomes(100, publisher) == 0
    await assert_stop_at_put(p)


async def test_target_ready_still_waits_for_own_removed_fact(target):
    p = target
    app = combined(p)
    assert await advance(p, app) == 1
    await published_through_combined(p, app)
    assert await advance(p, app) == 0 and await linked(p) is None
    await removed(p)
    assert await advance(p, combined(p)) == 1
    await assert_stop_at_put(p)


async def test_concurrent_ready_scans_create_and_link_one_put(target):
    p = target
    await advance(p, combined(p))
    await published_through_combined(p, combined(p))
    await removed(p)
    results = await asyncio.gather(*(advance(p, combined(p)) for _ in range(4)))
    assert sorted(results) == [0, 0, 0, 1]
    await assert_stop_at_put(p)


@pytest.mark.parametrize("point", ["created", "linked"])
async def test_rotated_put_creation_and_link_rollback_together(target, point):
    p = target
    app = combined(p)
    await advance(p, app)
    await published_through_combined(p, app)
    await removed(p)
    original_rotation = (await transport(p)).transport_task_id
    if point == "created":
        create = app.commands.create_command_in_session

        class Crash:
            async def create_command_in_session(self, db, request):
                await create(db, request)
                raise RuntimeError("offline PUT crash")

        app.commands = Crash()
    with pytest.raises(RuntimeError, match="offline PUT crash"):
        async with p.sessions.begin() as db:
            await app.advance_in_session(db, line_id=p.line.id, now=p.now)
            raise RuntimeError("offline PUT crash")
    assert await linked(p) is None
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
    assert (await transport(p)).transport_task_id == original_rotation
    assert await advance(p, combined(p)) == 1
    await assert_stop_at_put(p)


async def test_linked_put_recovers_without_reentering_readers_or_rotation(target):
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = target
    app = combined(p)
    await advance(p, app)
    await published_through_combined(p, app)
    await removed(p)
    await advance(p, app)
    async with p.sessions.begin() as db:
        code = (await current_pickup(p, db)).put_command_code
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        snapshot = (command.command_code, command.params, command.deadline_at)
        member = await db.scalar(select(PickingTaskBinSourceRack))
        member.cancelled_evidence_id = p.scan_id

    async def unavailable(*args, **kwargs):
        raise RuntimeError("reader is unavailable")

    rebuilt = combined(p)
    rebuilt.rotation_flow = N(advance_in_session=unavailable, read_in_session=unavailable)
    rebuilt.material_reader = N(by_id=unavailable, by_operation_id=unavailable)
    rebuilt.repository.physical_reader = rebuilt.target_reader = N(read_in_session=unavailable)
    assert await advance(p, rebuilt) == 0
    async with p.sessions() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        assert (command.command_code, command.params, command.deadline_at) == snapshot
    await assert_stop_at_put(p)


@pytest.mark.parametrize("bad", ["unknown", "wrong_face"])
async def test_bad_rotation_cannot_fall_back_to_ordinary_ready_reader(target, bad):
    p = target
    app = combined(p)
    await removed(p)
    await advance(p, app)
    original_rotation = (await transport(p)).transport_task_id
    from test_return_allocation_postgresql import processor as scan_processor

    service, publisher, scan = await outcome(
        p, publish=False, status="FAILED" if bad == "unknown" else "SUCCEEDED", face="B" if bad == "wrong_face" else "A"
    )
    scan.batch_driver.target_flow = app
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    assert await advance(p, combined(p)) == 0 and await linked(p) is None
    assert (await transport(p)).transport_task_id == original_rotation


@pytest.mark.parametrize("rotating", ["REPLACE"], indirect=True)
async def test_replace_never_creates_rotation_or_put(target):
    p = target
    await removed(p)
    assert await advance(p, combined(p)) == 0 and await linked(p) is None
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TransportTask)) == 0


async def test_rotate_without_explicit_flow_stays_deferred_even_if_ordinary_reader_ready(target):
    p = target
    await advance(p, rotation_flow(p))
    await outcome(p)
    await removed(p)
    assert await advance(p, put_flow(p)) == 0 and await linked(p) is None


async def test_uninstalled_rotation_consumer_preserves_unpublished_fact(target):
    from test_return_allocation_postgresql import processor as scan_processor

    from src.app.execution.models import InboundEvidence

    p = target
    await advance(p, rotation_flow(p))
    service, publisher, scan = await outcome(p, publish=False)
    scan.batch_driver.target_flow = put_flow(p)
    assert await service.publish_pending_outcomes(100, publisher) == 1
    assert await scan_processor(p, scan=scan).process_batch() == 1
    async with p.sessions() as db:
        fact = await db.scalar(select(InboundEvidence).where(InboundEvidence.kind == "TRANSPORT_RESULT"))
        assert fact.published_at is None and fact.apply_status == "APPLIED"
        assert fact.decision_next_attempt_at is not None
    assert await linked(p) is None


async def test_exact_pickup_preparation_does_not_reenter_other_material_context(target):
    p = target
    app = rotation_flow(p)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("another Pickup's ACCEPT must not be read")

    app.material_reader = N(by_id=unavailable, by_operation_id=unavailable)
    async with p.sessions.begin() as db:
        assert (
            await app.advance_in_session(
                db, line_id=p.line.id, now=p.now, pickup_id="00000000-0000-0000-0000-000000000001"
            )
            == 0
        )
    assert await transport(p) is None
