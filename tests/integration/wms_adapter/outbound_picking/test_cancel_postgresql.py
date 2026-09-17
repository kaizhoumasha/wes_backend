"""PickingTaskCancelRepository 的真实选择器匹配、行锁与全或无语义。"""

from __future__ import annotations

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
from src.app.execution.models import (
    InboundEvidenceApplyStatus as Status,
)
from src.app.execution.services import InboundEvidenceService
from src.app.wms_adapter.outbound_picking.cancel_wire import PickingTaskCancelMembersData
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PickingTaskPlanDeltaEvent
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.repositories.picking_task_cancel_repository import (
    PickingTaskCancelRepository,
)
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7

pytest_plugins = ("tests.integration.conftest",)
NOW = datetime(2026, 9, 17)


@pytest.fixture
async def executing_task_with_members(integration_session_factory):
    """EXECUTING 任务，已通过真实 plan_delta 产生五层来源货架面 + 退料直接取料成员。"""
    factory = integration_session_factory
    task_name = f"CANCEL-{new_uuid7()}"
    issued_operation_id = new_uuid7()
    async with factory.begin() as db:
        line = WorkLine(
            line_code=task_name,
            line_name="Cancel repository test",
            line_type=LineType.MANUAL,
            run_mode=WorkLineRunMode.AUTO,
            is_active=True,
        )
        db.add(line)
        await db.flush()
        issued = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.WMS_EVENT,
            source_identity=f"outbound.picking_task.issued@v1:{issued_operation_id}",
            operation="outbound.picking_task.issued@v1",
            operation_id=issued_operation_id,
            normalized_payload={"data": {"task_id": task_name}},
            received_at=NOW,
            apply_status=Status.APPLIED,
        )
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
        prepare_operation_id = new_uuid7()
        request = encode_request(
            wms_operations.outbound_picking_task_prepare(
                operation_id=prepare_operation_id, task_id=task_name, work_line_code=line.line_code
            ),
            timestamp=1,
        )
        response = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"{PICKING_TASK_PREPARE_OPERATION}:{prepare_operation_id}",
            operation=PICKING_TASK_PREPARE_OPERATION,
            operation_id=prepare_operation_id,
            normalized_payload={
                "operation_id": prepare_operation_id,
                "code": "PREPARE_ACCEPTED",
                "timestamp": 1,
                "data": {},
            },
            received_at=NOW,
            apply_status=Status.APPLIED,
        )
        db.add(
            WmsConfirmation(
                operation=PICKING_TASK_PREPARE_OPERATION,
                operation_id=prepare_operation_id,
                picking_task_id=task.id,
                request_digest="a" * 64,
                request_payload=request,
                deadline_at=NOW + timedelta(seconds=30),
                status=WmsConfirmationStatus.COMPLETED,
                response_evidence_id=response.evidence.id,
                response_result="PREPARE_ACCEPTED",
                completed_at=NOW,
            )
        )
        await db.flush()
        issued_evidence_id, response_evidence_id = issued.evidence.id, response.evidence.id

    plan_service = PickingTaskPlanDeltaService(factory)
    plan_result = await plan_service.record(
        PickingTaskPlanDeltaEvent.model_validate(
            {
                "operation": "outbound.picking_task.plan_delta@v1",
                "operation_id": new_uuid7(),
                "timestamp": 1,
                "data": {
                    "task_id": task_name,
                    "plan_revision": 1,
                    "target_rack": {"rack_id": "TARGET", "rack_face": "opaqueFace"},
                    "added_bin_source_racks": [{"rack_id": "RACK-5F-001", "rack_faces": ["90", "270"]}],
                    "added_direct_picks": [
                        {
                            "source_locator": {
                                "type": "RACK_SLOT",
                                "rack_id": "RETURN-RACK-01",
                                "rack_face": "A",
                                "slot_id": "A-03",
                            }
                        }
                    ],
                },
            }
        ),
        received_at=NOW,
    )
    assert plan_result.code == "RECEIVED"

    async with factory() as db:
        task_id = await db.scalar(select(PickingTask.id).where(PickingTask.task_id == task_name))

    yield task_id, task_name

    async with factory.begin() as db:
        evidence_ids = [
            *(
                await db.scalars(
                    select(InboundEvidence.id).where(
                        InboundEvidence.normalized_payload["data"]["task_id"].as_string() == task_name
                    )
                )
            ).all(),
            issued_evidence_id,
            response_evidence_id,
        ]
        await db.execute(delete(DirectPickExecution).where(DirectPickExecution.picking_task_id == task_id))
        await db.execute(delete(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.picking_task_id == task_id))
        # picking_task_id 与 issued_evidence_id 互为外键，先断开再各自删除。
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.picking_task_id == task_id).values(picking_task_id=None)
        )
        await db.execute(delete(PickingTask).where(PickingTask.id == task_id))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.line_code == task_name))


