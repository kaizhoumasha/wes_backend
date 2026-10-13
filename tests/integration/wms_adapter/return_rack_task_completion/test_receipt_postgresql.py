from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select

from deployment.plugin_composition import build_deployment_runtime
from src.app.execution.models import InboundEvidence
from src.app.execution.models.inbound_evidence import InboundEvidenceConflict
from src.app.wms_adapter.return_rack_task_completion.wire import (
    parse_return_rack_task_completed_event,
    parse_return_rack_task_completed_receipt,
)
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.app.wms_integration.outbound_picking.models.plan_members import DirectPickExecution, DirectPickFaceCompletion
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.wms_integration.return_rack_task_completion import ReturnRackTaskCompletedService
from src.app.wms_integration.return_rack_task_completion.reader import ReturnRackTaskCompletedReader
from src.app.wms_integration.return_rack_task_completion.repository import ReturnRackTaskCompletionRepository
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database


@pytest.mark.asyncio
async def test_durable_receipt_duplicate_drift_pending_promise_and_current_completion(monkeypatch):
    async with migrated_database() as (_, sessions):
        payload = {
            "operation": "outbound.return_rack.task_completed@v1",
            "operation_id": new_uuid7(),
            "timestamp": 1788390200000,
            "data": {
                "task_id": "PICK-COMPLETE",
                "rack_id": "R1",
                "through_plan_revision": 2,
                "completed_at": 1788390199000,
            },
        }
        service = ReturnRackTaskCompletedService(sessions)
        invalid_payload = deepcopy(payload)
        invalid_payload["operation_id"] = new_uuid7()
        invalid_payload["data"]["through_plan_revision"] = 0
        invalid = parse_return_rack_task_completed_receipt(invalid_payload)
        rejection = await service.record(invalid, received_at=timezone.now_for_db())
        replay = await service.record(invalid, received_at=timezone.now_for_db())
        assert rejection.code == replay.code == "REJECTED"
        assert rejection.reason_code == replay.reason_code == "INVALID_DATA"
        assert rejection.timestamp_ms == replay.timestamp_ms
        async with sessions() as db:
            rejected = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.operation_id == invalid_payload["operation_id"])
            )
            assert rejected.apply_status == "IGNORED" and rejected.processed_at is not None
            assert rejected.picking_task_id is None and rejected.workline_id is None
            assert rejected.normalized_payload == invalid_payload
        event = parse_return_rack_task_completed_event(payload)
        with pytest.raises(RuntimeError, match="PickingTask 尚未发布"):
            await service.record(event, received_at=timezone.now_for_db())
        async with sessions() as db:
            assert (
                await db.scalar(select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id))
                is None
            )
        async with sessions.begin() as db:
            line = WorkLine(
                line_code="RETURN-COMPLETE",
                line_name="退料架完成",
                line_type="MANUAL",
                is_active=True,
                config={"device_bindings": {"SCAN1": "S1", "SCAN2": "S2", "SCAN3": "S3", "SCAN4": "S4"}},
                plugin_key="manual-picking",
                plugin_version="0.1.0",
            )
            db.add(line)
            issued_id = new_uuid7()
            issued = InboundEvidence(
                kind="WMS_EVENT",
                source_identity=f"outbound.picking_task.issued@v1:{issued_id}",
                operation="outbound.picking_task.issued@v1",
                operation_id=issued_id,
                payload_digest="a" * 64,
                normalized_payload={},
                received_at=timezone.now_for_db(),
                apply_status="APPLIED",
            )
            db.add(issued)
            await db.flush()
            task = PickingTask(
                task_id="PICK-COMPLETE",
                task_type="MANUAL",
                status=PickingTaskStatus.EXECUTING,
                queue_revision=1,
                dispatch_sequence=1,
                issued_at_ms=1,
                issued_evidence_id=issued.id,
                workline_id=line.id,
                last_applied_plan_revision=1,
                initial_plan_evidence_id=issued.id,
                last_plan_evidence_id=issued.id,
            )
            db.add(task)
            await db.flush()
            db.add(
                DirectPickExecution(
                    picking_task_id=task.id,
                    rack_id="R1",
                    rack_face="90",
                    slot_id="S1",
                    plan_revision=1,
                    source_evidence_id=issued.id,
                )
            )
        assert (await service.record(event, received_at=timezone.now_for_db())).code == "RECEIVED"
        assert (await service.record(event, received_at=timezone.now_for_db())).code == "DUPLICATE"
        drift = deepcopy(payload)
        drift["data"]["completed_at"] -= 1
        assert (
            await service.record(parse_return_rack_task_completed_event(drift), received_at=timezone.now_for_db())
        ).code == "CONFLICT"
        monkeypatch.setattr(
            "deployment.plugin_composition.task_queue_gateway",
            SimpleNamespace(enqueue_wms_confirmations=Mock(), enqueue_execution_facts=Mock()),
        )
        runtime = build_deployment_runtime(
            enabled_plugin_keys=("manual-picking",),
            session_factory=sessions,
            transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
            device_command_service=object(),
        )
        processor = runtime.execution.fact_processor
        now = timezone.now_for_db()
        processor._clock = lambda: now
        assert await processor.process_batch() == 1
        async with sessions.begin() as db:
            evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id)
            )
            assert evidence.workline_id == line.id and evidence.picking_task_id == task.id
            assert evidence.published_at is None
            assert evidence.decision_next_attempt_at is not None
            fact = ReturnRackTaskCompletedReader().read(evidence)
            repo = ReturnRackTaskCompletionRepository()
            assert await repo.has_completed_racks(db, task.id, ("R1",), 3)
            assert not await repo.has_completed_racks(db, task.id, ("R1",), 2)
            assert not await repo.has_completed_racks(db, task.id, ("OTHER",), 3)
            assert await repo.members(db, task.id, "R1") == (1,)
            task_in_db = await db.get(PickingTask, task.id)
            task_in_db.last_applied_plan_revision = 2
        now += timedelta(seconds=2)
        assert await processor.process_batch() == 1
        async with sessions.begin() as db:
            evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.operation_id == event.operation_id)
            )
            assert evidence.published_at is not None and evidence.decision_digest
            marker = await repo.get(db, task.id, "R1")
            assert marker is not None and marker.operation_id == fact.operation_id
            assert await db.scalar(select(DirectPickFaceCompletion.id)) is None
            plans = PickingTaskPlanDeltaRepository()
            assert await plans.is_direct_pick_face_business_closed(
                db, picking_task_id=task.id, plan_revision=1, rack_id="R1", rack_face="90"
            )
            assert not await plans.is_direct_pick_face_business_closed(
                db, picking_task_id=task.id, plan_revision=3, rack_id="R1", rack_face="90"
            )
            assert not await plans.is_direct_pick_face_business_closed(
                db, picking_task_id=task.id + 1, plan_revision=1, rack_id="R1", rack_face="90"
            )
            assert not await plans.has_direct_pick_face_completion(
                db, picking_task_id=task.id, plan_revision=1, rack_id="R1", rack_face="90"
            )
            assert await runtime.plugins[0].picking_task_completion_driver._repository.ready_to_confirm(db, line, task)
            await db.execute(
                delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id == evidence.id)
            )
            await db.delete(evidence)
            await db.flush()
            assert await repo.has_completed_racks(db, task.id, ("R1",), 3)
