"""7C：报告确认后关闭本盘；当前 Work 不依赖可清理的已闭合前盘。"""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import delete, func, select
from test_pickup_identity_postgresql import OTHER, TARGET, FixtureObservations, components, consume
from test_placement_report_postgresql import (
    accepted,
    application,
    current_pickup,
    database,
    placed,
    prepared,
    processor,
    rotated_placed,
    rotating,
    target,
)
from test_work_and_task_progress_postgresql import workflow

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROTATE_ACCEPT = {
    "result": "ACCEPT",
    "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
    "target_preparation": {"mode": "ROTATE"},
}
ROTATE_WITH_NEXT = {**ROTATE_ACCEPT, "next_source_action": asdict(OTHER)}


async def report_result(p, code="RECORDED"):
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        report = await db.get(WmsConfirmation, pickup.movement_confirmation_id)
        payload = {"operation_id": report.operation_id, "timestamp": 1, "code": code, "data": {}}
        evidence = InboundEvidence(
            kind="WMS_RESULT",
            source_identity="fixture-report7c:" + new_uuid7(),
            operation=report.operation,
            operation_id=report.operation_id,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            workline_id=p.line.id,
            material_execution_id=p.material_id,
            received_at=p.now,
            processed_at=p.now,
            apply_status="APPLIED",
            contract_version="1.0",
        )
        db.add(evidence)
        await db.flush()
        report.status, report.response_evidence_id, report.response_result, report.completed_at = (
            "COMPLETED",
            evidence.id,
            code,
            p.now,
        )
        p.report_id, p.report_result_id = report.id, evidence.id
    return evidence.id


@pytest_asyncio.fixture
async def reported(placed):
    p = placed
    assert await processor(p).process_batch() == 1
    await report_result(p)
    return p


@pytest_asyncio.fixture
async def rotated_reported(rotated_placed):
    p = rotated_placed
    assert await processor(p).process_batch() == 1
    await report_result(p)
    return p


@pytest.mark.parametrize("rotating", [ROTATE_ACCEPT, ROTATE_WITH_NEXT], indirect=True, ids=["no-next", "next"])
@pytest.mark.parametrize("code", ["RECORDED", "DUPLICATE"])
async def test_rotated_report_closes_before_m2_with_current_work_facts(rotated_placed, code):
    await close_before_m2(rotated_placed, code)


@pytest.mark.parametrize("rotating", [ROTATE_WITH_NEXT], indirect=True)
@pytest.mark.parametrize("point", ["after_closed", "after_work"])
async def test_rotated_close_and_work_roll_back_then_original_result_recovers(rotated_reported, monkeypatch, point):
    await rollback_close(rotated_reported, monkeypatch, point)


@pytest.mark.parametrize("rotating", [ROTATE_WITH_NEXT], indirect=True)
async def test_rotated_close_concurrent_source_admission_keeps_new_top(rotated_reported):
    await concurrent_close(rotated_reported)


@pytest.mark.parametrize(
    "next_source,rotating", [(False, ROTATE_ACCEPT), (True, ROTATE_WITH_NEXT)], indirect=["rotating"]
)
async def test_rotated_closed_history_cleanup_leaves_current_work_independently_recoverable(
    rotated_placed, next_source
):
    await recover_current_work(rotated_placed, next_source)


@pytest.mark.parametrize("rotating", [ROTATE_WITH_NEXT], indirect=True)
@pytest.mark.parametrize("advanced", [False, True])
async def test_rotated_close_does_not_rehandoff_consumed_or_advanced_authorization(rotated_placed, advanced):
    await preserve_authorization(rotated_placed, advanced)


@pytest.mark.parametrize("rotating", [ROTATE_WITH_NEXT], indirect=True)
async def test_rotated_report_replay_after_m2_consumed_keeps_work_identity(rotated_reported):
    await replay_after_consumed(rotated_reported)


@pytest.mark.parametrize("drift", ["report_ref", "put_unpublished"])
async def test_rotated_report_with_invalid_preparation_or_proof_never_closes(rotated_reported, drift, caplog):
    p = rotated_reported
    async with p.sessions.begin() as db:
        if drift == "report_ref":
            pickup = await current_pickup(p, db)
            pickup.movement_confirmation_id = p.decide.id
        else:
            position = await db.get(InboundEvidence, p.put_evidence_id)
            position.published_at = None
            position.decision_next_attempt_at = p.now + timedelta(days=1)
    assert await processor(p).process_batch() == 0
    assert (await state(p))[0] != "CLOSED" and (await state(p))[4] is None


