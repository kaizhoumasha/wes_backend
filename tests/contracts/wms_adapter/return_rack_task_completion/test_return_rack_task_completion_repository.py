from dataclasses import replace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.wms_integration.outbound_picking.models import DirectPickFaceCompletion
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.wms_integration.return_rack_task_completion.models import ReturnRackTaskCompletion
from src.app.wms_integration.return_rack_task_completion.repository import ReturnRackTaskCompletionRepository
from src.app.wms_integration.return_rack_task_completion.types import ReturnRackTaskCompletedFact


@pytest.mark.asyncio
async def test_completion_watermark_advances_without_closing_later_members_or_regressing_on_replay():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")

    async with engine.begin() as connection:
        for table in (ReturnRackTaskCompletion.__table__, DirectPickFaceCompletion.__table__):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ReturnRackTaskCompletionRepository()
    plans = PickingTaskPlanDeltaRepository()
    first = ReturnRackTaskCompletedFact(91, 31, "PICK-1", "R1", 1, 1788390199000, "first-completion")
    later = replace(first, evidence_id=92, through_plan_revision=4, completed_at_ms=1788390200000, operation_id="later")
    try:
        async with sessions.begin() as db:
            await repository.add(db, first)
            marker_id = (await repository.get(db, 31, "R1")).id
            assert await plans.is_direct_pick_face_business_closed(
                db, picking_task_id=31, plan_revision=1, rack_id="R1", rack_face="90"
            )
            assert not await plans.is_direct_pick_face_business_closed(
                db, picking_task_id=31, plan_revision=4, rack_id="R1", rack_face="90"
            )
        async with sessions.begin() as db:
            await repository.add(db, later)
        for replay in (first, replace(first, through_plan_revision=2), replace(later, operation_id="same-range")):
            async with sessions.begin() as db:
                await repository.add(db, replay)
                marker = await repository.get(db, 31, "R1")
                assert marker.id == marker_id
                assert (marker.through_plan_revision, marker.operation_id, marker.source_evidence_id) == (
                    4,
                    "later",
                    92,
                )
                assert marker.completed_at_ms == later.completed_at_ms
                assert await db.scalar(select(func.count()).select_from(ReturnRackTaskCompletion)) == 1
                assert await plans.is_direct_pick_face_business_closed(
                    db, picking_task_id=31, plan_revision=4, rack_id="R1", rack_face="270"
                )
                for task_id, rack_id, revision in ((31, "R1", 5), (32, "R1", 1), (31, "R2", 1)):
                    assert not await plans.is_direct_pick_face_business_closed(
                        db, picking_task_id=task_id, plan_revision=revision, rack_id=rack_id, rack_face="90"
                    )
    finally:
        await engine.dispose()
