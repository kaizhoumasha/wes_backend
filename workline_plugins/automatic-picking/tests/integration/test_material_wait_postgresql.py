"""6D 持久 WAIT：真实 FactProcessor/Confirmation/事务，设备关联沿6C显式输入。"""

import asyncio
import subprocess
from datetime import timedelta

import pytest
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import delete, func, select, text
from test_material_flow_postgresql import flow, initial, processor, result
from test_pickup_identity_postgresql import database, prepared

from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from tests.support.postgresql_heavy import run_alembic

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def pickup(p, db):
    return await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.work_id == p.work.id))


async def waiting(p):
    identity, _, material, request = await initial(p)
    event = await result(p, request, data={"result": "WAIT", "retry_after_ms": 1000})
    assert await processor(p).process_batch() == 1
    return identity, material, request, event


async def advance(p, now):
    async with p.sessions.begin() as db:
        return await flow(p).advance_in_session(db, line_id=p.line.id, now=now)


async def test_wait_due_is_durable_and_creates_one_new_request_under_concurrency(prepared):
    p = prepared
    identity, material, original, _ = await waiting(p)
    async with p.sessions() as db:
        row = await pickup(p, db)
        assert row.material_confirmation_id == original.id
        assert row.material_retry_at == p.now + timedelta(seconds=1)
    assert await advance(p, p.now) == 0
    results = await asyncio.gather(advance(p, p.now + timedelta(seconds=2)), advance(p, p.now + timedelta(seconds=2)))
    assert sorted(results) == [0, 1]
    async with p.sessions() as db:
        row = await pickup(p, db)
        current = await db.get(WmsConfirmation, row.material_confirmation_id)
        assert current.id != original.id and current.operation_id != original.operation_id
        assert current.material_execution_id == material and row.material_retry_at is None
        assert current.request_payload["data"] == original.request_payload["data"]
        assert current.request_payload["timestamp"] != original.request_payload["timestamp"]
        assert await db.scalar(select(func.count()).select_from(AutomaticPickingPickup)) == 1
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert work.consumed_pickup_id == identity and work.authorization_evidence_id == p.ready.id
    assert await processor(p).process_batch() == 0


async def test_wait_wait_accept_uses_current_request_identity_and_original_work_handoff(prepared):
    p = prepared
    _, material, _, _ = await waiting(p)
    for i in (2, 4):
        await advance(p, p.now + timedelta(seconds=i))
        async with p.sessions() as db:
            request = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
        event = await result(
            p,
            request,
            data={"result": "WAIT", "retry_after_ms": 1000} if i == 2 else None,
            received_at=p.now + timedelta(seconds=i),
        )
        assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        row = await pickup(p, db)
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert row.accept_evidence_id == work.authorization_evidence_id == event
        assert row.material_retry_at is None and work.consumed_pickup_id is None
        assert (await db.get(MaterialExecution, material)).status == "HOLD"
    assert await advance(p, p.now + timedelta(days=1)) == 0


@pytest.mark.parametrize("stage", ["create", "cursor"])
async def test_due_request_and_cursor_roll_back_together_and_recover(prepared, monkeypatch, stage):
    p = prepared
    _, _, original, _ = await waiting(p)
    application = flow(p)
    create = application.scheduler.create_in_session

    async def crash(*args, **kwargs):
        await create(*args, **kwargs)
        raise RuntimeError("crash after create")

    if stage == "create":
        monkeypatch.setattr(application.scheduler, "create_in_session", crash)
    with pytest.raises(RuntimeError, match="crash"):
        async with p.sessions.begin() as db:
            await application.advance_in_session(db, line_id=p.line.id, now=p.now + timedelta(seconds=2))
            raise RuntimeError("crash after cursor")
    async with p.sessions() as db:
        row = await pickup(p, db)
        assert row.material_confirmation_id == original.id and row.material_retry_at == p.now + timedelta(seconds=1)
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
    assert await advance(p, p.now + timedelta(seconds=2)) == 1


async def test_wait_due_and_publication_roll_back_together(prepared, monkeypatch):
    p = prepared
    _, _, _, request = await initial(p)
    event = await result(p, request, data={"result": "WAIT", "retry_after_ms": 1000})
    application = flow(p)
    apply = application.apply_in_session

    async def crash(*args, **kwargs):
        await apply(*args, **kwargs)
        raise RuntimeError("after due, before publication")

    monkeypatch.setattr(application, "apply_in_session", crash)
    assert await processor(p, application=application).process_batch() == 0
    async with p.sessions() as db:
        assert (await pickup(p, db)).material_retry_at is None
        assert (await db.get(InboundEvidence, event)).published_at is None
    # batch 不能代替 FactProcessor 应用尚未发布的确定结果。
    assert await advance(p, p.now + timedelta(days=1)) == 0
    original_due = p.now + timedelta(seconds=1)
    p.now += timedelta(minutes=2)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        assert (await pickup(p, db)).material_retry_at == original_due