@pytest.mark.parametrize("code", ["RECORDED", "DUPLICATE"])
async def test_close_and_work_removed_fact_commit_together_before_m2_exists(placed, code):
    await close_before_m2(placed, code)


async def close_before_m2(p, code):
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).top_removed_evidence_id is None
    await report_result(p, code)
    p.identity_available = False  # 已冻结报告不重新索取设备映射。
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        removed = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == "fixture-removed:" + p.pickup_id)
        )
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        assert work.authorization_evidence_id == p.accept_id and work.consumed_pickup_id is None
        assert work.top_removed_evidence_id == removed.id and work.phase == "READY"
        assert (await db.get(InboundEvidence, p.report_result_id)).published_at is not None
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1
    assert await processor(p).process_batch() == 0


async def state(p):
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        result = await db.get(InboundEvidence, p.report_result_id)
        execution = await db.get(MaterialExecution, p.material_id)
        return (
            execution.status,
            work.authorization_evidence_id,
            work.consumed_pickup_id,
            work.top_removed_evidence_id,
            result.published_at,
            result.decision_digest,
        )


@pytest.mark.parametrize("point", ["after_closed", "after_work"])
async def test_close_work_and_publication_roll_back_then_recover(reported, monkeypatch, point):
    await rollback_close(reported, monkeypatch, point)


async def rollback_close(p, monkeypatch, point):
    app = application(p)
    original = app.put_flow.apply_in_session

    async def crash(db, *, fact):
        assert (await db.get(MaterialExecution, p.material_id)).status == "CLOSED"
        if point == "after_work":
            await original(db, fact=fact)
            assert (await db.get(AutomaticPickingWork, p.work.id)).top_removed_evidence_id is not None
        raise RuntimeError("fixture crash after close")

    monkeypatch.setattr(app.put_flow, "apply_in_session", crash)
    assert await processor(p, app=app).process_batch() == 0
    before = await state(p)
    assert before[0] != "CLOSED" and before[3:] == (None, None, None)
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_claim_crash_and_existing_7b_deferred_result_recover(reported):
    from src.app.execution.repositories import inbound_evidence_repository

    p = reported
    async with p.sessions.begin() as db:
        result = await db.get(InboundEvidence, p.report_result_id)
        result.decision_next_attempt_at = p.now
        execution = await db.get(MaterialExecution, p.material_id)
        execution.status = "HOLD"  # 7B 留存的报告结果没有决定摘要，可直接由原 owner 续领。
        claimed = await inbound_evidence_repository.claim_decision_batch(
            db,
            now=p.now,
            claim_token="dead-7c-worker",
            claim_expires_at=p.now + timedelta(seconds=30),
            limit=100,
        )
        assert [row.id for row in claimed] == [p.report_result_id]
    assert await processor(p).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_concurrent_report_consumers_and_source_admission_preserve_new_top(reported):
    await concurrent_close(reported)


async def concurrent_close(p):

    async def source():
        return await consume(p, p.accept_id)

    async with asyncio.timeout(15):
        results = await asyncio.gather(processor(p).process_batch(), processor(p).process_batch(), source())
    assert sum(results[:2]) == 1 and results[2] is not None
    after = await state(p)
    assert after[0] == "CLOSED" and after[1:4] == (p.accept_id, results[2], None)
    assert await processor(p).process_batch() == 0


@pytest.mark.parametrize("next_source,accepted", [(True, True), (False, "no_next_source")], indirect=["accepted"])
async def test_restart_and_cleanable_history_removal_recover_from_current_work(placed, next_source):
    await recover_current_work(placed, next_source)


