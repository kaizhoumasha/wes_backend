from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus
from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import parse_manual_bin_completed_event
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services.manual_bin_completed import ManualBinCompletedService
from src.app.workline.models import LineType, WorkLine
from src.app.workline_integration_debug.models import IntegrationRun
from src.app.workline_integration_debug.service import IntegrationRunWorkLineOwner
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import migrated_database

pytestmark = pytest.mark.asyncio


async def test_manual_bin_completion_is_persisted_before_received_ack() -> None:
    operation_id = new_uuid7()
    operation = "outbound.manual_bin.work_completed@v1"
    event = parse_manual_bin_completed_event(
        {
            "operation_id": operation_id,
            "operation": operation,
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": "NORMAL",
                "completed_at": 1_788_389_999_000,
            },
        }
    )
    async with migrated_database() as (_url, sessions):
        service = ManualBinCompletedService(sessions)
        try:
            received = await service.record(event, received_at=timezone.now_for_db())
            duplicate = await service.record(event, received_at=timezone.now_for_db())
            async with sessions() as db:
                evidence = await db.scalar(
                    select(InboundEvidence).where(
                        InboundEvidence.operation == operation,
                        InboundEvidence.operation_id == operation_id,
                    )
                )

            assert received.code == "RECEIVED"
            assert duplicate.code == "DUPLICATE"
            assert evidence is not None
            assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
            assert evidence.normalized_payload["data"]["bin_code"] == "BIN-001"
        finally:
            async with sessions.begin() as db:
                await db.execute(
                    delete(InboundEvidence).where(
                        InboundEvidence.operation == operation,
                        InboundEvidence.operation_id == operation_id,
                    )
                )


@pytest.mark.parametrize("debug_owned", [False, True])
async def test_manual_bin_completion_freezes_executing_task_workline_for_plugin(debug_owned: bool) -> None:
    operation_id = new_uuid7()
    issued_operation_id = new_uuid7()
    event = parse_manual_bin_completed_event(
        {
            "operation_id": operation_id,
            "operation": "outbound.manual_bin.work_completed@v1",
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "bin_code": "A000000001",
                "result": "NORMAL",
                "completed_at": 1_788_389_999_000,
            },
        }
    )
    async with migrated_database() as (_url, sessions):
        async with sessions.begin() as db:
            workline = WorkLine(
                line_code=f"MANUAL-{operation_id[-12:]}",
                line_name="Manual picking",
                line_type=LineType.MANUAL,
                is_active=True,
                plugin_key="manual-picking",
                plugin_version="0.1.0",
            )
            db.add(workline)
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
                task_id="PICK-001",
                task_type=PickingTaskType.MANUAL,
                status=PickingTaskStatus.EXECUTING,
                queue_revision=1,
                dispatch_sequence=1,
                issued_at_ms=1,
                issued_evidence_id=issued.id,
                workline_id=workline.id,
            )
            db.add(task)
            if debug_owned:
                db.add(
                    IntegrationRun(
                        run_id=f"debug-{operation_id}",
                        workline_id=workline.id,
                        workline_code=workline.line_code,
                        scenario_key="manual_outbound_picking@v1",
                        expected_plugin_key="manual-picking",
                        profile="DEVICE_INTEGRATION",
                        environment_label="test",
                        operator_user_id=1,
                        active_scope=f"WORKLINE:{workline.id}",
                        status="WAITING_EXTERNAL",
                        current_phase="WORK_COMPLETION",
                        task_id="PICK-001",
                        bin_code="A000000001",
                    )
                )

        service = ManualBinCompletedService(sessions, completion_owner=IntegrationRunWorkLineOwner())
        received = await service.record(event, received_at=timezone.now_for_db())
        duplicate = await service.record(event, received_at=timezone.now_for_db())
        async with sessions() as db:
            evidence = await db.scalar(select(InboundEvidence).where(InboundEvidence.operation_id == operation_id))

        assert received.code == "RECEIVED"
        assert duplicate.code == "DUPLICATE"
        assert evidence is not None
        assert evidence.workline_id == workline.id
        assert evidence.apply_status == (
            InboundEvidenceApplyStatus.PENDING if debug_owned else InboundEvidenceApplyStatus.APPLIED
        )
