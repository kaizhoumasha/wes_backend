"""调用方持有 WorkLine authority 锁，阶段与原回架请求在同一事务关联。"""

from sqlalchemy import and_, or_, select
from wes_plugin_sdk import BinInboundBatchRackFaceDone, BinInboundBatchReady

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.repositories import transport_decision_binding_repository
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
from src.utils.canonical_json import canonical_json_digest

from .feed_model import AutomaticPickingFeed
from .feed_repository import FeedRepository
from .return_allocation_model import AutomaticPickingReturnAllocation
from .return_motion_model import AutomaticPickingReturnMotion


class ReturnMotionRepository:
    async def current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnMotion)
                    .where(
                        AutomaticPickingReturnMotion.workline_id == line_id,
                        AutomaticPickingReturnMotion.archived_at.is_(None),
                    )
                    .order_by(AutomaticPickingReturnMotion.id)
                    .with_for_update()
                )
            ).all()
        )

    async def add(self, db, intent):
        face = intent.face
        row = AutomaticPickingReturnMotion(
            workline_id=face.workline_id,
            arrival_evidence_id=face.arrival_evidence_id,
            source_evidence_id=intent.source_evidence_id,
            rack_id=face.rack_id,
            rack_face=face.rack_face,
        )
        db.add(row)
        await db.flush()
        return row

    async def source_binding(self, db, line, intent):
        await transport_decision_binding_repository.lock_object_authority(
            db, object_type="RACK", object_id=intent.face.rack_id
        )
        feed = await db.get(AutomaticPickingFeed, intent.source_feed_id)
        proof = await self.source_round(db, line, intent, feed)
        if proof is None:
            return None
        root, ingress, members, started_at = proof
        member = await db.get(PickingTaskBinSourceRack, feed.source_member_id) if feed else None
        if (
            feed is None
            or member is None
            or feed.workline_id != line.id
            or feed.picking_task_id != root.picking_task_id
            or member.picking_task_id != feed.picking_task_id
            or (member.rack_id, member.rack_face) != (intent.face.rack_id, intent.face.rack_face)
        ):
            raise ValueError("motion source Feed differs from original ingress or member")
        if feed.phase != "FEED_COMPLETE" or feed.archived_at is not None:
            return None
        snapshot = await FeedRepository().inbound(db, feed)
        if snapshot is None:
            return None
        _request, result, response = snapshot
        confirmation = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == response.operation,
                WmsConfirmation.operation_id == feed.operation_id,
            )
        )
        arrival_fact = await db.get(InboundEvidence, intent.face.arrival_evidence_id)
        if ingress is not None:
            arrival_fact = await self.original_arrival_fact(db, root, ingress, members[0], arrival_fact)
        if confirmation.created_at < max(started_at, arrival_fact.received_at):
            raise ValueError("motion source Feed predates original round arrival")
        if response.published_at is None:
            return None
        end = await db.get(InboundEvidence, intent.service_end_evidence_id)
        if (
            end is None
            or end.workline_id != line.id
            or end.picking_task_id != feed.picking_task_id
            or end.apply_status != "APPLIED"
            or end.published_at is None
            or canonical_json_digest(end.normalized_payload) != end.payload_digest
        ):
            return None
        if type(result) is BinInboundBatchRackFaceDone:
            if end.id != response.id or feed.transport_task_id is not None or feed.offset != 0:
                raise ValueError("motion empty source end differs from original Feed")
        elif type(result) is BinInboundBatchReady:
            state = await FeedRepository().chunk_state(db, feed)
            binding = (
                await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.client_request_id == state.client_request_id
                    )
                )
                if state
                else None
            )
            delivered = list(
                (
                    await db.scalars(
                        select(TransportMember).where(TransportMember.transport_task_id == feed.transport_task_id)
                    )
                ).all()
            )
            tail = result.bins[((len(result.bins) - 1) // 4) * 4 :]
            expected_sources = {
                b.bin_code: {
                    "kind": "RACK_BIN_SLOT",
                    "rack_id": b.source_locator.rack_id,
                    "rack_face": b.source_locator.rack_face,
                    "slot_id": b.source_locator.slot_id,
                }
                for b in tail
            }
            if (
                state is None
                or any(m.source_json != expected_sources.get(m.object_id) for m in delivered)
                or state.kind != "BIN_MOVE"
                or state.authority_workline_id != line.id
                or any(
                    m.target_json
                    != {"kind": "HANDOFF_POSITION", "location_code": line.position_bindings["INLET"]["location_id"]}
                    for m in delivered
                )
                or binding is None
                or state.status != "SUCCEEDED"
                or state.outcome_version != state.published_outcome_version
                or feed.offset != len(result.bins)
                or binding.workline_id != line.id
                or binding.picking_task_id != feed.picking_task_id
                or binding.step != "AUTOMATIC_PICKING_FEED"
                or binding.source_evidence_id != response.id
                or binding.resource_fence_id != feed.operation_id
                or end.kind != "TRANSPORT_RESULT"
                or end.transport_task_id != state.transport_task_id
                or end.normalized_payload.get("client_request_id") != state.client_request_id
                or end.normalized_payload.get("outcome_version") != state.outcome_version
                or end.normalized_payload.get("status") != "SUCCEEDED"
                or not await FeedRepository().chunk_delivered(
                    db, feed, result.bins[((len(result.bins) - 1) // 4) * 4 :]
                )
            ):
                return None
        else:
            raise ValueError("unsupported motion source end")
        return root

    async def source_round(self, db, line, intent, feed):
        location = line.position_bindings["FIVE_RACK"]["location_id"]
        arrival = await self.current_arrival(
            db,
            line.id,
            intent.face.arrival_evidence_id,
            intent.face.rack_id,
            intent.face.rack_face,
            {"kind": "RACK_POSITION", "location_code": location},
        )
        ingress, members = None, []
        if feed is not None and feed.inbound_client_request_id is not None:
            # 下一面 Feed 已接管原轮次；已闭合进场和上一阶段不再参与恢复。
            root = feed
            if arrival is None:
                return None
            action, binding = arrival
            if (
                feed.inbound_client_request_id != intent.inbound_client_request_id
                or feed.source_arrival_evidence_id != intent.face.arrival_evidence_id
                or action.kind != "RACK_ROTATE"
                or binding.step != "AUTOMATIC_PICKING_SOURCE_RACK_ROTATE"
                or binding.picking_task_id != feed.picking_task_id
                or feed.round_causal_token is None
                or binding.causal_token < feed.round_causal_token
            ):
                raise ValueError("motion source Feed lost transferred round identity")
            started_at = feed.round_started_at
        else:
            root = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == intent.inbound_client_request_id
                )
            )
            if root is None:
                return None
            if (
                root.workline_id != line.id
                or root.resource_fence_id != intent.face.rack_id
                or root.step != "PICKING_TASK_BIN_SOURCE_RACK_IN"
                or root.window_target_location_code != location
                or root.causal_token <= 0
            ):
                raise ValueError("motion original ingress binding mismatch")
            ingress = await db.scalar(
                select(TransportTask).where(TransportTask.client_request_id == root.client_request_id)
            )
            members = (
                list(
                    (
                        await db.scalars(
                            select(TransportMember).where(
                                TransportMember.transport_task_id == ingress.transport_task_id
                            )
                        )
                    ).all()
                )
                if ingress
                else []
            )
            if (
                ingress is None
                or ingress.kind != "RACK_MOVE"
                or ingress.authority_workline_id != line.id
                or len(members) != 1
                or members[0].object_type != "RACK"
                or members[0].object_id != intent.face.rack_id
                or members[0].target_json != {"kind": "RACK_POSITION", "location_code": location}
            ):
                raise ValueError("motion original ingress lost its exact RACK execution")
            if arrival is None:
                return None
            if not await self.same_round(db, root, arrival[1]):
                raise ValueError("motion arrival differs from original round")
            started_at = root.created_at
        return root, ingress, members, started_at

    async def original_arrival_fact(self, db, root, ingress, member, arrival_fact):
        original_results = await db.scalars(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == ingress.transport_task_id,
                InboundEvidence.kind == "TRANSPORT_RESULT",
                InboundEvidence.apply_status == "APPLIED",
                InboundEvidence.published_at.is_not(None),
            )
        )
        for fact in original_results.all():
            if (
                ingress.status in {"SUCCEEDED", "FAILED"}
                and ingress.outcome_version > 0
                and ingress.outcome_version == ingress.published_outcome_version
                and not member.position_unknown
                and member.final_position_json is not None
                and fact.workline_id == root.workline_id
                and fact.picking_task_id == root.picking_task_id
                and fact.normalized_payload.get("client_request_id") == root.client_request_id
                and fact.normalized_payload.get("status") == ingress.status
                and fact.normalized_payload.get("outcome_version") == ingress.outcome_version
                and canonical_json_digest(fact.normalized_payload) == fact.payload_digest
            ):
                return fact
        return arrival_fact

    async def bind_source(self, db, current, intent, root):
        transferred = isinstance(root, AutomaticPickingFeed)
        current.inbound_client_request_id = root.inbound_client_request_id if transferred else root.client_request_id
        current.round_started_at = root.round_started_at if transferred else root.created_at
        current.round_causal_token = root.round_causal_token if transferred else root.causal_token
        current.source_feed_id = intent.source_feed_id
        current.service_end_evidence_id = intent.service_end_evidence_id
        await db.flush()

    async def current_arrival(self, db, line_id, arrival_evidence_id, rack_id, rack_face, expected):
        await transport_decision_binding_repository.lock_object_authority(db, object_type="RACK", object_id=rack_id)
        evidence = await db.get(InboundEvidence, arrival_evidence_id)
        task = (
            await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == evidence.transport_task_id))
            if evidence
            else None
        )
        binding = (
            await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            if task
            else None
        )
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == rack_id
            )
        )
        if (
            task is None
            or binding is None
            or position is None
            or binding.workline_id != line_id
            or binding.resource_fence_id != rack_id
            or evidence.workline_id != line_id
            or evidence.picking_task_id != binding.picking_task_id
            or evidence.kind != "TRANSPORT_RESULT"
            or evidence.apply_status != "APPLIED"
            or evidence.published_at is None
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            or evidence.normalized_payload.get("client_request_id") != task.client_request_id
            or evidence.normalized_payload.get("outcome_version") != task.outcome_version
            or evidence.normalized_payload.get("status") != "SUCCEEDED"
            or task.kind not in {"RACK_MOVE", "RACK_ROTATE"}
            or task.authority_workline_id != line_id
            or task.status != "SUCCEEDED"
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
            or position.workline_id != line_id
            or position.position_unknown
            or position.position_json != expected
            or position.arrival_face != rack_face
            or position.source_transport_task_id != task.transport_task_id
            or position.source_causal_token != binding.causal_token
        ):
            return None
        arrival = list(
            (
                await db.scalars(
                    select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
                )
            ).all()
        )
        if (
            len(arrival) != 1
            or arrival[0].object_type != "RACK"
            or arrival[0].object_id != rack_id
            or arrival[0].status != "SUCCEEDED"
            or arrival[0].position_unknown
            or arrival[0].target_json != expected
            or arrival[0].final_position_json != expected
            or arrival[0].arrival_face != rack_face
        ):
            return None
        return task, binding

    async def same_round(self, db, root, arrival):
        if arrival.causal_token < root.causal_token:
            return False
        if arrival.window_target_location_code is not None:
            return arrival.client_request_id == root.client_request_id
        if root.window_released_at is None:
            return True
        # 已释放窗口的换面沿冻结的直接来源接续，不能以历史缺失推断轮次。
        previous = await db.scalar(
            select(AutomaticPickingReturnMotion.id).where(
                AutomaticPickingReturnMotion.workline_id == root.workline_id,
                AutomaticPickingReturnMotion.rack_id == root.resource_fence_id,
                AutomaticPickingReturnMotion.inbound_client_request_id == root.client_request_id,
                AutomaticPickingReturnMotion.round_causal_token == root.causal_token,
                AutomaticPickingReturnMotion.service_end_evidence_id == arrival.source_evidence_id,
            )
        )
        return previous is not None

    async def current_source(self, db, line, current, *, arrival_evidence_id=None, rack_face=None):
        if current.inbound_client_request_id is None:
            return False
        arrival = await self.current_arrival(
            db,
            line.id,
            current.arrival_evidence_id if arrival_evidence_id is None else arrival_evidence_id,
            current.rack_id,
            current.rack_face if rack_face is None else rack_face,
            {"kind": "RACK_POSITION", "location_code": line.position_bindings["FIVE_RACK"]["location_id"]},
        )
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        if arrival is None or end is None:
            return False
        _task, binding = arrival
        if (
            binding.picking_task_id != end.picking_task_id
            or binding.causal_token < current.round_causal_token
            or (
                binding.window_target_location_code is not None
                and binding.client_request_id != current.inbound_client_request_id
            )
        ):
            return False
        # 本轮当前依赖含尚未 ACK 的动作；更早轮次只保留原履约，不作为本轮围栏。
        rows = (
            await db.execute(
                select(TransportTask, TransportDecisionBinding, TransportMember)
                .join(TransportMember, TransportMember.transport_task_id == TransportTask.transport_task_id)
                .outerjoin(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportMember.object_type == "RACK",
                    TransportMember.object_id == current.rack_id,
                    TransportTask.created_at >= current.round_started_at,
                )
            )
        ).all()
        for action, owner, member in rows:
            if (
                owner is None
                or action.status not in {"SUCCEEDED", "FAILED", "REJECTED"}
                or action.outcome_version != action.published_outcome_version
                or (action.status != "REJECTED" and member.position_unknown)
            ):
                return False
            if (
                owner.window_target_location_code is not None
                and owner.client_request_id != current.inbound_client_request_id
                and owner.window_released_at is None
                and owner.causal_token >= current.round_causal_token
            ):
                return False
        windows = list(
            (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.resource_fence_id == current.rack_id,
                        TransportDecisionBinding.window_target_location_code.is_not(None),
                        TransportDecisionBinding.window_released_at.is_(None),
                        TransportDecisionBinding.client_request_id != current.inbound_client_request_id,
                        TransportDecisionBinding.causal_token >= current.round_causal_token,
                    )
                )
            ).all()
        )
        if windows:
            return False
        return await self.bin_dependencies_closed(db, current)

    async def bin_dependencies_closed(self, db, current):
        bins = (
            await db.execute(
                select(TransportTask, TransportMember)
                .join(TransportMember, TransportMember.transport_task_id == TransportTask.transport_task_id)
                .where(
                    TransportMember.object_type == "BIN",
                    TransportTask.created_at >= current.round_started_at,
                    or_(
                        and_(
                            TransportMember.source_json["kind"].as_string() == "RACK_BIN_SLOT",
                            TransportMember.source_json["rack_id"].as_string() == current.rack_id,
                        ),
                        and_(
                            TransportMember.target_json["kind"].as_string() == "RACK_BIN_SLOT",
                            TransportMember.target_json["rack_id"].as_string() == current.rack_id,
                        ),
                    ),
                )
            )
        ).all()
        for action, member in bins:
            if action.outcome_version < 1 or action.outcome_version != action.published_outcome_version:
                return False
            if action.status == "REJECTED":
                continue
            if (
                action.status != "SUCCEEDED"
                or member.status != "SUCCEEDED"
                or member.position_unknown
                or member.final_position_json != member.target_json
            ):
                return False
            results = list(
                (
                    await db.scalars(
                        select(InboundEvidence).where(
                            InboundEvidence.transport_task_id == action.transport_task_id,
                            InboundEvidence.kind == "TRANSPORT_RESULT",
                            InboundEvidence.apply_status == "APPLIED",
                            InboundEvidence.published_at.is_not(None),
                        )
                    )
                ).all()
            )
            if not any(
                e.workline_id == action.authority_workline_id
                and e.normalized_payload.get("status") == action.status
                and e.normalized_payload.get("client_request_id") == action.client_request_id
                and e.normalized_payload.get("outcome_version") == action.outcome_version
                and canonical_json_digest(e.normalized_payload) == e.payload_digest
                for e in results
            ):
                return False
        return True

    async def allocation(self, db, current):
        row = await db.get(AutomaticPickingReturnAllocation, current.allocation_id)
        if row is None or (row.workline_id, row.arrival_evidence_id, row.rack_id, row.rack_face) != (
            current.workline_id,
            current.arrival_evidence_id,
            current.rack_id,
            current.rack_face,
        ):
            raise ValueError("motion phase lost original allocation identity")
        return row

    async def bind(self, db, current, allocation):
        current.allocation_id = allocation.id
        await db.flush()

    async def close(self, db, current, *, now):
        current.closed_at = now
        await db.flush()

    async def taken_over(self, db, line, current):
        await transport_decision_binding_repository.lock_object_authority(
            db, object_type="RACK", object_id=current.rack_id
        )
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == current.rack_id
            )
        )
        if (
            position is None
            or position.position_unknown
            or position.workline_id == line.id
            or position.source_causal_token is None
        ):
            return False
        old = await db.get(InboundEvidence, current.arrival_evidence_id)
        old_token = (
            await db.scalar(
                select(TransportDecisionBinding.causal_token)
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(TransportTask.transport_task_id == old.transport_task_id)
            )
            if old is not None
            else None
        )
        if old_token is None or position.source_causal_token <= old_token:
            return False
        evidence = await db.scalars(
            select(InboundEvidence).where(
                InboundEvidence.transport_task_id == position.source_transport_task_id,
                InboundEvidence.kind == "TRANSPORT_RESULT",
                InboundEvidence.apply_status == "APPLIED",
                InboundEvidence.published_at.is_not(None),
            )
        )
        for fact in evidence.all():
            if (
                await self.current_arrival(
                    db, position.workline_id, fact.id, current.rack_id, position.arrival_face, position.position_json
                )
                is not None
            ):
                return True
        return False

    async def retire(self, db, phases, faces, *, line, now):
        known = {(f.arrival_evidence_id, f.rack_id, f.rack_face) for f in faces}
        for current in phases:
            if (current.arrival_evidence_id, current.rack_id, current.rack_face) in known:
                continue
            if current.rotation_transport_task_id is not None:
                continue
            if current.closed_at is None and current.allocation_id is not None:
                continue
            if await self.taken_over(db, line, current):
                current.closed_at = current.closed_at or now
                if current.inbound_client_request_id is None:
                    current.archived_at = now
                continue
            for face in faces:
                if (
                    face.rack_id == current.rack_id
                    and face.arrival_evidence_id != current.arrival_evidence_id
                    and await self.current_arrival(
                        db,
                        line.id,
                        face.arrival_evidence_id,
                        face.rack_id,
                        face.rack_face,
                        {"kind": "RACK_POSITION", "location_code": line.position_bindings["FIVE_RACK"]["location_id"]},
                    )
                    is not None
                ):
                    current.archived_at = now
                    break
        await db.flush()
