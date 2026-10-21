"""REPLACE 旧架已交接后新架进场；仅离线冻结 F01，停在 ReadyTarget。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from sqlalchemy import select
from test_pickup_identity_postgresql import TARGET, database, prepared
from test_target_put_postgresql import advance, current_pickup
from test_target_replacement_departure_postgresql import (
    LOCATION,
    accept,
    application,
    callback,
    departure,
    replacing,
)

from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def incoming(replacing):
    p = replacing
    async with p.sessions.begin() as db:
        db.add(
            WorkLinePosition(
                workline_id=p.line.id,
                workline_code=p.line.line_code,
                position_code=LOCATION,
                position_type="RACK_POSITION",
                position_name="offline transfer",
                position_role="SMT_TRANSFER_RACK_POSITION",
                allowed_rack_kind="FIVE_LAYER",
                logic_location_code=LOCATION,
                capacity=1,
                enabled=True,
            )
        )
    assert await advance(p, application(p)) == 1
    p.outbound = await departure(p)
    await accept(p)
    p.observed = None
    return p


async def inbound(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        return await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == pickup.target_inbound_transport_task_id)
        )


async def ready(p, app=None):
    async with p.sessions.begin() as db:
        return await (app or application(p)).replacement_flow.read_in_session(
            db, workline_id=p.line.id, material_execution_id=p.material_id, target=TARGET
        )


async def test_handoff_creates_original_new_f01_then_reads_ready_without_put(incoming):
    p = incoming
    assert await advance(p, application(p)) == 1
    task = await inbound(p)
    assert task.request_json["rack_id"] == TARGET.rack_id
    assert task.request_json["rcs_template_id"] == "F01"
    assert task.request_json["target"] == {"kind": "RACK_POSITION", "location_code": LOCATION}
    assert task.request_json["target_face"] == TARGET.rack_face
    assert await ready(p) is None
    await callback(p, task)
    observed = await ready(p)
    assert observed.target == TARGET
    assert await advance(p, application(p)) == 0
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.movement_confirmation_id is None


async def test_concurrent_recovery_links_only_one_inbound(incoming):
    p = incoming
    assert sorted(await asyncio.gather(*(advance(p, application(p)) for _ in range(4)))) == [0, 0, 0, 1]
    task = await inbound(p)
    assert await advance(p, application(p)) == 0
    assert (await inbound(p)).transport_task_id == task.transport_task_id


@pytest.mark.parametrize("point", ["created", "linked"])
async def test_inbound_creation_and_reference_roll_back_together(incoming, point):
    p = incoming
    app = application(p)
    if point == "created":
        original = p.creator.create_windowed_inbound

        class Crash:
            async def create_windowed_inbound(self, *args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("offline creation crash")

        app.replacement_flow.creator = Crash()
        with pytest.raises(RuntimeError, match="crash"):
            await advance(p, app)
    else:
        async with p.sessions() as db:
            with pytest.raises(RuntimeError, match="crash"):
                async with db.begin():
                    assert await app.advance_in_session(db, line_id=p.line.id, now=p.now) == 1
                    raise RuntimeError("offline link crash")
    assert await inbound(p) is None
    assert await advance(p, application(p)) == 1


async def test_window_wait_preserves_original_intent_then_recovers(incoming):
    from src.app.execution.models import TransportDecisionBinding
    from src.utils.timezone import timezone

    p = incoming
    async with p.sessions.begin() as db:
        window = await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.client_request_id == p.inbound.client_request_id
            )
        )
        window.window_released_at = None
    assert await advance(p, application(p)) == 0
    assert await inbound(p) is None
    async with p.sessions.begin() as db:
        window = await db.get(TransportDecisionBinding, window.id)
        window.window_released_at = timezone.now_for_db()
    assert await advance(p, application(p)) == 1


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "REJECTED"])
async def test_no_normal_departure_handoff_never_creates_new_inbound(incoming, status):
    p = incoming
    async with p.sessions.begin() as db:
        old = await db.get(TransportTask, p.outbound.id)
        old.status = status
    assert await advance(p, application(p)) == 0
    assert await inbound(p) is None


async def test_callback_publication_replay_and_restart_do_not_create_put(incoming):
    from test_return_allocation_postgresql import processor as scan_processor

    from src.app.execution.models import InboundEvidence

    p = incoming
    assert await advance(p, application(p)) == 1
    task = await inbound(p)
    publisher, scan = await callback(p, task, publish=False)
    assert await ready(p) is None
    scan.batch_driver.target_flow = None
    assert await p.service.publish_pending_outcomes(100, publisher) == 1
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    assert await ready(p) is None
    async with p.sessions.begin() as db:
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        assert fact.published_at is None
        assert fact.decision_next_attempt_at is not None
    p.now += timedelta(seconds=2)
    scan.batch_driver.target_flow = application(p)
    assert await scan_processor(p, scan=scan).process_batch() >= 1
    assert await ready(p) is not None
    assert await scan_processor(p, scan=scan).process_batch() == 0
    assert await advance(p, application(p)) == 0
    assert (await inbound(p)).transport_task_id == task.transport_task_id
    async with p.sessions() as db:
        assert (await db.get(TransportTask, task.id)).submit_attempt_count == 0
        fact = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.transport_task_id == task.transport_task_id)
        )
        assert (
            await application(p).apply_transport_result_in_session(db, line=p.line, evidence=fact)
            == "TARGET_REPLACEMENT_DEPARTURE_OBSERVED"
        )
        payload = {**fact.normalized_payload, "client_request_id": "FOREIGN"}
        from src.utils.canonical_json import canonical_json_digest

        wrong = N(
            kind=fact.kind,
            apply_status=fact.apply_status,
            transport_task_id=task.transport_task_id,
            workline_id=p.line.id,
            picking_task_id=None,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
        )
        assert await application(p).apply_transport_result_in_session(db, line=p.line, evidence=wrong) == "RECONCILING"
        pickup = await current_pickup(p, db)
        assert pickup.put_command_code is None and pickup.movement_confirmation_id is None


@pytest.mark.parametrize("bad", ["face", "unknown", "old-round", "version", "released"])
async def test_wrong_current_arrival_cannot_be_ready_or_start_a_new_round(incoming, bad):
    from src.app.execution.models import PositionProjection, TransportDecisionBinding
    from src.utils.timezone import timezone

    p = incoming
    assert await advance(p, application(p)) == 1
    task = await inbound(p)
    await callback(p, task)
    assert await ready(p) is not None
    async with p.sessions.begin() as db:
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == TARGET.rack_id))
        if bad == "face":
            position.arrival_face = "B"
        elif bad == "unknown":
            position.position_unknown = True
        elif bad == "old-round":
            position.source_transport_task_id = p.inbound.transport_task_id
            position.source_causal_token -= 1
        elif bad == "version":
            (await db.get(TransportTask, task.id)).published_outcome_version = 0
        else:
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            binding.window_released_at = timezone.now_for_db()
    assert await ready(p) is None
    assert await advance(p, application(p)) == 0
    assert (await inbound(p)).transport_task_id == task.transport_task_id


async def test_closed_predecessor_cleanup_keeps_new_arrival_independently_recoverable(incoming):
    from sqlalchemy import delete

    from src.app.execution.models import InboundEvidence, TransportDecisionBinding
    from src.app.transport.models import TransportMember
    from src.app.wms_integration.outbound_picking.models import PickingTask

    p = incoming
    assert await advance(p, application(p)) == 1
    task = await inbound(p)
    await callback(p, p.outbound)
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        pickup.target_departure_transport_task_id = None
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
        await db.flush()
        for old in [p.outbound, p.inbound]:
            await db.execute(delete(TransportMember).where(TransportMember.transport_task_id == old.transport_task_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.transport_task_id == old.transport_task_id))
            await db.execute(delete(TransportTask).where(TransportTask.id == old.id))
            await db.execute(
                delete(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == old.client_request_id
                )
            )
    assert await advance(p, application(p)) == 0
    await callback(p, task)
    assert await ready(p) is not None
    assert await advance(p, application(p)) == 0


async def test_new_reference_migration_retains_legacy_pickup_and_refuses_loss(incoming):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = incoming
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        pickup.target_departure_confirmation_id = pickup.target_departure_round_id = (
            pickup.target_departure_retry_at
        ) = None
    url = p.sessions.kw["bind"].url.render_as_string(hide_password=False)
    run_alembic("downgrade", "6a7b8c9d0112", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert await advance(p, application(p)) == 1
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "6a7b8c9d0112", database_url=url)
    assert "cannot discard automatic target inbound handoff" in error.value.stderr


async def test_two_materials_share_the_same_new_arrival_round(incoming):
    from dataclasses import asdict

    from test_material_flow_postgresql import bind, result, scan
    from test_material_flow_postgresql import processor as material_processor
    from test_pickup_identity_postgresql import consume
    from test_rotated_target_put_postgresql import removed

    from src.app.execution.models import WmsConfirmation

    p = incoming
    assert await advance(p, application(p)) == 1
    original = await inbound(p)
    await removed(p)
    pickup_id = await consume(p, p.accept_id)
    material_id = await bind(p, await scan(p, pickup_id))
    assert await material_processor(p).process_batch() == 1
    async with p.sessions() as db:
        confirmation = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.material_execution_id == material_id)
        )
    accepted = await result(
        p,
        confirmation,
        data={
            "result": "ACCEPT",
            "target_locator": {"type": "RACK_SLOT", **asdict(TARGET), "slot_id": "S2", "rack_column": 2},
            "target_preparation": {"mode": "REPLACE"},
        },
    )
    assert await material_processor(p).process_batch() == 1
    p.pickup_id = pickup_id
    from automatic_picking.application.target_replacement import MatchedTargetDeparture

    p.observed = MatchedTargetDeparture(
        p.line.id,
        material_id,
        accepted,
        "OLD-TARGET",
        p.inbound.client_request_id,
        p.arrival_id,
        p.departure_operation_id,
    )
    assert await advance(p, application(p)) == 1  # 旧架已接纳后仍关联原F01。
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).target_departure_transport_task_id == p.outbound.transport_task_id
    assert await advance(p, application(p)) == 1
    assert (await inbound(p)).transport_task_id == original.transport_task_id


async def test_failed_new_arrival_preserves_original_identity_without_readiness(incoming):
    p = incoming
    assert await advance(p, application(p)) == 1
    task = await inbound(p)
    await callback(p, task, status="FAILED")
    assert await ready(p) is None
    assert await advance(p, application(p)) == 0
    assert (await inbound(p)).transport_task_id == task.transport_task_id
