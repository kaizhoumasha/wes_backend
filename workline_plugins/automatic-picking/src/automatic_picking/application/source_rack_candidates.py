"""从当前来源事实发现既有运动/离场输入；调用方持有 WorkLine 事务锁。"""

from sqlalchemy import or_, select
from wes_plugin_sdk import BinInboundBatchRackFaceDone, BinInboundBatchReady

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.workline.models import WorkLine

from .feed_model import AutomaticPickingFeed
from .feed_repository import FeedRepository
from .return_allocation_flow import MatchedReturnFace, MatchedReturnMotion
from .return_motion_repository import ReturnMotionRepository
from .source_rack_departure import MatchedSourceRackDeparture
from .source_rack_departure_repository import SourceRackDepartureRepository


class SourceRackFaceReader:
    async def read_in_session(self, db, *, workline_id):
        line = await db.get(WorkLine, workline_id)
        if line is None or not line.is_active:
            return ()
        expected = {"kind": "RACK_POSITION", "location_code": line.position_bindings["FIVE_RACK"]["location_id"]}
        positions = await db.scalars(
            select(PositionProjection)
            .where(
                PositionProjection.workline_id == line.id,
                PositionProjection.object_type == "RACK",
                PositionProjection.position_unknown.is_(False),
            )
            .order_by(PositionProjection.object_id)
        )
        result = []
        motions = ReturnMotionRepository()
        for position in positions:
            if position.position_json != expected or not position.arrival_face:
                continue
            facts = await db.scalars(
                select(InboundEvidence).where(
                    InboundEvidence.workline_id == line.id,
                    InboundEvidence.transport_task_id == position.source_transport_task_id,
                    InboundEvidence.kind == "TRANSPORT_RESULT",
                    InboundEvidence.apply_status == "APPLIED",
                    InboundEvidence.published_at.is_not(None),
                )
            )
            for fact in facts:
                arrival = await motions.current_arrival(
                    db, line.id, fact.id, position.object_id, position.arrival_face, expected
                )
                if arrival is None or arrival[1].step not in {
                    "PICKING_TASK_BIN_SOURCE_RACK_IN",
                    "AUTOMATIC_PICKING_SOURCE_RACK_ROTATE",
                }:
                    continue
                result.append(MatchedReturnFace(fact.id, line.id, position.object_id, position.arrival_face))
                break
        return tuple(result)


