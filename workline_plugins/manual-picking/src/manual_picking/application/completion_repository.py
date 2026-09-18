"""人工拣料本地完成条件：计划来源结清、无未结清的直接取料面、无待 WMS 回答的料箱。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, or_, select

from src.app.execution.models import TransportDecisionBinding
from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.transport.models import TransportMember, TransportTask
from src.app.transport.repository import TransportRepository
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, DirectPickFaceCompletion
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader
from src.app.wms_integration.outbound_picking.services.picking_task_completion import PickingTaskCompletionResultReader

from .batch_repository import BatchRepository
from .passage_model import ManualPickingPassage
from .rack_readiness import rack_ready

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ManualPickingCompletionRepository:
    def __init__(
        self,
        *,
        plans: Any = None,
        history: Any = None,
        completion_reader: Any = None,
        positions: Any = None,
        transports: Any = None,
    ) -> None:
        self._plans = plans or PickingTaskPlanDeltaRepository()
        self._history = history or BinBatchResultReader()
        self._batches = BatchRepository(self._history)
        self._completion_reader = completion_reader or PickingTaskCompletionResultReader()
        self._positions = positions or position_projection_repository
        self._transports = transports or TransportRepository()

    async def latest_confirmation(self, db: AsyncSession, picking_task_id: int) -> Any:
        return await self._completion_reader.latest(db, picking_task_id)

    async def ready_to_confirm(self, db: AsyncSession, line: Any, task: Any) -> bool:
        """计划已接纳直接取料，因此只有未结清的面（未取消且无面级完成事实）才阻塞完成确认。"""
        direct = cast("Any", DirectPickExecution).__table__.c
        completed = cast("Any", DirectPickFaceCompletion).__table__.c
        unclosed_face = (
            select(direct.id)
            .where(
                direct.picking_task_id == task.id,
                direct.cancelled_evidence_id.is_(None),
                ~select(completed.id)
                .where(
                    completed.picking_task_id == direct.picking_task_id,
                    completed.rack_id == direct.rack_id,
                    completed.rack_face == direct.rack_face,
                )
                .exists(),
            )
            .limit(1)
        )
        if await db.scalar(unclosed_face) is not None:
            return False
        passage = cast("Any", ManualPickingPassage).__table__.c
        if (
            await db.scalar(
                select(passage.id)
                .where(
                    passage.workline_id == line.id,
                    passage.task_id == task.task_id,
                    passage.disposition != "CLOSED",
                    passage.wms_result.is_(None),
                )
                .limit(1)
            )
            is not None
        ):
            return False
        sources = await self._plans.list_bin_source_racks(db, task.id)
        if not sources and not await rack_ready(
            db,
            line,
            task.target_rack_id,
            task.target_rack_face,
            line.position_bindings["TRANSFER_RACK"]["location_id"],
            positions=self._positions,
            transports=self._transports,
        ):
            return False
        for source in sources:
            progress = await self._batches.inbound_progress(
                db,
                line.id,
                task.task_id,
                source.rack_id,
                source.rack_face,
                line.position_bindings["INLET"]["location_id"],
            )
            if progress is not None:
                if progress.complete:
                    continue
                return False
            binding = cast("Any", TransportDecisionBinding).__table__.c
            transport = cast("Any", TransportTask).__table__.c
            member = cast("Any", TransportMember).__table__.c
            terminal_failure = await db.scalar(
                select(binding.id)
                .select_from(
                    TransportDecisionBinding.__table__.join(
                        TransportTask.__table__, binding.client_request_id == transport.client_request_id
                    ).outerjoin(
                        TransportMember.__table__,
                        and_(
                            member.transport_task_id == transport.transport_task_id,
                            member.object_type == "RACK",
                            member.object_id == source.rack_id,
                        ),
                    )
                )
                .where(
                    binding.workline_id == line.id,
                    binding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                    binding.resource_fence_id == source.rack_id,
                    binding.source_evidence_id == source.source_evidence_id,
                    transport.authority_workline_id == line.id,
                    transport.outcome_version > 0,
                    transport.published_outcome_version >= transport.outcome_version,
                    or_(
                        transport.status == "REJECTED",
                        and_(
                            transport.status == "FAILED",
                            member.status == "FAILED",
                            member.position_unknown.is_(False),
                            member.final_position_json.is_not(None),
                        ),
                    ),
                )
                .limit(1)
            )
            if terminal_failure is None:
                return False
        return True


__all__ = ["ManualPickingCompletionRepository"]
