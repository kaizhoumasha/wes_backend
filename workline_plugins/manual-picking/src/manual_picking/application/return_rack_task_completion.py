"""插件核对整架业务完成；整架通知是权威业务事实，计划版本须连续应用。"""

from dataclasses import asdict

from src.app.execution.plugin_binding import BusinessEvidenceApplication, BusinessEvidenceDisposition
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.wms_integration.return_rack_task_completion.repository import ReturnRackTaskCompletionRepository
from src.utils.canonical_json import canonical_json_digest


class ManualPickingReturnRackTaskCompletion:
    def __init__(self, *, tasks=None, completions=None):
        self._tasks = tasks or PickingTaskRepository()
        self._completions = completions or ReturnRackTaskCompletionRepository()

    async def apply_in_session(self, db, fact, workline_id):
        digest = canonical_json_digest(asdict(fact))
        task = await self._tasks.get_by_task_id_for_update(db, fact.task_id)
        if task is None or task.id != fact.picking_task_id or task.workline_id != workline_id:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        if task.last_applied_plan_revision < fact.through_plan_revision:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.DEFERRED, retry_after_ms=1000)
        existing = await self._completions.get(db, task.id, fact.rack_id)
        if existing is not None:
            if (
                existing.operation_id != fact.operation_id
                or existing.through_plan_revision != fact.through_plan_revision
                or existing.completed_at_ms != fact.completed_at_ms
            ):
                return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.APPLIED, digest)
        members = await self._completions.members(db, task.id, fact.rack_id)
        if not members or any(revision > fact.through_plan_revision for revision in members):
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        await self._completions.add(db, fact)
        return BusinessEvidenceApplication(BusinessEvidenceDisposition.APPLIED, digest)