@pytest.mark.parametrize("status", ["PENDING", "DISPATCHING", "RECONCILING"])
async def test_current_unknown_never_becomes_new_business_request_on_timeout(prepared, status):
    p = prepared
    await waiting(p)
    await advance(p, p.now + timedelta(seconds=2))
    async with p.sessions.begin() as db:
        current = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
        current.status = status
        frozen = current.request_payload
        identity = current.id
    assert await advance(p, p.now + timedelta(days=1)) == 0
    async with p.sessions() as db:
        row = await pickup(p, db)
        assert row.material_confirmation_id == identity and row.material_retry_at is None
        assert (await db.get(WmsConfirmation, identity)).request_payload == frozen
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2


async def test_old_wait_replay_does_not_rewind_current_request_or_accept(prepared):
    p = prepared
    identity, _, _, old_event = await waiting(p)
    async with p.sessions() as db:
        row = await db.get(InboundEvidence, old_event)
        from automatic_picking.handlers.material_flow import MaterialDecidedFact
        from wes_plugin_sdk import PickingMaterialWait

        stale = MaterialDecidedFact(
            fact_id="old",
            evidence_id=str(old_event),
            fact_version="1",
            material_execution_id=identity,
            pickup_id=identity,
            work_id=p.work.id,
            result=PickingMaterialWait(1000),
        )
        published = row.published_at
    await advance(p, p.now + timedelta(seconds=2))
    async with p.sessions.begin() as db:
        current = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
        await flow(p).apply_in_session(db, fact=stale)
        assert (await pickup(p, db)).material_confirmation_id == current.id
        assert (await pickup(p, db)).material_retry_at is None
    accepted = await result(p, current, received_at=p.now + timedelta(seconds=3))
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        await flow(p).apply_in_session(db, fact=stale)
        assert (await pickup(p, db)).accept_evidence_id == accepted
        assert (await db.get(AutomaticPickingWork, p.work.id)).authorization_evidence_id == accepted
        assert (await db.get(InboundEvidence, old_event)).published_at == published


@pytest.mark.parametrize("drift", ["request", "result", "owner"])
async def test_later_result_must_match_current_frozen_request_and_owner(prepared, drift):
    p = prepared
    await waiting(p)
    await advance(p, p.now + timedelta(seconds=2))
    async with p.sessions() as db:
        request = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
    event = await result(p, request)
    from src.utils.canonical_json import canonical_json_digest

    async with p.sessions.begin() as db:
        row = await db.get(WmsConfirmation, request.id)
        if drift == "request":
            payload = {**row.request_payload, "data": {**row.request_payload["data"], "barcode": "another reel"}}
            row.request_payload, row.request_digest = payload, canonical_json_digest(payload)
        elif drift == "result":
            evidence = await db.get(InboundEvidence, event)
            payload = {**evidence.normalized_payload, "operation_id": request.operation_id[::-1]}
            evidence.normalized_payload, evidence.payload_digest = payload, canonical_json_digest(payload)
        else:
            evidence = await db.get(InboundEvidence, event)
            evidence.material_execution_id = None
    assert await processor(p).process_batch() == 0
    async with p.sessions() as db:
        assert (await pickup(p, db)).accept_evidence_id is None
        assert (await db.get(InboundEvidence, event)).published_at is None
        assert (await db.get(AutomaticPickingWork, p.work.id)).authorization_evidence_id == p.ready.id


@pytest.mark.parametrize("cancelled", [True, False])
async def test_direct_cancel_stops_new_question_but_parent_completion_is_not_cancellation(prepared, cancelled):
    p = prepared
    await waiting(p)
    async with p.sessions.begin() as db:
        task = await db.get(PickingTask, p.task.id)
        task.status = "EXECUTION_COMPLETED"
        if cancelled:
            member = await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)
            member.cancelled_evidence_id = p.task.issued_evidence_id
    assert await advance(p, p.now + timedelta(seconds=2)) == int(not cancelled)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == (1 if cancelled else 2)