async def _cancel_evidence_id(db, *, task_name: str, operation_id: str) -> int:
    acceptance = await InboundEvidenceService().accept(
        db,
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.cancel@v1:{operation_id}",
        operation="outbound.picking_task.cancel@v1",
        operation_id=operation_id,
        normalized_payload={"data": {"task_id": task_name, "cancel_scope": "PLAN_MEMBERS"}},
        received_at=NOW,
        apply_status=Status.APPLIED,
    )
    await db.flush()
    return acceptance.evidence.id


async def test_cancel_members_matches_selectors_and_marks_rows(
    integration_session_factory, executing_task_with_members
) -> None:
    task_id, task_name = executing_task_with_members
    repository = PickingTaskCancelRepository()
    data = PickingTaskCancelMembersData.model_validate(
        {
            "task_id": task_name,
            "cancel_scope": "PLAN_MEMBERS",
            "bin_source_racks": [{"rack_id": "RACK-5F-001", "rack_faces": ["90"]}],
            "direct_pick_sources": [{"rack_id": "RETURN-RACK-01", "rack_face": "A", "slot_ids": ["A-03"]}],
        }
    )

    async with integration_session_factory.begin() as db:
        evidence_id = await _cancel_evidence_id(db, task_name=task_name, operation_id=new_uuid7())
        matched, transport_task_ids = await repository.cancel_members(
            db, task_id=task_id, data=data, evidence_id=evidence_id
        )

    assert matched is True
    assert transport_task_ids == ()
    async with integration_session_factory() as db:
        rack_row = await db.scalar(
            select(PickingTaskBinSourceRack).where(
                PickingTaskBinSourceRack.picking_task_id == task_id,
                PickingTaskBinSourceRack.rack_id == "RACK-5F-001",
                PickingTaskBinSourceRack.rack_face == "90",
            )
        )
        untouched_face = await db.scalar(
            select(PickingTaskBinSourceRack).where(
                PickingTaskBinSourceRack.picking_task_id == task_id,
                PickingTaskBinSourceRack.rack_id == "RACK-5F-001",
                PickingTaskBinSourceRack.rack_face == "270",
            )
        )
        direct_row = await db.scalar(
            select(DirectPickExecution).where(
                DirectPickExecution.picking_task_id == task_id,
                DirectPickExecution.rack_id == "RETURN-RACK-01",
                DirectPickExecution.slot_id == "A-03",
            )
        )
    assert rack_row.cancelled_evidence_id == evidence_id
    assert untouched_face.cancelled_evidence_id is None
    assert direct_row.cancelled_evidence_id == evidence_id


