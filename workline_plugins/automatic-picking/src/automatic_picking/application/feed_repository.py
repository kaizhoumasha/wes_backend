"""自动投料只引用当前成员、冻结决定和本段实际履约事实。"""

from sqlalchemy import or_, select
from sqlmodel import col

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader
from src.utils.canonical_json import canonical_json_digest

from .feed_model import AutomaticPickingFeed, AutomaticPickingFeedScan
from .return_motion_model import AutomaticPickingReturnMotion


class FeedRepository:
    def __init__(self, reader=None):
        self.reader = reader or BinBatchResultReader()

    async def source_members(self, db, task_id):
        return list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack)
                    .where(col(PickingTaskBinSourceRack.picking_task_id) == task_id)
                    .order_by(col(PickingTaskBinSourceRack.id))
                )
            ).all()
        )

    async def source_ready(self, db, line, member):
        leaving = await db.scalar(
            select(AutomaticPickingReturnMotion.id).where(
                AutomaticPickingReturnMotion.workline_id == line.id,
                AutomaticPickingReturnMotion.rack_id == member.rack_id,
                AutomaticPickingReturnMotion.departure_operation_id.is_not(None),
                AutomaticPickingReturnMotion.archived_at.is_(None),
            )
        )
        if leaving is not None:
            return False
        position = await db.scalar(
            select(PositionProjection).where(
                col(PositionProjection.object_type) == "RACK", col(PositionProjection.object_id) == member.rack_id
            )
        )
        target = {"kind": "RACK_POSITION", "location_code": line.position_bindings["FIVE_RACK"]["location_id"]}
        if (
            position is None
            or position.position_unknown
            or position.workline_id != line.id
            or position.position_json != target
            or position.arrival_face != member.rack_face
        ):
            return False
        row = await db.scalar(
            select(TransportTask)
            .join(
                TransportDecisionBinding,
                col(TransportDecisionBinding.client_request_id) == col(TransportTask.client_request_id),
            )
            .where(
                col(TransportTask.transport_task_id) == position.source_transport_task_id,
                col(TransportDecisionBinding.workline_id) == line.id,
                col(TransportDecisionBinding.picking_task_id) == member.picking_task_id,
                col(TransportDecisionBinding.resource_fence_id) == member.rack_id,
            )
        )
        if row is None or row.status != "SUCCEEDED" or row.outcome_version != row.published_outcome_version:
            return False
        binding = await db.scalar(
            select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == row.client_request_id)
        )
        current = await self.rotation_round(db, member)
        if current is not None and member.id in current["completed_source_member_ids"]:
            return False
        return binding.step != "AUTOMATIC_PICKING_SOURCE_RACK_ROTATE" or current is not None

    async def by_operation(self, db, operation_id):
        return await db.scalar(
            select(AutomaticPickingFeed).where(col(AutomaticPickingFeed.operation_id) == operation_id).with_for_update()
        )

    async def by_member(self, db, member_id):
        return await db.scalar(
            select(AutomaticPickingFeed)
            .where(col(AutomaticPickingFeed.source_member_id) == member_id)
            .with_for_update()
        )

    async def open_for_face(self, db, workline_id, member):
        return await db.scalar(
            select(AutomaticPickingFeed)
            .join(PickingTaskBinSourceRack, AutomaticPickingFeed.source_member_id == PickingTaskBinSourceRack.id)
            .where(
                AutomaticPickingFeed.workline_id == workline_id,
                AutomaticPickingFeed.picking_task_id == member.picking_task_id,
                PickingTaskBinSourceRack.rack_id == member.rack_id,
                PickingTaskBinSourceRack.rack_face == member.rack_face,
                AutomaticPickingFeed.phase.in_(("REQUESTED", "ALLOCATED", "FEEDING", "RECONCILING")),
                AutomaticPickingFeed.archived_at.is_(None),
            )
            .order_by(AutomaticPickingFeed.id)
            .limit(1)
            .with_for_update(of=AutomaticPickingFeed)
        )

    async def rotation_round(self, db, member):
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == member.rack_id
            )
        )
        if position is None:
            return None
        arrival_id = await db.scalar(
            select(InboundEvidence.id)
            .where(
                InboundEvidence.transport_task_id == position.source_transport_task_id,
                InboundEvidence.kind == "TRANSPORT_RESULT",
                InboundEvidence.apply_status == "APPLIED",
                InboundEvidence.published_at.is_not(None),
            )
            .order_by(InboundEvidence.id.desc())
        )
        if arrival_id is None:
            return None
        root = await db.scalar(
            select(AutomaticPickingReturnMotion)
            .join(InboundEvidence, InboundEvidence.id == AutomaticPickingReturnMotion.service_end_evidence_id)
            .where(
                InboundEvidence.picking_task_id == member.picking_task_id,
                AutomaticPickingReturnMotion.workline_id == position.workline_id,
                AutomaticPickingReturnMotion.arrival_evidence_id == arrival_id,
                AutomaticPickingReturnMotion.rack_id == member.rack_id,
                AutomaticPickingReturnMotion.rack_face == member.rack_face,
                AutomaticPickingReturnMotion.inbound_client_request_id.is_not(None),
                AutomaticPickingReturnMotion.archived_at.is_(None),
            )
        )
        if root is None:
            root = await db.scalar(
                select(AutomaticPickingFeed)
                .where(
                    AutomaticPickingFeed.picking_task_id == member.picking_task_id,
                    AutomaticPickingFeed.workline_id == position.workline_id,
                    AutomaticPickingFeed.source_arrival_evidence_id == arrival_id,
                    AutomaticPickingFeed.inbound_client_request_id.is_not(None),
                    AutomaticPickingFeed.archived_at.is_(None),
                )
                .order_by(AutomaticPickingFeed.id)
            )
        if root is None:
            return None
        return {
            "inbound_client_request_id": root.inbound_client_request_id,
            "round_started_at": root.round_started_at,
            "round_causal_token": root.round_causal_token,
            "source_arrival_evidence_id": arrival_id,
            "completed_source_member_ids": list(root.completed_source_member_ids),
        }

    async def add_feed(self, db, **values):
        if "inbound_client_request_id" not in values:
            member = await db.get(PickingTaskBinSourceRack, values["source_member_id"])
            transferred = await self.rotation_round(db, member)
            if transferred is not None:
                values.update(transferred)
        feed = AutomaticPickingFeed(**values)
        db.add(feed)
        await db.flush()
        return feed

    async def list_current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingFeed)
                    .where(
                        col(AutomaticPickingFeed.workline_id) == line_id,
                        col(AutomaticPickingFeed.archived_at).is_(None),
                    )
                    .order_by(col(AutomaticPickingFeed.id))
                    .with_for_update()
                )
            ).all()
        )

    async def list_recoverable(self, db, line_id):
        return [
            feed for feed in await self.list_current(db, line_id) if feed.phase in {"REQUESTED", "ALLOCATED", "FEEDING"}
        ]

    async def list_admission_pending(self, db, line_id):
        return [feed for feed in await self.list_current(db, line_id) if not await self.handoff_complete(db, feed)]

    async def handoff_complete(self, db, feed):
        if feed.phase not in {"FEED_COMPLETE", "CANCELLED"} or feed.response_evidence_id is None:
            return False
        evidence = await db.get(InboundEvidence, feed.response_evidence_id)
        if evidence is None or evidence.published_at is None:
            return False
        entries = list(
            (
                await db.scalars(
                    select(AutomaticPickingFeedScan).where(col(AutomaticPickingFeedScan.feed_id) == feed.id)
                )
            ).all()
        )
        if len(entries) < feed.offset or not all(entry.work_admitted for entry in entries):
            return False
        if feed.transport_task_id is not None:
            codes = set(
                (
                    await db.scalars(
                        select(col(TransportMember.object_id)).where(
                            col(TransportMember.transport_task_id) == feed.transport_task_id
                        )
                    )
                ).all()
            )
            if not codes or not codes.issubset({entry.bin_code for entry in entries}):
                return False
        # 当前来源的投料完成标记仍保留；准入已交给 Work，不再读取原 Confirmation。
        return True

    async def inbound(self, db, feed):
        frozen_feed = await self.by_operation(db, feed.operation_id)
        if frozen_feed is None or (
            frozen_feed.workline_id,
            frozen_feed.picking_task_id,
            frozen_feed.source_member_id,
        ) != (feed.workline_id, feed.picking_task_id, feed.source_member_id):
            raise ValueError("feed result differs from frozen plan member")
        confirmation = await db.scalar(
            select(WmsConfirmation).where(
                col(WmsConfirmation.operation) == BIN_INBOUND_BATCH_OPERATION,
                col(WmsConfirmation.operation_id) == feed.operation_id,
            )
        )
        if confirmation is None:
            raise ValueError("current feed lost its frozen confirmation")
        if confirmation.request_digest != canonical_json_digest(confirmation.request_payload):
            raise ValueError("feed request digest mismatch")
        if confirmation.status == "RECONCILING":
            feed.phase = "RECONCILING"
            return None
        if confirmation.status != "COMPLETED":
            return None
        evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
        if evidence is None or evidence.apply_status != "APPLIED":
            raise ValueError("feed result has no applied evidence")
        intent, outcome = await self.reader.read_inbound(db, evidence, workline_id=feed.workline_id)
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id)
        task = await db.get(PickingTask, feed.picking_task_id)
        if (
            member is None
            or task is None
            or intent.operation_id != feed.operation_id
            or intent.task_id != task.task_id
            or member.picking_task_id != task.id
            or (intent.rack_id, intent.rack_face) != (member.rack_id, member.rack_face)
        ):
            raise ValueError("feed result differs from frozen plan member")
        return intent, outcome.result, evidence

    async def source_cancelled(self, db, feed):
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id)
        if member is None or member.picking_task_id != feed.picking_task_id:
            raise ValueError("current feed lost its plan member")
        return member.cancelled_evidence_id is not None

    async def chunk_state(self, db, feed):
        return await db.scalar(
            select(TransportTask).where(col(TransportTask.transport_task_id) == feed.transport_task_id)
        )

    async def chunk_entered(self, db, feed, bins):
        codes = {m.bin_code for m in bins}
        scans = set(
            (
                await db.scalars(
                    select(col(AutomaticPickingFeedScan.bin_code)).where(
                        col(AutomaticPickingFeedScan.feed_id) == feed.id,
                        col(AutomaticPickingFeedScan.bin_code).in_(codes),
                    )
                )
            ).all()
        )
        return scans == codes

    async def chunk_delivered(self, db, feed, bins):
        codes = {m.bin_code for m in bins}
        members = (
            await db.scalars(
                select(TransportMember).where(col(TransportMember.transport_task_id) == feed.transport_task_id)
            )
        ).all()
        return {m.object_id for m in members} == codes and all(
            m.status == "SUCCEEDED" and not m.position_unknown and m.final_position_json == m.target_json
            for m in members
        )

    async def channel_busy(self, db, line_id):
        return (
            await db.scalar(
                select(col(TransportTask.id))
                .where(
                    col(TransportTask.authority_workline_id) == line_id,
                    col(TransportTask.kind).in_(("BIN_MOVE", "BIN_EXCHANGE")),
                    or_(
                        col(TransportTask.status).notin_(("SUCCEEDED", "FAILED", "REJECTED")),
                        col(TransportTask.outcome_version) > col(TransportTask.published_outcome_version),
                    ),
                )
                .limit(1)
            )
            is not None
        )

    async def entry(self, db, feed_id, bin_code):
        return await db.scalar(
            select(AutomaticPickingFeedScan)
            .where(col(AutomaticPickingFeedScan.feed_id) == feed_id, col(AutomaticPickingFeedScan.bin_code) == bin_code)
            .with_for_update()
        )

    async def record_entry(self, db, feed, bin_code, evidence_id):
        entry = await self.entry(db, feed.id, bin_code)
        if entry is None:
            entry = AutomaticPickingFeedScan(feed_id=feed.id, bin_code=bin_code, evidence_id=evidence_id)
            db.add(entry)
            await db.flush()
        return entry

    async def scan_candidates(self, db, line_id, task, bin_code):
        candidates = []
        for feed in await self.list_admission_pending(db, line_id):
            if feed.picking_task_id != task.id or feed.response_evidence_id is None:
                continue
            entered = await self.entry(db, feed.id, bin_code)
            if entered is not None and entered.work_admitted:
                continue
            snapshot = await self.inbound(db, feed)
            if snapshot is None:
                continue
            ready = snapshot[1]
            bins = getattr(ready, "bins", ())
            if not any(m.bin_code == bin_code for m in bins):
                continue
            member = await db.get(PickingTaskBinSourceRack, feed.source_member_id)
            delivered = await self.bin_delivered(db, feed, bin_code)
            if (
                member.cancelled_evidence_id is None
                or entered is not None
                or delivered
                or await self.delivery_pending(db, feed, bin_code)
            ):
                candidates.append(feed)
        return candidates

    async def bin_delivered(self, db, feed, bin_code):
        if feed.transport_task_id is None:
            return False
        transport = await self.chunk_state(db, feed)
        physical = await db.scalar(
            select(TransportMember).where(
                col(TransportMember.transport_task_id) == feed.transport_task_id,
                col(TransportMember.object_type) == "BIN",
                col(TransportMember.object_id) == bin_code,
            )
        )
        return bool(
            transport is not None
            and transport.outcome_version > 0
            and transport.outcome_version == transport.published_outcome_version
            and physical is not None
            and physical.status == "SUCCEEDED"
            and not physical.position_unknown
            and physical.final_position_json == physical.target_json
        )

    async def delivery_pending(self, db, feed, bin_code):
        if feed.transport_task_id is None:
            return False
        transport = await self.chunk_state(db, feed)
        member = await db.scalar(
            select(TransportMember).where(
                col(TransportMember.transport_task_id) == feed.transport_task_id,
                col(TransportMember.object_type) == "BIN",
                col(TransportMember.object_id) == bin_code,
            )
        )
        return (
            member is not None
            and transport is not None
            and (
                transport.status not in {"SUCCEEDED", "FAILED", "REJECTED"}
                or transport.outcome_version != transport.published_outcome_version
            )
        )

    async def admission_ready(self, db, feed, bin_code):
        snapshot = await self.inbound(db, feed)
        if snapshot is None or snapshot[2].published_at is None:
            return False
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id)
        return (
            member.cancelled_evidence_id is None
            or await self.entry(db, feed.id, bin_code) is not None
            or await self.bin_delivered(db, feed, bin_code)
        )
