"""plan_delta 原子提交、任务级串行化与自动修正的 PostgreSQL owner。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, insert, select, update
from wes_plugin_sdk import PickingTaskPlanAdmissionDecision, PickingTaskPlanAdmissionDecisionKind, wms_operations

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.services import InboundEvidenceService
from src.app.sys.models.audit_log import AuditLog
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelEvent
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaEvent
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import (
    DirectPickExecution,
    DirectPickFaceCompletion,
    PickingTask,
    PickingTaskBinSourceRack,
)
from src.app.wms_integration.outbound_picking.services.picking_task_cancel import PickingTaskCancelService
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

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
        await db.execute(delete(DirectPickFaceCompletion).where(DirectPickFaceCompletion.picking_task_id == task_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id == confirmation_id))
        # picking_task_id 与 issued_evidence_id 互为外键，先断开再各自删除。
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.picking_task_id == task_id).values(picking_task_id=None)
        )
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


async def test_revisions_reuse_source_members_and_cancel_only_selected_revision(integration_session_factory, prepared):
    task_name, ids = prepared
    plans = PickingTaskPlanDeltaService(integration_session_factory)
    direct = [{"source_locator": {"type": "RACK_SLOT", "rack_id": "DIRECT-A", "rack_face": "A", "slot_id": "S1"}}]
    first = _event(
        task_name, 1, added_bin_source_racks=[{"rack_id": "A", "rack_face": ["90"]}], added_direct_picks=direct
    )
    second = _event(
        task_name, 2, added_bin_source_racks=[{"rack_id": "A", "rack_face": ["90"]}], added_direct_picks=direct
    )
    third = _event(
        task_name,
        3,
        added_bin_source_racks=[
            {"rack_id": "B", "rack_face": ["90"]},
            {"rack_id": "C", "rack_face": ["90"]},
        ],
    )
    assert (await plans.record(first, received_at=NOW)).code == "RECEIVED"
    assert (await plans.record(second, received_at=NOW)).code == "RECEIVED"
    assert (await plans.record(second, received_at=NOW)).code == "DUPLICATE"
    assert (await plans.record(third, received_at=NOW)).code == "RECEIVED"

    cancel = PickingTaskCancelEvent.model_validate(
        {
            "operation": "outbound.picking_task.cancel@v1",
            "operation_id": new_uuid7(),
            "timestamp": 1,
            "data": {
                "task_id": task_name,
                "cancel_scope": "PLAN_MEMBERS",
                "bin_source_racks": [{"plan_revision": 2, "rack_id": "A", "rack_face": ["90"]}],
                "direct_pick_sources": [
                    {"plan_revision": 2, "rack_id": "DIRECT-A", "rack_face": "A", "slot_ids": ["S1"]}
                ],
            },
        }
    )
    assert (
        await PickingTaskCancelService(integration_session_factory).record(cancel, received_at=NOW)
    ).code == "RECEIVED"
    async with integration_session_factory() as db:
        racks = list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack)
                    .where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                    .order_by(PickingTaskBinSourceRack.plan_revision, PickingTaskBinSourceRack.rack_id)
                )
            ).all()
        )
        picks = list(
            (
                await db.scalars(
                    select(DirectPickExecution)
                    .where(DirectPickExecution.picking_task_id == ids[0])
                    .order_by(DirectPickExecution.plan_revision)
                )
            ).all()
        )
        assert [(row.plan_revision, row.rack_id, row.cancelled_evidence_id is not None) for row in racks] == [
            (1, "A", False),
            (2, "A", True),
            (3, "B", False),
            (3, "C", False),
        ]
        assert [(row.plan_revision, row.cancelled_evidence_id is not None) for row in picks] == [
            (1, False),
            (2, True),
        ]


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


async def test_normal_correction_clears_blocker_and_preserves_original_rejection(integration_session_factory, prepared):
    task_name, ids = prepared
    service = PickingTaskPlanDeltaService(integration_session_factory)
    first = _event(task_name)
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"
    conflict = _event(task_name, 4, added_bin_source_racks=[{"rack_id": "B", "rack_face": ["F"]}])
    assert (await service.record(conflict, received_at=NOW)).reason_code == "REVISION_CONFLICT"
    correction = _event(task_name, 2, added_bin_source_racks=[{"rack_id": "B", "rack_face": ["F"]}])
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        blocked_id, version = task.plan_blocked_evidence_id, task.version
        blocker = await db.get(InboundEvidence, blocked_id)
        old_payload, processed_at = blocker.normalized_payload, blocker.processed_at
    assert (await service.record(correction, received_at=NOW)).code == "RECEIVED"
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    assert (await service.record(conflict, received_at=NOW)).reason_code == "REVISION_CONFLICT"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.plan_blocked_evidence_id is None
        assert task.last_applied_plan_revision == 2
        assert task.version == version + 1
        blocker = await db.get(InboundEvidence, blocked_id)
        assert blocker.normalized_payload == old_payload
        assert blocker.processed_at == processed_at
        assert blocker.apply_status == Status.RECONCILING
        assert (
            len(
                list(
                    (
                        await db.scalars(
                            select(InboundEvidenceConflict).where(
                                InboundEvidenceConflict.first_evidence_id == blocked_id
                            )
                        )
                    ).all()
                )
            )
            == 1
        )
        assert await db.scalar(select(AuditLog.id).where(AuditLog.args["task_id"].as_string() == task_name)) is None


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


async def test_custom_admission_reason_replays_and_drift_stays_idempotency_conflict(
    integration_session_factory, prepared
):
    task_name, ids = prepared
    async with integration_session_factory.begin() as db:
        await db.execute(
            update(WorkLine).where(WorkLine.id == ids[1]).values(plugin_key="sample_plugin", plugin_version="0.1.0")
        )
    policy = Mock(
        return_value=PickingTaskPlanAdmissionDecision(
            kind=PickingTaskPlanAdmissionDecisionKind.REJECT,
            reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
        )
    )
    service = PickingTaskPlanDeltaService(
        integration_session_factory,
        plan_admission_policies={("sample_plugin", "0.1.0"): policy},
    )
    first = _event(
        task_name,
        added_direct_picks=[
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "SOURCE", "rack_face": "A", "slot_id": "1"}}
        ],
    )

    assert (await service.record(first, received_at=NOW)).reason_code == "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task is not None
        assert (
            await db.scalar(select(DirectPickExecution.id).where(DirectPickExecution.picking_task_id == task.id))
            is None
        )
    assert (await service.record(first, received_at=NOW + timedelta(seconds=1))).reason_code == (
        "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"
    )
    drift = first.model_copy(
        update={
            "data": first.data.model_copy(
                update={
                    "added_direct_picks": [
                        {
                            "source_locator": {
                                "type": "RACK_SLOT",
                                "rack_id": "OTHER",
                                "rack_face": "A",
                                "slot_id": "1",
                            }
                        }
                    ]
                }
            ),
            "timestamp": 2,
        }
    )
    assert (await service.record(drift, received_at=NOW + timedelta(seconds=2))).reason_code == ("IDEMPOTENCY_CONFLICT")
    assert policy.call_count == 1


async def test_payload_drift_before_admission_rejection_does_not_hide_plugin_reason(
    integration_session_factory, prepared
):
    task_name, ids = prepared
    async with integration_session_factory.begin() as db:
        await db.execute(
            update(WorkLine).where(WorkLine.id == ids[1]).values(plugin_key="sample_plugin", plugin_version="0.1.0")
        )
        await db.execute(
            update(WmsConfirmation).where(WmsConfirmation.id == ids[4]).values(status=WmsConfirmationStatus.DISPATCHING)
        )
    policy = Mock(
        return_value=PickingTaskPlanAdmissionDecision(
            kind=PickingTaskPlanAdmissionDecisionKind.REJECT,
            reason_code="MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED",
        )
    )
    service = PickingTaskPlanDeltaService(
        integration_session_factory,
        plan_admission_policies={("sample_plugin", "0.1.0"): policy},
    )
    first = _event(
        task_name,
        added_direct_picks=[
            {"source_locator": {"type": "RACK_SLOT", "rack_id": "SOURCE", "rack_face": "A", "slot_id": "1"}}
        ],
    )
    drift = first.model_copy(update={"timestamp": 2})

    assert (await service.record(first, received_at=NOW)).code == "UNAVAILABLE"
    assert (await service.record(drift, received_at=NOW + timedelta(seconds=1))).reason_code == ("IDEMPOTENCY_CONFLICT")
    async with integration_session_factory.begin() as db:
        await db.execute(
            update(WmsConfirmation).where(WmsConfirmation.id == ids[4]).values(status=WmsConfirmationStatus.COMPLETED)
        )
    assert (await service.record(first, received_at=NOW + timedelta(seconds=2))).reason_code == (
        "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"
    )
    assert (await service.record(first, received_at=NOW + timedelta(seconds=3))).reason_code == (
        "MANUAL_PICKING_DIRECT_PICK_UNSUPPORTED"
    )
    assert policy.call_count == 1


async def test_admission_policy_exception_rolls_back_evidence_task_and_members(integration_session_factory, prepared):
    task_name, ids = prepared
    async with integration_session_factory.begin() as db:
        await db.execute(
            update(WorkLine).where(WorkLine.id == ids[1]).values(plugin_key="sample_plugin", plugin_version="0.1.0")
        )
    policy = Mock(side_effect=RuntimeError("policy failure"))
    service = PickingTaskPlanDeltaService(
        integration_session_factory,
        plan_admission_policies={("sample_plugin", "0.1.0"): policy},
    )
    first = _event(task_name)

    with pytest.raises(RuntimeError, match="policy failure"):
        await service.record(first, received_at=NOW)

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
        assert (
            await db.scalar(select(DirectPickExecution.id).where(DirectPickExecution.picking_task_id == task.id))
            is None
        )


async def test_ten_character_faces_keep_exact_source_identity(integration_session_factory, prepared):

    task_name, ids = prepared
    face = "面" * 10
    racks = [{"rack_id": "LONG", "rack_face": [face]}]
    picks = [{"source_locator": {"type": "RACK_SLOT", "rack_id": "LONG", "rack_face": face, "slot_id": "S"}}]
    first = _event(task_name, added_bin_source_racks=racks, added_direct_picks=picks)
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(first, received_at=NOW)).code == "RECEIVED"
    assert (
        await service.record(first.model_copy(update={"operation_id": new_uuid7()}), received_at=NOW)
    ).code == "DUPLICATE"
    assert (
        await service.record(_event(task_name, 2, added_bin_source_racks=racks), received_at=NOW)
    ).code == "RECEIVED"
    async with integration_session_factory() as db:
        members = list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack)
                    .where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                    .order_by(PickingTaskBinSourceRack.plan_revision)
                )
            ).all()
        )
        assert [(member.plan_revision, member.rack_face) for member in members] == [(1, face), (2, face)]


async def test_multi_face_bin_source_rack_persists_one_member_per_face(integration_session_factory, prepared):
    task_name, ids = prepared
    event = _event(task_name, added_bin_source_racks=[{"rack_id": "SOURCE", "rack_face": ["90", "270"]}])

    assert (
        await PickingTaskPlanDeltaService(integration_session_factory).record(event, received_at=NOW)
    ).code == "RECEIVED"

    async with integration_session_factory() as db:
        members = list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack)
                    .where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                    .order_by(PickingTaskBinSourceRack.rack_face)
                )
            ).all()
        )
        assert [(member.rack_id, member.rack_face) for member in members] == [
            ("SOURCE", "270"),
            ("SOURCE", "90"),
        ]


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


async def _blocked_correction(factory, task_name, task_id, *, persisted=True):
    service = PickingTaskPlanDeltaService(factory)
    assert (await service.record(_event(task_name), received_at=NOW)).code == "RECEIVED"
    bad = _event(task_name, 3, added_bin_source_racks=[{"rack_id": "BAD", "rack_face": ["A"]}])
    await service.record(bad, received_at=NOW)
    correction = _event(task_name, 2, added_bin_source_racks=[{"rack_id": "GOOD", "rack_face": ["A"]}])
    if persisted:
        # 已可靠保存为 RECONCILING 的修正；保留其首次拒绝事实供正常入口重报测试。
        async with factory.begin() as db:
            evidences = InboundEvidenceService()
            accepted = await evidences.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{OP}:{correction.operation_id}",
                normalized_payload=correction.model_dump(mode="json", exclude_none=True),
                operation=OP,
                operation_id=correction.operation_id,
                contract_key=OP,
                contract_version="1.0",
                apply_status=Status.RECONCILING,
                received_at=NOW,
            )
            await evidences.record_conflict(
                db,
                first=accepted.evidence,
                source_identity=accepted.evidence.source_identity,
                normalized_payload=accepted.evidence.normalized_payload,
                reason_code="STATE_CONFLICT",
                received_at=NOW,
            )
            accepted.evidence.processed_at = NOW
    async with factory() as db:
        task = await db.get(PickingTask, task_id)
        correction_id = await db.scalar(
            select(InboundEvidence.id).where(InboundEvidence.source_identity == f"{OP}:{correction.operation_id}")
        )
        return correction, {
            "blocked_evidence_id": task.plan_blocked_evidence_id,
            "correction_evidence_id": correction_id,
            "expected_version": task.version,
        }


@pytest.mark.parametrize("persisted", [False, True])
async def test_concurrent_same_correction_applies_once(integration_session_factory, prepared, persisted):
    task_name, ids = prepared
    correction, args = await _blocked_correction(integration_session_factory, task_name, ids[0], persisted=persisted)
    service = PickingTaskPlanDeltaService(integration_session_factory)
    results = await asyncio.wait_for(
        asyncio.gather(*(service.record(correction, received_at=NOW) for _ in range(2))), timeout=5
    )
    assert sorted(result.code for result in results) == ["DUPLICATE", "RECEIVED"]
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.plan_blocked_evidence_id is None
        assert task.version == args["expected_version"] + 1
        assert task.last_applied_plan_revision == 2
        members = list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                )
            ).all()
        )
        assert len(members) == 1
        assert members[0].source_evidence_id == task.last_plan_evidence_id
        applied = await db.get(InboundEvidence, task.last_plan_evidence_id)
        assert applied.apply_status == Status.APPLIED
        assert applied.normalized_payload == correction.model_dump(mode="json", exclude_none=True)
        if persisted:
            assert applied.id == args["correction_evidence_id"]
            assert await service._plans.first_rejection(db, applied.id) == "STATE_CONFLICT"
    next_event = _event(task_name, 3, added_bin_source_racks=[{"rack_id": "NEXT", "rack_face": ["A"]}])
    assert (await service.record(next_event, received_at=NOW)).code == "RECEIVED"
    async with integration_session_factory.begin() as db:
        await db.execute(update(PickingTask).where(PickingTask.id == ids[0]).values(status="EXECUTION_COMPLETED"))
    assert (await service.record(correction, received_at=NOW)).code == "DUPLICATE"
    assert (
        await service.record(correction.model_copy(update={"timestamp": 2}), received_at=NOW)
    ).reason_code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("persisted", [False, True])
async def test_normal_correction_member_failure_rolls_back_blocker_and_evidence(
    integration_session_factory, prepared, persisted
):
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        PickingTaskPlanDeltaRepository,
    )

    class FailingMembers(PickingTaskPlanDeltaRepository):
        async def add_members(self, db, task_id, data, evidence_id):
            await super().add_members(db, task_id, data, evidence_id)
            raise RuntimeError("correction member failure")

    task_name, ids = prepared
    correction, args = await _blocked_correction(integration_session_factory, task_name, ids[0], persisted=persisted)
    service = PickingTaskPlanDeltaService(integration_session_factory, plan_repository=FailingMembers())
    with pytest.raises(RuntimeError, match="correction member failure"):
        await service.record(correction, received_at=NOW + timedelta(seconds=1))
    async with integration_session_factory() as db:
        task = await db.get(PickingTask, ids[0])
        assert task.plan_blocked_evidence_id == args["blocked_evidence_id"]
        assert task.version == args["expected_version"]
        assert task.last_applied_plan_revision == 1
        assert not list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == ids[0])
                )
            ).all()
        )
        evidence = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == f"{OP}:{correction.operation_id}")
        )
        if persisted:
            assert evidence.id == args["correction_evidence_id"]
            assert evidence.apply_status == Status.RECONCILING
            assert evidence.processed_at == NOW
            assert evidence.normalized_payload == correction.model_dump(mode="json", exclude_none=True)
        else:
            assert evidence is None
    assert (
        await PickingTaskPlanDeltaService(integration_session_factory).record(correction, received_at=NOW)
    ).code == "RECEIVED"


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
    first = _event(task_name, added_bin_source_racks=[{"rack_id": "SOURCE", "rack_face": ["A", "B"]}])
    await PickingTaskPlanDeltaService(integration_session_factory).record(first, received_at=NOW)
    async with integration_session_factory() as db:
        members = await db.scalars(
            select(PickingTaskBinSourceRack.rack_face).where(PickingTaskBinSourceRack.picking_task_id == ids[0])
        )
        assert sorted(members.all()) == ["A", "B"]
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


@pytest.mark.parametrize("violation", ["unique", "empty_face"])
async def test_direct_pick_face_completion_constraints_are_enforced_by_postgresql(
    integration_session_factory, prepared, violation
):
    from sqlalchemy.exc import IntegrityError

    _task_name, ids = prepared
    async with integration_session_factory.begin() as db:
        db.add(
            DirectPickFaceCompletion(
                picking_task_id=ids[0],
                plan_revision=1,
                rack_id="SOURCE",
                rack_face="A",
                completed_at=NOW,
                source_evidence_id=ids[3],
            )
        )
    with pytest.raises(IntegrityError):
        # 核心 insert 绕过 Pydantic min_length 校验，直接命中数据库 CHECK/唯一约束。
        async with integration_session_factory.begin() as db:
            await db.execute(
                insert(DirectPickFaceCompletion).values(
                    picking_task_id=ids[0],
                    rack_id="SOURCE",
                    rack_face="" if violation == "empty_face" else "A",
                    completed_at=NOW,
                    source_evidence_id=ids[3],
                )
            )


async def test_source_member_ids_preserve_wire_rack_and_face_order(integration_session_factory, prepared):
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        PickingTaskPlanDeltaRepository,
    )

    task_name, ids = prepared
    event = _event(
        task_name,
        added_bin_source_racks=[
            {"rack_id": "Z-RACK", "rack_face": ["270", "90"]},
            {"rack_id": "A-RACK", "rack_face": ["90"]},
        ],
    )
    assert (
        await PickingTaskPlanDeltaService(integration_session_factory).record(event, received_at=NOW)
    ).code == "RECEIVED"
    async with integration_session_factory() as db:
        rows = await PickingTaskPlanDeltaRepository().list_bin_source_racks(db, ids[0])
    assert [(row.rack_id, row.rack_face) for row in rows] == [
        ("Z-RACK", "270"),
        ("Z-RACK", "90"),
        ("A-RACK", "90"),
    ]
    assert [row.id for row in rows] == sorted(row.id for row in rows)


async def test_completed_rack_owners_use_current_transport_and_plan_identity(integration_session_factory, prepared):
    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        PickingTaskPlanDeltaRepository,
    )

    task_name, ids = prepared
    event = _event(task_name, added_bin_source_racks=[{"rack_id": "SOURCE", "rack_face": ["90"]}])
    assert (
        await PickingTaskPlanDeltaService(integration_session_factory).record(event, received_at=NOW)
    ).code == "RECEIVED"
    client_request_id = new_uuid7()
    transport_task_id = new_uuid7()
    target_request_id = new_uuid7()
    target_transport_id = new_uuid7()
    async with integration_session_factory.begin() as db:
        task = await db.get(PickingTask, ids[0])
        task.status = "EXECUTION_COMPLETED"
        source = (await PickingTaskPlanDeltaRepository().list_bin_source_racks(db, ids[0]))[0]
        db.add(
            TransportTask(
                transport_task_id=transport_task_id,
                client_request_id=client_request_id,
                request_digest="a" * 64,
                kind="RACK_MOVE",
                caller_json={"workline_id": str(ids[1])},
                request_json={"rack_id": "SOURCE"},
                submit_operation_id=new_uuid7(),
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="b" * 64,
                status="SUCCEEDED",
                authority_workline_id=ids[1],
                created_at=timezone.now_for_db(),
                updated_at=timezone.now_for_db(),
            )
        )
        db.add(
            TransportTask(
                transport_task_id=target_transport_id,
                client_request_id=target_request_id,
                request_digest="c" * 64,
                kind="RACK_MOVE",
                caller_json={"workline_id": str(ids[1])},
                request_json={"rack_id": "TARGET"},
                submit_operation_id=new_uuid7(),
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="d" * 64,
                status="SUCCEEDED",
                authority_workline_id=ids[1],
                created_at=timezone.now_for_db(),
                updated_at=timezone.now_for_db(),
            )
        )
        db.add(
            TransportDecisionBinding(
                correlation_id=f"pt:{ids[0]}:e:{source.source_evidence_id}:rack:SOURCE",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                workline_id=ids[1],
                resource_fence_id="SOURCE",
                client_request_id=client_request_id,
                source_evidence_id=source.source_evidence_id,
            )
        )
        db.add(
            TransportDecisionBinding(
                correlation_id=f"pt:{ids[0]}:e:{task.initial_plan_evidence_id}:rack:TARGET",
                step="PICKING_TASK_TARGET_RACK_IN",
                workline_id=ids[1],
                resource_fence_id="TARGET",
                client_request_id=target_request_id,
                source_evidence_id=task.initial_plan_evidence_id,
            )
        )
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="SOURCE",
                workline_id=ids[1],
                position_json={"kind": "RACK_POSITION", "location_code": "FIVE-POS"},
                arrival_face="90",
                source_operation_id=new_uuid7(),
                source_transport_task_id=transport_task_id,
            )
        )
        db.add(
            PositionProjection(
                object_type="RACK",
                object_id="TARGET",
                workline_id=ids[1],
                position_json={"kind": "RACK_POSITION", "location_code": "TRANSFER-POS"},
                arrival_face="opaqueFace",
                source_operation_id=new_uuid7(),
                source_transport_task_id=target_transport_id,
            )
        )
    try:
        async with integration_session_factory() as db:
            repository = PickingTaskPlanDeltaRepository()
            assert await repository.source_transport_matches(
                db, ids[1], "SOURCE", source.source_evidence_id, transport_task_id
            )
            owner = await repository.first_completed_source_owner_at_position(db, ids[1], "FIVE-POS")
            assert owner is not None and owner.id == ids[0]
            assert await repository.first_completed_source_owner_at_position(db, ids[1], "OTHER-POS") is None
            target_owner = await repository.first_completed_transfer_owner_at_position(db, ids[1], "TRANSFER-POS")
            assert target_owner is not None and target_owner.id == ids[0]
            assert await repository.first_completed_transfer_owner_at_position(db, ids[1], "OTHER-POS") is None
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(PositionProjection).where(
                    PositionProjection.source_transport_task_id.in_((transport_task_id, target_transport_id))
                )
            )
            await db.execute(
                delete(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id.in_((client_request_id, target_request_id))
                )
            )
            await db.execute(
                delete(TransportTask).where(TransportTask.client_request_id.in_((client_request_id, target_request_id)))
            )


async def test_source_queries_return_only_candidates_with_large_history(
    integration_session_factory, prepared, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import (
        MEMBER_BATCH_SIZE,
        PickingTaskPlanDeltaRepository,
    )

    task_name, ids = prepared
    history = [{"rack_id": f"H{i}", "rack_face": ["A"]} for i in range(MEMBER_BATCH_SIZE * 3 + 1)]
    service = PickingTaskPlanDeltaService(integration_session_factory)
    assert (await service.record(_event(task_name, added_bin_source_racks=history), received_at=NOW)).code == "RECEIVED"
    async with integration_session_factory() as db:
        execute = AsyncMock(wraps=db.execute)
        monkeypatch.setattr(db, "execute", execute)
        repository = PickingTaskPlanDeltaRepository()
        picks, racks = await repository.source_identities(db, ids[0], 1, direct_picks=[], bin_racks=[("H0", "A")])
        assert picks == set() and racks == {("H0", "A")}
        assert execute.await_count == 1
        execute.reset_mock()
        candidates = [(f"M{i}", "A") for i in range(MEMBER_BATCH_SIZE)] + [("H0", "A")]
        assert await repository.source_identities(db, ids[0], 1, direct_picks=[], bin_racks=candidates) == (
            set(),
            {("H0", "A")},
        )
        assert execute.await_count == 2
