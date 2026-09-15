"""清线后 PickingTask 队列可立即为同一 WorkLine 领取下一任务。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence, InboundEvidenceKind
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.workline.models import LineType, WorkLine
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.workline_archive_service import WorkLineArchiveService
from src.app.workline_integration_debug.models import IntegrationRun
from src.app.workline_integration_debug.repository import IntegrationRunRepository
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database


@pytest.mark.asyncio
async def test_archive_releases_workline_for_next_queued_picking_task() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PickingTaskRepository()
        integration_runs = IntegrationRunRepository()
        try:
            async with sessions.begin() as db:
                line = WorkLine(
                    line_code="ARCHIVE-LINE",
                    line_name="Archive line",
                    line_type=LineType.MANUAL,
                    is_active=True,
                )
                db.add(line)
                await db.flush()
                evidences = [
                    InboundEvidence(
                        kind=InboundEvidenceKind.WMS_EVENT,
                        source_identity=f"ARCHIVE-TASK-{index}",
                        operation="outbound.picking_task.issued@v1",
                        operation_id=f"019f12d0-58d7-7b4d-a23a-1b90aa5d448{index}",
                        payload_digest=str(index) * 64,
                        normalized_payload={"data": {}},
                        received_at=timezone.now_for_db(),
                    )
                    for index in (1, 2, 3)
                ]
                db.add_all(evidences)
                await db.flush()
                active = PickingTask(
                    task_id="ARCHIVE-CURRENT",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.EXECUTING,
                    queue_revision=1,
                    dispatch_sequence=1,
                    issued_at_ms=1,
                    issued_evidence_id=evidences[1].id,
                    workline_id=line.id,
                    plan_blocked_evidence_id=evidences[0].id,
                )
                completed = PickingTask(
                    task_id="ARCHIVE-COMPLETED",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.EXECUTION_COMPLETED,
                    queue_revision=1,
                    dispatch_sequence=2,
                    issued_at_ms=2,
                    issued_evidence_id=evidences[0].id,
                    workline_id=line.id,
                )
                queued = PickingTask(
                    task_id="ARCHIVE-NEXT",
                    task_type=PickingTaskType.MANUAL,
                    status=PickingTaskStatus.QUEUED,
                    queue_revision=1,
                    dispatch_sequence=3,
                    issued_at_ms=3,
                    issued_evidence_id=evidences[2].id,
                )
                run = IntegrationRun(
                    run_id="archive-active-run",
                    workline_id=line.id,
                    workline_code=line.line_code,
                    scenario_key="manual_outbound_picking@v1",
                    expected_plugin_key="manual-picking",
                    profile="CONTRACT_SIMULATION",
                    environment_label="test",
                    operator_user_id=1,
                    active_scope=f"WORKLINE:{line.id}",
                    status="ACTIVE",
                    current_phase="TASK_PREPARE",
                )
                db.add_all((active, completed, queued, run))
                await db.flush()
                line_id, line_version, active_id, completed_id, queued_id = (
                    line.id,
                    line.version,
                    active.id,
                    completed.id,
                    queued.id,
                )

            async with sessions.begin() as db:
                result = await WorkLineArchiveService(
                    plugins=(),
                    reservation_archiver=integration_runs,
                ).archive_open_work(
                    db,
                    workline_id=line_id,
                    version=line_version,
                )
                assert result.archived_total == 3
                assert result.archived_picking_tasks == 2
                assert result.archived_integration_runs == 1
                assert not await repository.has_active_for_workline(db, line_id)
                assert await integration_runs.get_active_for_workline(db, line_id) is None
                summary = await WorkLineRepository().get_unfinished_workload_summary(db, line_id)
                assert summary["by_type"]["picking_tasks"] == 0
                claimed = await repository.claim_next_queued(db, task_type=PickingTaskType.MANUAL, now_ms=4)
                assert claimed is not None and claimed.id == queued_id

            async with sessions() as db:
                persisted = await db.get(PickingTask, active_id)
                assert persisted is not None
                assert persisted.status == PickingTaskStatus.ARCHIVED
                assert persisted.archived_at is not None
                completed_persisted = await db.get(PickingTask, completed_id)
                assert completed_persisted is not None
                assert completed_persisted.status == PickingTaskStatus.ARCHIVED
                archived_run = await integration_runs.get_run(db, "archive-active-run")
                assert archived_run is not None
                assert archived_run.status == "ARCHIVED"
                assert archived_run.active_scope is None
        finally:
            await engine.dispose()
