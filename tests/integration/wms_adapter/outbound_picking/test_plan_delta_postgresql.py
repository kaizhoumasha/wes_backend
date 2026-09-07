"""plan_delta 原子提交、任务级串行化与受控修正的 PostgreSQL owner。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from wes_plugin_sdk import wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.services import InboundEvidenceService
from src.app.sys.models.audit_log import AuditLog
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaEvent
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import (
    PickingTaskPlanDeltaService,
    PlanCorrectionConflictError,
)
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7

pytest_plugins = ("tests.integration.conftest",)
NOW = datetime(2026, 9, 6)
OP = "outbound.picking_task.plan_delta@v1"


def _event(task_id, revision=1, operation_id=None, **data):
    return PickingTaskPlanDeltaEvent.model_validate(
        {
            "operation": OP,
            "operation_id": operation_id or new_uuid7(),
            "timestamp": 1,
            "data": {
                "task_id": task_id,
                "plan_revision": revision,
                **({"target_rack": {"rack_id": "TARGET", "rack_face": "opaqueFace"}} if revision == 1 else {}),
                **data,
            },
        }
    )


@pytest.fixture
async def prepared(integration_session_factory):
    factory = integration_session_factory
    task_name = f"PLAN-{new_uuid7()}"
    operation_id = new_uuid7()
    async with factory.begin() as db:
        line = WorkLine(
            line_code=task_name,
            line_name="Plan test",
            line_type=LineType.MANUAL,
            run_mode=WorkLineRunMode.AUTO,
            is_active=True,
        )
        db.add(line)
        await db.flush()
        issued = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.WMS_EVENT,
            source_identity=f"outbound.picking_task.issued@v1:{operation_id}",
            operation="outbound.picking_task.issued@v1",
            operation_id=operation_id,
            normalized_payload={"data": {"task_id": task_name}},
            received_at=NOW,
            apply_status=Status.APPLIED,
        )
        await db.flush()
        task = PickingTask(
            task_id=task_name,
            task_type="MANUAL",
            status="PREPARING",
            queue_revision=1,
            dispatch_sequence=1,
            issued_at_ms=1,
            issued_evidence_id=issued.evidence.id,
            workline_id=line.id,
        )
        db.add(task)
        await db.flush()
        request = encode_request(
            wms_operations.outbound_picking_task_prepare(
                operation_id=operation_id, task_id=task_name, work_line_code=line.line_code
            ),
            timestamp=1,
        )
        response = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"{PICKING_TASK_PREPARE_OPERATION}:{operation_id}",
            operation=PICKING_TASK_PREPARE_OPERATION,
            operation_id=operation_id,
            normalized_payload={"operation_id": operation_id, "code": "PREPARE_ACCEPTED", "timestamp": 1, "data": {}},
            received_at=NOW,
            apply_status=Status.APPLIED,
        )
        confirmation = WmsConfirmation(
            operation=PICKING_TASK_PREPARE_OPERATION,
            operation_id=operation_id,
            picking_task_id=task.id,
            request_digest="a" * 64,
            request_payload=request,
            deadline_at=NOW + timedelta(seconds=30),
            status=WmsConfirmationStatus.COMPLETED,
            response_evidence_id=response.evidence.id,
            response_result="PREPARE_ACCEPTED",
            completed_at=NOW,
        )
        db.add(confirmation)
        await db.flush()
        ids = task.id, line.id, response.evidence.id, issued.evidence.id, confirmation.id
    yield task_name, ids
    async with factory.begin() as db:
        task_id, line_id, response_id, issued_id, confirmation_id = ids
        evidences = [
            *list(
                (
                    await db.scalars(
                        select(InboundEvidence.id).where(
                            InboundEvidence.normalized_payload["data"]["task_id"].as_string() == task_name
                        )
                    )
                ).all()
            ),
            response_id,
            issued_id,
        ]
        await db.execute(delete(AuditLog).where(AuditLog.args["task_id"].as_string() == task_name))
        await db.execute(delete(DirectPickExecution).where(DirectPickExecution.picking_task_id == task_id))
        await db.execute(delete(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id == confirmation_id))
        await db.execute(delete(PickingTask).where(PickingTask.id == task_id))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidences))
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidences)))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


async def test_concurrent_revision_replay_and_business_duplicate(integration_session_factory, prepared):
    task_name, ids = prepared
    service = PickingTaskPlanDeltaService(integration_session_factory)
    first = _event(
        task_name,
        added_direct_picks=[
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "SOURCE", "rack_face": " A ", "slot_id": "1"}}
        ],
    )
    results = await asyncio.gather(*(service.record(first, received_at=NOW) for _ in range(2)))
    assert sorted(result.code for result in results) == ["DUPLICATE", "RECEIVED"]
    duplicate = first.model_copy(update={"operation_id": new_uuid7(), "timestamp": 2})
    assert (await service.record(duplicate, received_at=NOW)).code == "DUPLICATE"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        members = list(
            (await db.scalars(select(DirectPickExecution).where(DirectPickExecution.picking_task_id == task.id))).all()
        )
        assert len(members) == 1
        assert task.last_applied_plan_revision == 1
        assert task.initial_plan_evidence_id == task.last_plan_evidence_id == members[0].source_evidence_id
        evidence = await db.get(InboundEvidence, task.last_plan_evidence_id)
        assert evidence.workline_id is evidence.material_execution_id is evidence.transport_task_id is None


async def test_pending_retry_only_applies_after_persisted_prepare(integration_session_factory, prepared):
    task_name, ids = prepared
    service = PickingTaskPlanDeltaService(integration_session_factory)
    first = _event(task_name)
    async with integration_session_factory.begin() as db:
        await db.execute(
            update(WmsConfirmation).where(WmsConfirmation.id == ids[4]).values(status=WmsConfirmationStatus.DISPATCHING)
        )
    assert (await service.record(first, received_at=NOW)).code == "UNAVAILABLE"
    async with integration_session_factory.begin() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.last_applied_plan_revision == 0 and task.plan_blocked_evidence_id is None
        await db.execute(
            update(WmsConfirmation).where(WmsConfirmation.id == ids[4]).values(status=WmsConfirmationStatus.COMPLETED)
        )
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"


async def test_blocked_correction_audit_and_original_identity_replay(integration_session_factory, prepared):
    task_name, ids = prepared
    service = PickingTaskPlanDeltaService(integration_session_factory)
    first = _event(task_name)
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"
    conflict = _event(task_name, 3, added_bin_source_racks=[{"rack_id": "B", "rack_face": "F"}])
    assert (await service.record(conflict, received_at=NOW)).reason_code == "REVISION_CONFLICT"
    correction = _event(task_name, 2, added_bin_source_racks=[{"rack_id": "B", "rack_face": "F"}])
    assert (await service.record(correction, received_at=NOW)).reason_code == "STATE_CONFLICT"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        correction_id = await db.scalar(
            select(InboundEvidence.id).where(InboundEvidence.source_identity == f"{OP}:{correction.operation_id}")
        )
        args = {
            "task_id": task_name,
            "blocked_evidence_id": task.plan_blocked_evidence_id,
            "correction_evidence_id": correction_id,
            "expected_version": task.version,
            "actor_id": None,
            "reason": "WMS confirmed corrected source",
            "received_at": NOW,
        }
    result = await service.apply_correction(**args)
    assert result.plan_revision == 2
    assert (await service.apply_correction(**args)) == result
    with pytest.raises(PlanCorrectionConflictError):
        await service.apply_correction(**{**args, "reason": "changed audit"})
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    assert (await service.record(conflict, received_at=NOW)).reason_code == "REVISION_CONFLICT"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.plan_blocked_evidence_id is None
        assert await db.scalar(select(AuditLog.id).where(AuditLog.args["task_id"].as_string() == task_name)) is not None


async def test_member_flush_failure_rolls_back_evidence_and_task(integration_session_factory, prepared):
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        PickingTaskPlanDeltaRepository,
    )

    class FailingMembers(PickingTaskPlanDeltaRepository):
        async def add_members(self, db, task_id, data, evidence_id):
            await super().add_members(db, task_id, data, evidence_id)
            raise RuntimeError("injected after member flush")

    task_name, ids = prepared
    first = _event(task_name)
    with pytest.raises(RuntimeError, match="injected"):
        await PickingTaskPlanDeltaService(integration_session_factory, plan_repository=FailingMembers()).record(
            first, received_at=NOW
        )
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.last_applied_plan_revision == 0
        assert task.target_rack_id is None
        assert (
            await db.scalar(
                select(InboundEvidence.id).where(InboundEvidence.source_identity == f"{OP}:{first.operation_id}")
            )
            is None
        )


async def test_ten_character_faces_keep_exact_source_identity(integration_session_factory, prepared):

    task_name, ids = prepared
    face = "面" * 10
    racks = [{"rack_id": "LONG", "rack_face": face}]
    picks = [{"source_locator": {"type": "RACK_SLOT", "rack_id": "LONG", "rack_face": face, "slot_id": "S"}}]
    first = _event(task_name, added_bin_source_racks=racks, added_direct_picks=picks)
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"
    assert (
        await service.record(first.model_copy(update={"operation_id": new_uuid7()}), received_at=NOW)
    ).code == "DUPLICATE"
    assert (
        await service.record(_event(task_name, 2, added_bin_source_racks=racks), received_at=NOW)
    ).reason_code == "REFERENCE_CONFLICT"
    async with integration_session_factory() as db:
        member = await db.scalar(
            select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == ids[0])
        )
        assert member.rack_face == face


async def test_identity_drift_blocks_original_task_without_rewriting_applied_evidence(
    integration_session_factory, prepared
):
    task_name, ids = prepared
    first = _event(task_name)
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"
    drift = first.model_copy(update={"timestamp": 2})
    assert (await service.record(drift, received_at=NOW)).reason_code == "IDEMPOTENCY_CONFLICT"
    assert (await service.record(first, received_at=NOW)).code == "DUPLICATE"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.plan_blocked_evidence_id == task.last_plan_evidence_id
        assert (await db.get(InboundEvidence, task.last_plan_evidence_id)).apply_status == Status.APPLIED


async def _blocked_correction(factory, task_name, task_id):
    service = PickingTaskPlanDeltaService(factory)
    assert (await service.record(_event(task_name), received_at=NOW)).code == "RECEIVED"
    bad = _event(task_name, 3, added_bin_source_racks=[{"rack_id": "BAD", "rack_face": "A"}])
    await service.record(bad, received_at=NOW)
    correction = _event(task_name, 2, added_bin_source_racks=[{"rack_id": "GOOD", "rack_face": "A"}])
    await service.record(correction, received_at=NOW)
    async with factory() as db:
        task = await db.get(PickingTask, task_id)
        correction_id = await db.scalar(
            select(InboundEvidence.id).where(InboundEvidence.source_identity == f"{OP}:{correction.operation_id}")
        )
        return correction, {
            "task_id": task_name,
            "blocked_evidence_id": task.plan_blocked_evidence_id,
            "correction_evidence_id": correction_id,
            "expected_version": task.version,
            "actor_id": None,
            "reason": "Verified correction",
            "received_at": NOW,
        }


async def test_concurrent_same_correction_applies_once(integration_session_factory, prepared):
    task_name, ids = prepared
    correction, args = await _blocked_correction(integration_session_factory, task_name, ids[0])
    service = PickingTaskPlanDeltaService(integration_session_factory)
    results = await asyncio.gather(service.apply_correction(**args), service.apply_correction(**args))
    assert results[0] == results[1]
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    next_event = _event(task_name, 3, added_bin_source_racks=[{"rack_id": "NEXT", "rack_face": "A"}])
    assert (await service.record(next_event, received_at=NOW)).code == "RECEIVED"
    async with integration_session_factory.begin() as db:
        await db.execute(update(PickingTask).where(PickingTask.id == ids[0]).values(status="EXECUTION_COMPLETED"))
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    assert (
        await service.record(correction.model_copy(update={"timestamp": 2}), received_at=NOW)
    ).reason_code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("changed", ["version", "blocker", "revision", "terminal"])
async def test_correction_rejects_changed_preconditions(integration_session_factory, prepared, changed):
    task_name, ids = prepared
    _, args = await _blocked_correction(integration_session_factory, task_name, ids[0])
    if changed == "version":
        args["expected_version"] += 1
    elif changed == "blocker":
        args["blocked_evidence_id"] = ids[3]
    elif changed == "revision":
        args["correction_evidence_id"] = args["blocked_evidence_id"]
    else:
        async with integration_session_factory.begin() as db:
            await db.execute(update(PickingTask).where(PickingTask.id == ids[0]).values(status="EXECUTION_COMPLETED"))
    with pytest.raises(PlanCorrectionConflictError):
        await PickingTaskPlanDeltaService(integration_session_factory).apply_correction(**args)
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.last_applied_plan_revision == 1 and task.plan_blocked_evidence_id is not None


async def test_correction_audit_failure_rolls_back_plan_and_blocker(integration_session_factory, prepared):
    class FailingAudit:
        async def create_audit_log(self, *args, **kwargs):
            raise RuntimeError("audit failure")

    task_name, ids = prepared
    correction, args = await _blocked_correction(integration_session_factory, task_name, ids[0])
    with pytest.raises(RuntimeError, match="audit failure"):
        await PickingTaskPlanDeltaService(integration_session_factory, audit_service=FailingAudit()).apply_correction(
            **args
        )
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(correction, received_at=NOW)).reason_code == "STATE_CONFLICT"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.last_applied_plan_revision == 1
        assert task.plan_blocked_evidence_id == args["blocked_evidence_id"]
        assert not list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                )
            ).all()
        )


async def test_invalid_data_is_durable_and_never_blocks_task(integration_session_factory, prepared):
    from src.app.wms_adapter.outbound_picking.plan_delta_wire import parse_picking_task_plan_delta_receipt

    task_name, ids = prepared
    payload = _event(task_name).model_dump(mode="json", exclude_none=True)
    payload["data"]["target_rack"] = None
    invalid = parse_picking_task_plan_delta_receipt(payload)
    service = PickingTaskPlanDeltaService(integration_session_factory)
    first = await service.record(invalid, received_at=NOW)
    assert first.reason_code == "INVALID_DATA"
    assert await service.record(invalid, received_at=NOW + timedelta(seconds=1)) == first
    payload["data"]["target_rack"] = {"rack_id": "R", "rack_face": "A"}
    assert (
        await service.record(parse_picking_task_plan_delta_receipt(payload), received_at=NOW)
    ).reason_code == "IDEMPOTENCY_CONFLICT"
    async with integration_session_factory() as db:
        assert (await db.get(PickingTask, ids[0])).plan_blocked_evidence_id is None


async def test_commit_failure_never_acknowledges_success(integration_session_factory, prepared):
    from sqlalchemy import event as sqlalchemy_event
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy.orm import Session

    class RejectCommit(Session):
        pass

    @sqlalchemy_event.listens_for(RejectCommit, "before_commit")
    def reject(session):
        session.flush()
        raise RuntimeError("commit rejected")

    task_name, ids = prepared
    first = _event(task_name)
    factory = async_sessionmaker(integration_session_factory.kw["bind"], sync_session_class=RejectCommit)
    with pytest.raises(RuntimeError, match="commit rejected"):
        await PickingTaskPlanDeltaService(factory).record(first, received_at=NOW)
    async with integration_session_factory() as db:
        assert (await db.get(PickingTask, ids[0])).last_applied_plan_revision == 0
        assert (
            await db.scalar(
                select(InboundEvidence.id).where(InboundEvidence.source_identity == f"{OP}:{first.operation_id}")
            )
            is None
        )


async def test_prepare_confirmation_lock_is_not_reacquired_after_task_lock(integration_session_factory, prepared):
    task_name, ids = prepared
    async with integration_session_factory.begin() as owner_transaction:
        await owner_transaction.scalar(select(WmsConfirmation).where(WmsConfirmation.id == ids[4]).with_for_update())
        result = await asyncio.wait_for(
            PickingTaskPlanDeltaService(integration_session_factory).record(_event(task_name), received_at=NOW),
            timeout=2,
        )
        assert result.code == "RECEIVED"


@pytest.mark.parametrize("violation", ["unique", "revision", "evidence_fk"])
async def test_plan_member_constraints_are_enforced_by_postgresql(integration_session_factory, prepared, violation):
    from sqlalchemy.exc import IntegrityError

    task_name, ids = prepared
    first = _event(task_name, added_bin_source_racks=[{"rack_id": "SOURCE", "rack_face": "A"}])
    await PickingTaskPlanDeltaService(integration_session_factory).record(first, received_at=NOW)
    with pytest.raises(IntegrityError):
        async with integration_session_factory.begin() as db:
            task = await db.get(PickingTask, ids[0])
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=task.id,
                    rack_id="SOURCE" if violation == "unique" else "OTHER",
                    rack_face="A",
                    plan_revision=0 if violation == "revision" else 1,
                    source_evidence_id=-1 if violation == "evidence_fk" else task.last_plan_evidence_id,
                )
            )
            await db.flush()


@pytest.mark.parametrize("change", ["terminal", "new_blocker", "unrelated_version"])
async def test_management_replay_rejects_drift_but_wms_replay_stays_successful(
    integration_session_factory, prepared, change
):
    task_name, ids = prepared
    correction, args = await _blocked_correction(integration_session_factory, task_name, ids[0])
    service = PickingTaskPlanDeltaService(integration_session_factory)
    result = await service.apply_correction(**args)
    assert result.version == args["expected_version"] + 1
    assert await service.apply_correction(**args) == result
    if change == "new_blocker":
        await service.record(
            _event(task_name, 4, added_bin_source_racks=[{"rack_id": "NEW", "rack_face": "A"}]), received_at=NOW
        )
    else:
        async with integration_session_factory.begin() as db:
            task = await db.get(PickingTask, ids[0])
            if change == "terminal":
                task.status = "EXECUTION_COMPLETED"
            else:
                task.issued_at_ms += 1
                task.increment_version()
    with pytest.raises(PlanCorrectionConflictError):
        await service.apply_correction(**args)
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"


async def test_source_queries_return_only_candidates_with_large_history(
    integration_session_factory, prepared, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        MEMBER_BATCH_SIZE,
        PickingTaskPlanDeltaRepository,
    )

    task_name, ids = prepared
    history = [{"rack_id": f"H{i}", "rack_face": "A"} for i in range(MEMBER_BATCH_SIZE * 3 + 1)]
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(_event(task_name, added_bin_source_racks=history), received_at=NOW)).code == "RECEIVED"
    async with integration_session_factory() as db:
        execute = AsyncMock(wraps=db.execute)
        monkeypatch.setattr(db, "execute", execute)
        repository = PickingTaskPlanDeltaRepository()
        picks, racks = await repository.source_identities(db, ids[0], direct_picks=[], bin_racks=[("H0", "A")])
        assert picks == set() and racks == {("H0", "A")}
        assert execute.await_count == 1
        execute.reset_mock()
        candidates = [(f"M{i}", "A") for i in range(MEMBER_BATCH_SIZE)] + [("H0", "A")]
        assert await repository.source_identities(db, ids[0], direct_picks=[], bin_racks=candidates) == (
            set(),
            {("H0", "A")},
        )
        assert execute.await_count == 2
