"""PickingTask 当前 WorkLine 读取的 PostgreSQL 合同。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence, InboundEvidenceKind
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.workline.models import LineType, WorkLine
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database


@pytest.mark.asyncio
async def test_get_active_for_workline_filters_statuses_and_returns_without_lock() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PickingTaskRepository()
        try:
            async with sessions.begin() as db:
                lines = [
                    WorkLine(
                        line_code=f"CURRENT-TASK-{index}",
                        line_name=f"Current task {index}",
                        line_type=LineType.MANUAL,
                        is_active=True,
                    )
                    for index in range(1, 4)
                ]
                db.add_all(lines)
                await db.flush()
                evidences = [
                    InboundEvidence(
                        kind=InboundEvidenceKind.WMS_EVENT,
                        source_identity=f"CURRENT-TASK-EVIDENCE-{index}",
                        operation="outbound.picking_task.issued@v1",
                        operation_id=f"019f12d0-58d7-7b4d-a23a-1b90aa5d44{index:02d}",
                        payload_digest=str(index) * 64,
                        normalized_payload={"data": {}},
                        received_at=timezone.now_for_db(),
                    )
                    for index in range(1, 6)
                ]
                db.add_all(evidences)
                await db.flush()
                preparing = PickingTask(
                    task_id="CURRENT-PREPARING",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.PREPARING,
                    queue_revision=1,
                    dispatch_sequence=1,
                    issued_at_ms=1,
                    issued_evidence_id=evidences[0].id,
                    workline_id=lines[0].id,
                )
                executing = PickingTask(
                    task_id="CURRENT-EXECUTING",
                    task_type=PickingTaskType.AUTO,
                    status=PickingTaskStatus.EXECUTING,
                    queue_revision=1,
                    dispatch_sequence=2,
                    issued_at_ms=2,
                    issued_evidence_id=evidences[1].id,
                    workline_id=lines[1].id,
                )
                completed = PickingTask(
                    task_id="CURRENT-COMPLETED",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.EXECUTION_COMPLETED,
                    queue_revision=1,
                    dispatch_sequence=3,
                    issued_at_ms=3,
                    issued_evidence_id=evidences[2].id,
                    workline_id=lines[0].id,
                )
                queued = PickingTask(
                    task_id="CURRENT-QUEUED",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.QUEUED,
                    queue_revision=1,
                    dispatch_sequence=4,
                    issued_at_ms=4,
                    issued_evidence_id=evidences[3].id,
                    workline_id=lines[2].id,
                )
                db.add_all((preparing, executing, completed, queued))

            async with sessions() as db:
                preparing_result = await repository.get_active_for_workline(db, lines[0].id)
                executing_result = await repository.get_active_for_workline(db, lines[1].id)
                empty_result = await repository.get_active_for_workline(db, lines[2].id)

            assert preparing_result is not None
            assert preparing_result.task_id == "CURRENT-PREPARING"
            assert executing_result is not None
            assert executing_result.task_id == "CURRENT-EXECUTING"
            assert empty_result is None
        finally:
            await engine.dispose()
