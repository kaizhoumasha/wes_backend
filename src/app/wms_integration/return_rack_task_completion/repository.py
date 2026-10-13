"""整架完成当前事实及计划范围的持久化读取。"""

from sqlalchemy import select

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
        # 调用方持有 PickingTask 锁；截止版本只前进，迟到旧通知不覆盖新完成事实。
        existing = await self.get(db, fact.picking_task_id, fact.rack_id)
        if existing is None:
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
        elif existing.through_plan_revision < fact.through_plan_revision:
            existing.through_plan_revision = fact.through_plan_revision
            existing.operation_id = fact.operation_id
            existing.completed_at_ms = fact.completed_at_ms
            existing.source_evidence_id = fact.evidence_id
            existing.increment_version()
        await db.flush()