async def recover_current_work(p, next_source):
    from automatic_picking.application.batch_driver import AutomaticPickingBatchDriver
    from automatic_picking.application.pickup_model import AutomaticPickingPickup
    from automatic_picking.application.pickup_repository import SourceObservation
    from automatic_picking.application.work_repository import WorkRepository

    from src.app.workline.models import WorkLine

    assert await processor(p).process_batch() == 1
    await report_result(p)
    assert await processor(p).process_batch() == 1
    frozen = await state(p)
    assert frozen[0] == "CLOSED" and frozen[2] is None and frozen[3] is not None
    async with p.sessions.begin() as db:
        old = await current_pickup(p, db)
        codes = [old.command_code, old.put_command_code]
        await db.execute(delete(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == p.pickup_id))
        await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code.in_(codes)))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id == p.material_id))
        # 当前授权/取出 Evidence 及其必要 owner 仍是当前数据；只删已无引用的 PUT 历史。
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == p.put_evidence_id))

    class CurrentOnly(FixtureObservations):
        async def read_in_session(self, db, *, work, source, pickup):
            assert pickup is None or pickup.pickup_id != p.pickup_id
            assert work.authorization_evidence_id == frozen[1]
            if pickup is None:
                assert work.top_removed_evidence_id == frozen[3]
            return SourceObservation(True, work.top_removed_evidence_id)

    p.observations = CurrentOnly()
    repo, _, source_flow = components(p)

    async def forbidden(*args, **kwargs):
        raise AssertionError("closed M1 must not be read for current Work recovery")

    repo.material_context_for_update = repo.material_execution = repo.material_source = repo.movement_confirmation = (
        forbidden
    )
    driver = AutomaticPickingBatchDriver(
        WorkRepository(),
        N(list_recoverable=empty),
        workflow(),
        None,
        None,
        source_flow=source_flow,
    )
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        assert await driver.advance_completed_in_session(db, line) == int(next_source)
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        rows = (await db.scalars(select(AutomaticPickingPickup))).all()
        assert len(rows) == int(next_source)
        assert work.authorization_evidence_id == p.accept_id
        if next_source:
            assert rows[0].pickup_id != p.pickup_id and work.consumed_pickup_id == rows[0].pickup_id
        else:
            assert work.consumed_pickup_id is None and work.top_removed_evidence_id == frozen[3]
    async with p.sessions.begin() as db:
        assert await driver.advance_completed_in_session(db, await db.get(WorkLine, p.line.id)) == 0


async def empty(*args):
    return []


@pytest.mark.parametrize("advanced", [False, True])
async def test_report_close_does_not_rehandoff_or_overwrite_consumed_or_advanced_authorization(placed, advanced):
    await preserve_authorization(placed, advanced)


async def preserve_authorization(p, advanced):
    from test_material_flow_postgresql import bind, result, scan

    assert await processor(p).process_batch() == 1
    m2 = await consume(p, p.accept_id)
    if advanced:
        scan_id = await scan(p, m2)
        material_id = await bind(p, scan_id)
        assert await processor(p).process_batch() == 1
        async with p.sessions() as db:
            confirmation = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.material_execution_id == material_id)
            )
        await result(p, confirmation)
        assert await processor(p).process_batch() == 1
    await report_result(p)
    before = (await state(p))[1:4]
    assert before[2] is None
    assert await processor(p).process_batch() == 1
    assert (await state(p))[1:4] == before
    assert (await state(p))[0] == "CLOSED"


@pytest.mark.parametrize("blocked", ["source_not_ready", "cancelled"])
async def test_next_source_admission_does_not_block_closing_real_report(reported, blocked):
    from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack

    p = reported
    if blocked == "source_not_ready":
        p.observations.ready = False
    else:
        async with p.sessions.begin() as db:
            member = await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)
            member.cancelled_evidence_id = p.accept_id
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"
    assert await consume(p, p.accept_id) is None