async def test_current_request_restores_without_closed_previous_wait_history(prepared):
    p = prepared
    _, material, old, old_event = await waiting(p)
    await advance(p, p.now + timedelta(seconds=2))
    async with p.sessions() as db:
        current = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
    event = await result(
        p, current, data={"result": "WAIT", "retry_after_ms": 1000}, received_at=p.now + timedelta(seconds=3)
    )
    assert await processor(p).process_batch() == 1
    await advance(p, p.now + timedelta(seconds=4))
    async with p.sessions() as db:
        current_request = await db.get(WmsConfirmation, (await pickup(p, db)).material_confirmation_id)
    await result(
        p, current_request, data={"result": "WAIT", "retry_after_ms": 1000}, received_at=p.now + timedelta(seconds=5)
    )
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        # 相同 HOLD 不重写迁移事实；原 WAIT Evidence 仍是当前 ME 的事实，不能冒充可清理历史。
        assert (await db.get(MaterialExecution, material)).last_transition_evidence_id == old_event
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id.in_([old.id, current.id])))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == event))
    assert await advance(p, p.now + timedelta(seconds=6)) == 1


async def test_migration_restores_6c_published_wait_once_without_republishing(prepared, database):
    p = prepared
    url, _ = database
    _, _, original, event = await waiting(p)
    async with p.sessions.begin() as db:
        row = await pickup(p, db)
        row.material_confirmation_id = row.material_retry_at = None
        published = (await db.get(InboundEvidence, event)).published_at
    run_alembic("downgrade", "807514a7776b", database_url=url)
    async with p.sessions() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='wes_biz' AND table_name='automatic_picking_pickups' AND column_name='material_confirmation_id'"
                )
            )
            == 0
        )
    run_alembic("upgrade", "head", database_url=url)
    assert await advance(p, p.now) == 1
    assert await advance(p, p.now) == 0
    async with p.sessions() as db:
        row = await pickup(p, db)
        assert row.material_confirmation_id == original.id and row.material_retry_at == p.now + timedelta(seconds=1)
        assert (await db.get(InboundEvidence, event)).published_at == published
    assert await advance(p, p.now + timedelta(seconds=2)) == 1
    assert await processor(p).process_batch() == 0
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic("downgrade", "807514a7776b", database_url=url)
    assert "cannot discard active material decide cursor" in error.value.stderr


async def test_empty_wait_cursor_migration_is_reversible(database):
    url, _ = database
    run_alembic("downgrade", "807514a7776b", database_url=url)
    run_alembic("upgrade", "head", database_url=url)


@pytest.mark.parametrize("determinate", [True, False])
async def test_6c_null_cursor_unpublished_or_pending_request_waits_for_its_owner(prepared, determinate):
    p = prepared
    _, _, _, original = await initial(p)
    event = await result(p, original, data={"result": "WAIT", "retry_after_ms": 1000}) if determinate else None
    async with p.sessions.begin() as db:
        (await pickup(p, db)).material_confirmation_id = None
    assert await advance(p, p.now + timedelta(days=1)) == 1
    assert await advance(p, p.now + timedelta(days=1)) == 0
    async with p.sessions() as db:
        row = await pickup(p, db)
        assert row.material_confirmation_id == original.id and row.material_retry_at is None
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1
        if event:
            assert (await db.get(InboundEvidence, event)).published_at is None
    if determinate:
        assert await processor(p).process_batch() == 1


async def test_6c_missing_original_request_fails_closed_instead_of_selecting_latest(prepared):
    p = prepared
    _, _, _, original = await initial(p)
    async with p.sessions.begin() as db:
        (await pickup(p, db)).material_confirmation_id = None
        await db.flush()
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id == original.id))
    with pytest.raises(ValueError, match="published scan lost"):
        await advance(p, p.now + timedelta(seconds=2))


async def test_cached_member_is_refreshed_before_wait_retry(prepared):
    p = prepared
    await waiting(p)
    async with p.sessions.begin() as db:
        member = await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)
        assert member.cancelled_evidence_id is None
        async with p.sessions.begin() as other:
            current = await other.get(PickingTaskBinSourceRack, member.id)
            current.cancelled_evidence_id = p.task.issued_evidence_id
        assert await flow(p).advance_in_session(db, line_id=p.line.id, now=p.now + timedelta(seconds=2)) == 0
        assert member.cancelled_evidence_id is not None


async def test_fact_publication_races_batch_without_losing_due_or_creating_two_questions(prepared):
    p = prepared
    _, _, _, original = await initial(p)
    await result(p, original, data={"result": "WAIT", "retry_after_ms": 1000})
    applied, _ = await asyncio.gather(processor(p).process_batch(), advance(p, p.now + timedelta(seconds=2)))
    assert applied == 1
    await advance(p, p.now + timedelta(seconds=2))
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2
        assert (await pickup(p, db)).material_retry_at is None