class SourceRackMotionReader:
    async def read_in_session(self, db, *, workline_id):
        faces = await SourceRackFaceReader().read_in_session(db, workline_id=workline_id)
        motions, feeds, result = ReturnMotionRepository(), FeedRepository(), []
        current = await motions.current(db, workline_id)
        for face in faces:
            if any(
                p.arrival_evidence_id == face.arrival_evidence_id
                and p.rack_id == face.rack_id
                and p.rack_face == face.rack_face
                and p.inbound_client_request_id is not None
                for p in current
            ):
                continue
            arrival = await db.get(InboundEvidence, face.arrival_evidence_id)
            action = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == arrival.transport_task_id)
            )
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == action.client_request_id
                )
            )
            candidates = await db.scalars(
                select(AutomaticPickingFeed)
                .join(PickingTaskBinSourceRack, PickingTaskBinSourceRack.id == AutomaticPickingFeed.source_member_id)
                .where(
                    AutomaticPickingFeed.workline_id == workline_id,
                    AutomaticPickingFeed.picking_task_id == arrival.picking_task_id,
                    AutomaticPickingFeed.archived_at.is_(None),
                    AutomaticPickingFeed.phase == "FEED_COMPLETE",
                    PickingTaskBinSourceRack.rack_id == face.rack_id,
                    PickingTaskBinSourceRack.rack_face == face.rack_face,
                )
                .order_by(AutomaticPickingFeed.id)
            )
            for feed in candidates:
                if feed.inbound_client_request_id is not None:
                    if feed.source_arrival_evidence_id != arrival.id:
                        continue
                    root = feed.inbound_client_request_id
                elif binding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN":
                    root = binding.client_request_id
                else:
                    continue
                request = await db.scalar(
                    select(WmsConfirmation).where(
                        WmsConfirmation.operation == "outbound.bin.inbound_batch@v1",
                        WmsConfirmation.operation_id == feed.operation_id,
                    )
                )
                if request is None or request.created_at < arrival.received_at:
                    continue
                snapshot = await feeds.inbound(db, feed)
                if snapshot is None:
                    continue
                _, outcome, response = snapshot
                end = response if type(outcome) is BinInboundBatchRackFaceDone else None
                if type(outcome) is BinInboundBatchReady and feed.transport_task_id is not None:
                    transport = await feeds.chunk_state(db, feed)
                    if transport is not None:
                        end = await db.scalar(
                            select(InboundEvidence).where(
                                InboundEvidence.transport_task_id == transport.transport_task_id,
                                InboundEvidence.kind == "TRANSPORT_RESULT",
                                InboundEvidence.apply_status == "APPLIED",
                                InboundEvidence.published_at.is_not(None),
                                InboundEvidence.normalized_payload["outcome_version"].as_integer()
                                == transport.outcome_version,
                            )
                        )
                if end is None or end.published_at is None:
                    continue
                unfinished = await db.scalar(
                    select(PickingTaskBinSourceRack.id)
                    .outerjoin(
                        AutomaticPickingFeed, AutomaticPickingFeed.source_member_id == PickingTaskBinSourceRack.id
                    )
                    .outerjoin(InboundEvidence, InboundEvidence.id == AutomaticPickingFeed.response_evidence_id)
                    .where(
                        PickingTaskBinSourceRack.picking_task_id == arrival.picking_task_id,
                        PickingTaskBinSourceRack.rack_id == face.rack_id,
                        PickingTaskBinSourceRack.rack_face == face.rack_face,
                        PickingTaskBinSourceRack.cancelled_evidence_id.is_(None),
                        PickingTaskBinSourceRack.id.not_in(feed.completed_source_member_ids),
                        or_(
                            AutomaticPickingFeed.id.is_(None),
                            AutomaticPickingFeed.archived_at.is_not(None),
                            AutomaticPickingFeed.phase != "FEED_COMPLETE",
                            AutomaticPickingFeed.response_evidence_id.is_(None),
                            InboundEvidence.published_at.is_(None),
                            InboundEvidence.apply_status != "APPLIED",
                        ),
                    )
                    .limit(1)
                )
                if unfinished is not None:
                    continue
                result.append(MatchedReturnMotion(face, face.arrival_evidence_id, root, feed.id, end.id))
                break
        return tuple(result)


class SourceRackDepartureReader:
    async def read_in_session(self, db, *, workline_id):
        line = await db.get(WorkLine, workline_id)
        if line is None or not line.is_active:
            return ()
        motions = ReturnMotionRepository()
        repository, result = SourceRackDepartureRepository(motions), []
        for current in await motions.current(db, workline_id):
            if (
                current.closed_at is None
                or current.inbound_client_request_id is None
                or current.departure_operation_id is not None
            ):
                continue
            if await repository.current_context(db, line, current) is None or not await repository.service_finished(
                db, current
            ):
                continue
            end = await db.get(InboundEvidence, current.service_end_evidence_id)
            task = await db.get(PickingTask, end.picking_task_id)
            if task.status == "EXECUTING":
                if task.plan_blocked_evidence_id is not None:
                    continue
                members = await db.scalars(
                    select(PickingTaskBinSourceRack).where(
                        PickingTaskBinSourceRack.picking_task_id == task.id,
                        PickingTaskBinSourceRack.rack_id == current.rack_id,
                        PickingTaskBinSourceRack.cancelled_evidence_id.is_(None),
                        PickingTaskBinSourceRack.created_at <= current.created_at,
                    )
                )
                if any(member.id not in current.completed_source_member_ids for member in members):
                    continue
            result.append(MatchedSourceRackDeparture(current.id))
        return tuple(result)
