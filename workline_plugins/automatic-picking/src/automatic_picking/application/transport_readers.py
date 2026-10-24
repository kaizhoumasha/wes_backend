"""人工线搬运基线的自动插件适配：本线静态绑定与当前执行事实。"""

from sqlalchemy import select
from wes_plugin_sdk import ReturnBufferDrainReady, TransportRackPosition, TransportRcsTemplateId

from src.app.execution.models import InboundEvidence
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader
from src.app.workline.models import WorkLine

from .drain_flow import MatchedDrainBuffer
from .drain_inbound import MatchedDrainInbound
from .drain_repository import DrainRepository
from .feed_model import AutomaticPickingFeed
from .return_allocation_flow import MatchedReturnLocation
from .return_allocation_repository import ReturnAllocationRepository
from .return_motion_repository import ReturnMotionRepository
from .return_repository import ReturnRepository
from .source_rack_motion import MatchedSourceRackRotation


class ReturnBufferReader:
    async def read_in_session(self, db, *, workline_id):
        line = await db.get(WorkLine, workline_id)
        if line is None:
            return None
        # 与人工线 return_batch/drain 一致，每批最多四箱；不是货架进场 capacity。
        return MatchedDrainBuffer(line.position_bindings["OUTLET"]["location_id"], 4)


class ReturnLocationReader:
    async def read_in_session(self, db, *, return_id):
        row = await ReturnRepository().by_id_for_update(db, return_id)
        if row is None or row.archived_at is not None or not await ReturnAllocationRepository().qualified(db, row):
            return None
        line = await db.get(WorkLine, row.workline_id)
        if line is None:
            return None
        return MatchedReturnLocation(
            row.id, row.workline_id, row.bin_code, line.position_bindings["OUTLET"]["location_id"]
        )


class DrainInboundReader:
    async def read_in_session(self, db, *, workline_id, operation_id, ready_evidence_id, rack_id):
        line = await db.get(WorkLine, workline_id)
        root = await DrainRepository().current(db, workline_id)
        if (
            line is None
            or root is None
            or (root.operation_id, root.response_evidence_id) != (operation_id, ready_evidence_id)
        ):
            return None
        record = await ReturnBufferDrainResultReader().for_operation(
            db, workline_id=workline_id, operation_id=operation_id
        )
        if (
            record is None
            or type(record.result) is not ReturnBufferDrainReady
            or record.evidence_id != ready_evidence_id
        ):
            return None
        fact = await db.get(InboundEvidence, ready_evidence_id)
        if fact is None or fact.published_at is None:
            return None
        rack = next((r for r in record.result.racks if r.rack_id == rack_id), None)
        if rack is None:
            return None
        return MatchedDrainInbound(
            workline_id,
            operation_id,
            ready_evidence_id,
            rack_id,
            TransportRackPosition(line.position_bindings["FIVE_RACK"]["location_id"]),
            rack.rack_faces[0],
            TransportRcsTemplateId.CTU01,
        )


class SourceRackRotationReader:
    async def read_in_session(self, db, *, workline_id):
        line = await db.get(WorkLine, workline_id)
        if line is None or not line.is_active:
            return ()
        result = []
        for current in await ReturnMotionRepository().current(db, workline_id):
            if (
                current.closed_at is None
                or current.inbound_client_request_id is None
                or current.departure_operation_id is not None
                or current.rotation_transport_task_id is not None
            ):
                continue
            end = await db.get(InboundEvidence, current.service_end_evidence_id)
            task = await db.get(PickingTask, end.picking_task_id) if end else None
            if task is None or task.status != "EXECUTING" or task.plan_blocked_evidence_id is not None:
                continue
            candidates = await db.scalars(
                select(PickingTaskBinSourceRack)
                .where(
                    PickingTaskBinSourceRack.picking_task_id == task.id,
                    PickingTaskBinSourceRack.rack_id == current.rack_id,
                    PickingTaskBinSourceRack.cancelled_evidence_id.is_(None),
                    PickingTaskBinSourceRack.plan_revision <= task.last_applied_plan_revision,
                )
                .order_by(PickingTaskBinSourceRack.plan_revision, PickingTaskBinSourceRack.id)
            )
            for member in candidates:
                if member.id in current.completed_source_member_ids:
                    continue
                feed = await db.scalar(
                    select(AutomaticPickingFeed.id).where(AutomaticPickingFeed.source_member_id == member.id)
                )
                if feed is not None:
                    continue
                if member.rack_face != current.rack_face:
                    result.append(
                        MatchedSourceRackRotation(
                            current.id,
                            member.id,
                            current.rack_id,
                            TransportRackPosition(line.position_bindings["FIVE_RACK"]["location_id"]),
                            member.rack_face,
                        )
                    )
                break
        return tuple(result)
