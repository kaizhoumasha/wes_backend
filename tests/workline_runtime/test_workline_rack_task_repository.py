from __future__ import annotations

import pytest

from src.app.workline.models.rack_task import WorklineRackTask, WorklineRackTaskStatus, WorklineRackTaskType
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.repositories.rack_task_repository import WorklineRackTaskRepository


@pytest.mark.asyncio
async def test_cancel_active_by_material_session_closes_only_active_tasks(db_session) -> None:
    session = WorklineSession(
        session_code="session-rack-timeout",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add(session)
    await db_session.flush()

    active_task = WorklineRackTask(
        task_key="rack-task-active",
        operation_key="rack-op-active",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.REQUESTED,
        workline_id=45,
        workline_code="WL-CONVEYOR-02",
        material_session_id=session.id,
        target_position_code="SINGLE_LAYER_A",
    )
    terminal_task = WorklineRackTask(
        task_key="rack-task-terminal",
        operation_key="rack-op-terminal",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=WorklineRackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=WorklineRackTaskStatus.SUCCEEDED,
        workline_id=45,
        workline_code="WL-CONVEYOR-02",
        material_session_id=session.id,
        target_position_code="SINGLE_LAYER_A",
    )
    db_session.add_all([active_task, terminal_task])
    await db_session.flush()

    closed = await WorklineRackTaskRepository().cancel_active_by_material_session(
        db_session,
        material_session_id=session.id,
        reason="CALLBACK_DEADLINE_EXPIRED",
    )

    assert closed == 1
    assert active_task.task_status == WorklineRackTaskStatus.CANCELLED
    assert active_task.error_code == "CALLBACK_DEADLINE_EXPIRED"
    assert active_task.error_message == "CALLBACK_DEADLINE_EXPIRED"
    assert active_task.completed_at is not None
    assert active_task.result_json["status"] == WorklineRackTaskStatus.CANCELLED.value
    assert terminal_task.task_status == WorklineRackTaskStatus.SUCCEEDED