@pytest.mark.parametrize(
    "drift",
    [
        "report_ref",
        "result_ref",
        "put_ref",
        "put_unpublished",
        "put_digest",
        "put_not_success",
        "accept_unpublished",
        "handoff_missing",
        "source",
        "target",
        "barcode",
        "removed_command",
        "removed_digest",
    ],
)
async def test_mismatched_report_or_placement_proof_never_closes(reported, drift):
    p = reported
    async with p.sessions.begin() as db:
        pickup = await current_pickup(p, db)
        report = await db.get(WmsConfirmation, p.report_id)
        put = await db.get(InboundEvidence, p.put_evidence_id)
        accept = await db.get(InboundEvidence, p.accept_id)
        work = await db.get(AutomaticPickingWork, p.work.id)
        if drift == "report_ref":
            pickup.movement_confirmation_id = p.decide.id
        elif drift == "result_ref":
            report.response_evidence_id = p.accept_id
        elif drift == "put_ref":
            pickup.put_result_evidence_id = p.report_result_id
        elif drift == "put_unpublished":
            put.published_at = None
            put.decision_next_attempt_at = p.now + timedelta(days=1)
        elif drift == "put_digest":
            put.payload_digest = "x" * 64
        elif drift == "put_not_success":
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == p.put_code))
            command.status = "ACKNOWLEDGED"
        elif drift == "accept_unpublished":
            accept.published_at = None
            accept.decision_next_attempt_at = p.now + timedelta(days=1)
        elif drift == "handoff_missing":
            work.authorization_evidence_id = pickup.authorization_evidence_id
        elif drift == "barcode":
            report.request_payload = {
                **report.request_payload,
                "data": {**report.request_payload["data"], "barcode": "OTHER-SCAN"},
            }
            report.request_digest = canonical_json_digest(report.request_payload)
        elif drift in ("source", "target"):
            data = {**report.request_payload["data"]}
            field, key = ("source_locator", "cell_id") if drift == "source" else ("to_locator", "slot_id")
            data[field] = {**data[field], key: "DIFFERENT"}
            report.request_payload = {**report.request_payload, "data": data}
            report.request_digest = canonical_json_digest(report.request_payload)
        else:
            removed = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.source_identity == "fixture-removed:" + p.pickup_id)
            )
            if drift == "removed_command":
                removed.command_code = p.put_code
            else:
                removed.payload_digest = "x" * 64
    await processor(p).process_batch()
    after = await state(p)
    assert after[0] != "CLOSED" and after[4:] == (None, None)


@pytest.mark.parametrize("status", ["DISPATCHING", "RECONCILING", "PENDING", "SUPERSEDED"])
async def test_non_completed_report_preserves_identity_without_closing(reported, status):
    p = reported
    async with p.sessions.begin() as db:
        report = await db.get(WmsConfirmation, p.report_id)
        report.status = status
    await processor(p).process_batch()
    assert (await state(p))[0] != "CLOSED"
    async with p.sessions() as db:
        assert (await current_pickup(p, db)).movement_confirmation_id == p.report_id
        assert (await db.get(InboundEvidence, p.report_result_id)).published_at is None


async def test_missing_removed_fact_waits_then_existing_fact_owner_recovers(reported):
    from automatic_picking.application.pickup_repository import SourceObservation

    p = reported
    original = p.observations.read_in_session

    async def unavailable(*args, **kwargs):
        return SourceObservation(False, None)

    p.observations.read_in_session = unavailable
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "HOLD" and (await state(p))[3:] == (None, None, None)
    p.observations.read_in_session = original
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await state(p))[0] == "CLOSED"


async def test_replayed_report_after_m2_consumed_is_idempotent(reported):
    await replay_after_consumed(reported)


async def replay_after_consumed(p):
    assert await processor(p).process_batch() == 1
    assert await consume(p, p.accept_id) is not None
    frozen = await state(p)
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, p.report_result_id)
        evidence.published_at = None
    assert await processor(p).process_batch() == 1
    after = await state(p)
    assert after[0:4] == frozen[0:4] and after[5] == frozen[5]
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2


@pytest.mark.parametrize("drift", ["outcome", "operation_id", "old_evidence"])
async def test_fact_must_match_current_typed_report(reported, drift):
    from wes_plugin_sdk import FactRecorded, MaterialMovementReportOutcome, WmsResultReadyFact

    p = reported
    fact = WmsResultReadyFact(
        fact_id="evidence:" + str(p.report_result_id),
        evidence_id=str(p.report_result_id if drift != "old_evidence" else p.accept_id),
        fact_version="1.0",
        material_execution_id=p.pickup_id,
        operation_id=p.pickup_id if drift != "operation_id" else new_uuid7(),
        outcome=MaterialMovementReportOutcome(FactRecorded(drift == "outcome")),
    )
    async with p.sessions.begin() as db:
        with pytest.raises(ValueError):
            await application(p).put_flow.build(db, fact)
    assert (await state(p))[0] != "CLOSED"
