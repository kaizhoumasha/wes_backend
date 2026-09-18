from __future__ import annotations

import pytest
from sqlalchemy import select

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus
from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_wire import parse_manual_rack_direct_pick_event
from src.app.wms_integration.outbound_picking.models import (
    DirectPickExecution,
    DirectPickFaceCompletion,
    PickingTask,
    PickingTaskStatus,
    PickingTaskType,
)
from src.app.wms_integration.outbound_picking.services.manual_rack_direct_pick_completed import (
    ManualRackDirectPickCompletedService,
)
from src.app.workline.models import LineType, WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database

pytestmark = pytest.mark.asyncio

OPERATION = "outbound.manual_rack.direct_pick_completed@v1"


async def _seed_task(db, *, task_id: str) -> tuple[PickingTask, InboundEvidence]:
    workline = WorkLine(
        line_code=f"MANUAL-DP-{task_id}",
        line_name="Manual direct pick",
        line_type=LineType.MANUAL,
        is_active=True,
        plugin_key="manual-picking",
        plugin_version="0.1.0",
    )
    db.add(workline)
    issued_operation_id = new_uuid7()
    issued = InboundEvidence(
        kind="WMS_EVENT",
        source_identity=f"outbound.picking_task.issued@v1:{issued_operation_id}",
        payload_digest="a" * 64,
        normalized_payload={},
        received_at=timezone.now_for_db(),
        operation="outbound.picking_task.issued@v1",
        operation_id=issued_operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    db.add(issued)
    await db.flush()
    task = PickingTask(
        task_id=task_id,
        task_type=PickingTaskType.MANUAL,
        status=PickingTaskStatus.EXECUTING,
        queue_revision=1,
        dispatch_sequence=1,
        issued_at_ms=1,
        issued_evidence_id=issued.id,
        workline_id=workline.id,
    )
    db.add(task)
    await db.flush()
    return task, issued


def _event(*, operation_id: str, task_id: str, rack_id: str, rack_face: str):
    return parse_manual_rack_direct_pick_event(
        {
            "operation_id": operation_id,
            "operation": OPERATION,
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": task_id,
                "rack_id": rack_id,
                "rack_face": rack_face,
                "completed_at": 1_788_389_999_000,
            },
        }
    )


async def test_direct_pick_completion_applied_for_matching_execution() -> None:
    task_id = "PICK-DP-001"
    rack_id = "RETURN-RACK-01"
    rack_face = "A"
    async with migrated_database() as (_url, sessions):
        async with sessions.begin() as db:
            task, issued = await _seed_task(db, task_id=task_id)
            db.add(
                DirectPickExecution(
                    picking_task_id=task.id,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    slot_id="SLOT-01",
                    plan_revision=1,
                    source_evidence_id=issued.id,
                )
            )

        service = ManualRackDirectPickCompletedService(sessions)
        event = _event(operation_id=new_uuid7(), task_id=task_id, rack_id=rack_id, rack_face=rack_face)
        result = await service.record(event, received_at=timezone.now_for_db())

        async with sessions() as db:
            evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id)
            )
            completion = await db.scalar(
                select(DirectPickFaceCompletion).where(
                    DirectPickFaceCompletion.picking_task_id == task.id,
                    DirectPickFaceCompletion.rack_id == rack_id,
                    DirectPickFaceCompletion.rack_face == rack_face,
                )
            )

        assert result.code == "RECEIVED"
        assert evidence is not None
        assert evidence.apply_status == InboundEvidenceApplyStatus.APPLIED
        assert evidence.picking_task_id == task.id
        assert completion is not None
        assert completion.source_evidence_id == evidence.id


async def test_direct_pick_completion_reconciles_without_matching_execution() -> None:
    task_id = "PICK-DP-002"
    rack_id = "RETURN-RACK-02"
    rack_face = "B"
    async with migrated_database() as (_url, sessions):
        async with sessions.begin() as db:
            task, _issued = await _seed_task(db, task_id=task_id)
            # 未插入匹配的 DirectPickExecution，模拟绑定不匹配。

        service = ManualRackDirectPickCompletedService(sessions)
        event = _event(operation_id=new_uuid7(), task_id=task_id, rack_id=rack_id, rack_face=rack_face)
        result = await service.record(event, received_at=timezone.now_for_db())

        async with sessions() as db:
            evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id)
            )
            completion = await db.scalar(
                select(DirectPickFaceCompletion).where(DirectPickFaceCompletion.picking_task_id == task.id)
            )

        assert result.code == "RECEIVED"
        assert evidence is not None
        assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
        assert completion is None


async def test_direct_pick_completion_replay_is_idempotent() -> None:
    task_id = "PICK-DP-003"
    rack_id = "RETURN-RACK-03"
    rack_face = "C"
    async with migrated_database() as (_url, sessions):
        async with sessions.begin() as db:
            task, issued = await _seed_task(db, task_id=task_id)
            db.add(
                DirectPickExecution(
                    picking_task_id=task.id,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    slot_id="SLOT-01",
                    plan_revision=1,
                    source_evidence_id=issued.id,
                )
            )

        service = ManualRackDirectPickCompletedService(sessions)
        operation_id = new_uuid7()
        event = _event(operation_id=operation_id, task_id=task_id, rack_id=rack_id, rack_face=rack_face)
        first = await service.record(event, received_at=timezone.now_for_db())
        second = await service.record(event, received_at=timezone.now_for_db())

        async with sessions() as db:
            completions = (
                await db.scalars(
                    select(DirectPickFaceCompletion).where(
                        DirectPickFaceCompletion.picking_task_id == task.id,
                        DirectPickFaceCompletion.rack_id == rack_id,
                        DirectPickFaceCompletion.rack_face == rack_face,
                    )
                )
            ).all()

        assert first.code == "RECEIVED"
        assert second.code == "DUPLICATE"
        assert len(completions) == 1


async def test_different_operation_id_for_a_completed_face_never_violates_the_unique_index() -> None:
    """不同 operation_id 重报同一已完成面走不到 InboundEvidence 去重，
    只能靠 already_completed 前置检查挡住唯一索引冲突。"""
    task_id = "PICK-DP-004"
    rack_id = "RETURN-RACK-04"
    rack_face = "D"
    async with migrated_database() as (_url, sessions):
        async with sessions.begin() as db:
            task, issued = await _seed_task(db, task_id=task_id)
            db.add(
                DirectPickExecution(
                    picking_task_id=task.id,
                    rack_id=rack_id,
                    rack_face=rack_face,
                    slot_id="SLOT-01",
                    plan_revision=1,
                    source_evidence_id=issued.id,
                )
            )

        service = ManualRackDirectPickCompletedService(sessions)
        first = await service.record(
            _event(operation_id=new_uuid7(), task_id=task_id, rack_id=rack_id, rack_face=rack_face),
            received_at=timezone.now_for_db(),
        )
        second = await service.record(
            _event(operation_id=new_uuid7(), task_id=task_id, rack_id=rack_id, rack_face=rack_face),
            received_at=timezone.now_for_db(),
        )

        async with sessions() as db:
            completions = (
                await db.scalars(
                    select(DirectPickFaceCompletion).where(
                        DirectPickFaceCompletion.picking_task_id == task.id,
                        DirectPickFaceCompletion.rack_id == rack_id,
                        DirectPickFaceCompletion.rack_face == rack_face,
                    )
                )
            ).all()

        assert first.code == "RECEIVED"
        assert second.code == "RECEIVED"
        assert len(completions) == 1
