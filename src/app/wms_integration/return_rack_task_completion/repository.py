"""整架完成当前事实及计划范围的持久化读取。"""

from sqlalchemy import select

from src.app.execution.models import InboundEvidence
from src.app.wms_integration.outbound_picking.models.plan_members import DirectPickExecution

from .models import ReturnRackTaskCompletion


class ReturnRackTaskCompletionRepository:
    async def get(self, db, picking_task_id, rack_id):
        return await db.scalar(
            select(ReturnRackTaskCompletion).where(
                ReturnRackTaskCompletion.picking_task_id == picking_task_id,
                ReturnRackTaskCompletion.rack_id == rack_id,
            )
        )

    async def members(self, db, picking_task_id, rack_id):
        return tuple(
            (
                await db.scalars(
                    select(DirectPickExecution.plan_revision).where(
                        DirectPickExecution.picking_task_id == picking_task_id,
                        DirectPickExecution.rack_id == rack_id,
                    )
                )
            ).all()
        )

    async def add(self, db, fact):
        db.add(
            ReturnRackTaskCompletion(
                picking_task_id=fact.picking_task_id,
                rack_id=fact.rack_id,
                operation_id=fact.operation_id,
                through_plan_revision=fact.through_plan_revision,
                completed_at_ms=fact.completed_at_ms,
                source_evidence_id=fact.evidence_id,
            )
        )
        await db.flush()

    async def has_completed_racks(self, db, picking_task_id, rack_ids, plan_revision):
        if not rack_ids:
            return False
        applied = (
            await db.scalar(
                select(ReturnRackTaskCompletion.id)
                .where(
                    ReturnRackTaskCompletion.picking_task_id == picking_task_id,
                    ReturnRackTaskCompletion.rack_id.in_(rack_ids),
                )
                .limit(1)
            )
        ) is not None

        if applied:
            return True
        # 未发布接收事实是当前可靠义务；已发布历史由独立当前完成事实承接。
        columns = InboundEvidence.__table__.c
        return (
            await db.scalar(
                select(columns.id)
                .where(
                    columns.picking_task_id == picking_task_id,
                    columns.operation == "outbound.return_rack.task_completed@v1",
                    columns.kind == "WMS_EVENT",
                    columns.apply_status.in_(("PENDING", "APPLIED")),
                    columns.published_at.is_(None),
                    columns.normalized_payload["data"]["rack_id"].as_string().in_(rack_ids),
                    columns.normalized_payload["data"]["through_plan_revision"].as_integer() < plan_revision,
                )
                .limit(1)
            )
        ) is not None