async def test_cancel_members_is_all_or_nothing_when_selector_partially_matches(
    integration_session_factory, executing_task_with_members
) -> None:
    task_id, task_name = executing_task_with_members
    repository = PickingTaskCancelRepository()
    data = PickingTaskCancelMembersData.model_validate(
        {
            "task_id": task_name,
            "cancel_scope": "PLAN_MEMBERS",
            # 90 面真实存在；999 面不存在——整条请求必须原子拒绝，不部分写入。
            "bin_source_racks": [{"rack_id": "RACK-5F-001", "rack_faces": ["90", "999"]}],
        }
    )

    async with integration_session_factory.begin() as db:
        evidence_id = await _cancel_evidence_id(db, task_name=task_name, operation_id=new_uuid7())
        matched, transport_task_ids = await repository.cancel_members(
            db, task_id=task_id, data=data, evidence_id=evidence_id
        )

    assert matched is False
    assert transport_task_ids == ()
    async with integration_session_factory() as db:
        rack_row = await db.scalar(
            select(PickingTaskBinSourceRack).where(
                PickingTaskBinSourceRack.picking_task_id == task_id,
                PickingTaskBinSourceRack.rack_id == "RACK-5F-001",
                PickingTaskBinSourceRack.rack_face == "90",
            )
        )
    assert rack_row.cancelled_evidence_id is None


async def test_cancel_members_rejects_reselecting_an_already_cancelled_member(
    integration_session_factory, executing_task_with_members
) -> None:
    task_id, task_name = executing_task_with_members
    repository = PickingTaskCancelRepository()
    data = PickingTaskCancelMembersData.model_validate(
        {
            "task_id": task_name,
            "cancel_scope": "PLAN_MEMBERS",
            "bin_source_racks": [{"rack_id": "RACK-5F-001", "rack_faces": ["90"]}],
        }
    )

    async with integration_session_factory.begin() as db:
        first_evidence_id = await _cancel_evidence_id(db, task_name=task_name, operation_id=new_uuid7())
        first_matched, _ = await repository.cancel_members(
            db, task_id=task_id, data=data, evidence_id=first_evidence_id
        )
    assert first_matched is True

    async with integration_session_factory.begin() as db:
        second_evidence_id = await _cancel_evidence_id(db, task_name=task_name, operation_id=new_uuid7())
        second_matched, _ = await repository.cancel_members(
            db, task_id=task_id, data=data, evidence_id=second_evidence_id
        )

    assert second_matched is False
    async with integration_session_factory() as db:
        rack_row = await db.scalar(
            select(PickingTaskBinSourceRack).where(
                PickingTaskBinSourceRack.picking_task_id == task_id,
                PickingTaskBinSourceRack.rack_id == "RACK-5F-001",
                PickingTaskBinSourceRack.rack_face == "90",
            )
        )
    # 仍归属第一次取消；第二次请求不得覆盖已有的取消归属。
    assert rack_row.cancelled_evidence_id == first_evidence_id


async def test_first_rejection_returns_state_and_reference_conflict_reasons(
    integration_session_factory, executing_task_with_members
) -> None:
    _task_id, task_name = executing_task_with_members
    repository = PickingTaskCancelRepository()
    operation_id = new_uuid7()
    async with integration_session_factory.begin() as db:
        evidence = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.WMS_EVENT,
            source_identity=f"outbound.picking_task.cancel@v1:{operation_id}",
            operation="outbound.picking_task.cancel@v1",
            operation_id=operation_id,
            normalized_payload={"data": {"task_id": task_name, "cancel_scope": "TASK"}},
            received_at=NOW,
            apply_status=Status.APPLIED,
        )
        await db.flush()
        assert evidence.evidence.id is not None
        no_rejection = await repository.first_rejection(db, evidence.evidence.id)
        db.add(
            InboundEvidenceConflict(
                source_identity=f"outbound.picking_task.cancel@v1:{new_uuid7()}",
                first_evidence_id=evidence.evidence.id,
                conflicting_digest="b" * 64,
                normalized_payload={},
                reason_code="STATE_CONFLICT",
                received_at=NOW,
            )
        )
        await db.flush()
        rejection = await repository.first_rejection(db, evidence.evidence.id)

    assert no_rejection is None
    assert rejection == "STATE_CONFLICT"

    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id == evidence.evidence.id)
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence.evidence.id))
