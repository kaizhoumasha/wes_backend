"""8E/8F 共用的权威离线来源结束与 RACK 到位夹具；不调用设备。"""

from datetime import timedelta
from types import SimpleNamespace as N

from automatic_picking.application.feed_model import AutomaticPickingFeed
from sqlalchemy import select
from wes_plugin_sdk import wms_operations

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchScheduler, BinInboundBatchOwnerService
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest


async def rack_action(db, p, rack, face, *, root=None, status="SUCCEEDED", kind="RACK_MOVE", line_id=None):
    line_id = line_id or p.line.id
    target = {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"}
    client = new_uuid7()
    task = TransportTask(
        transport_task_id=new_uuid7(),
        client_request_id=client,
        kind=kind,
        caller_json={"workline_id": str(line_id)},
        request_json={"fixture": "offline rack"},
        request_digest="a" * 64,
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="a" * 64,
        status=status,
        authority_workline_id=line_id,
        outcome_version=1 if status == "SUCCEEDED" else 0,
        published_outcome_version=1 if status == "SUCCEEDED" else 0,
        created_at=p.now - timedelta(seconds=20) if root is None else p.now,
        updated_at=p.now,
    )
    binding = TransportDecisionBinding(
        workline_id=line_id,
        picking_task_id=p.task.id,
        correlation_id="offline:" + client,
        resource_fence_id=rack,
        client_request_id=client,
        step="PICKING_TASK_BIN_SOURCE_RACK_IN" if root is None else "OFFLINE_ROTATION",
        source_evidence_id=p.task.issued_evidence_id,
        window_target_location_code="OFFLINE-FIVE" if root is None else None,
        created_at=task.created_at,
    )
    db.add_all([task, binding])
    await db.flush()
    db.add(
        TransportMember(
            transport_task_id=task.transport_task_id,
            ordinal=1,
            object_type="RACK",
            object_id=rack,
            source_json={"kind": "RACK", "rack_id": rack},
            target_json=target,
            status=status,
            final_position_json=target if status == "SUCCEEDED" else None,
            position_unknown=status != "SUCCEEDED",
            arrival_face=face if status == "SUCCEEDED" else None,
            updated_at=p.now,
        )
    )
    await db.flush()
    return task, binding


async def source_facts(p, face, *, root_client=None, revision=1, reuse_arrival=False):
    eid, rack, side = face
    async with p.sessions.begin() as db:
        line = await db.get(type(p.line), p.line.id)
        line.position_bindings = {
            **line.position_bindings,
            "FIVE_RACK": {"location_type": "RACK_POSITION", "location_id": "OFFLINE-FIVE"},
        }
        line.position_bindings = {
            **line.position_bindings,
            "INLET": {"location_type": "HANDOFF_POSITION", "location_id": "OFFLINE-INLET"},
        }
        p.line.position_bindings = line.position_bindings
        if reuse_arrival:
            root = await db.scalar(
                select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == root_client)
            )
            fact = await db.get(InboundEvidence, eid)
            task = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == fact.transport_task_id)
            )
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
        elif root_client is None:
            task, root = await rack_action(db, p, rack, side)
            binding = root
        else:
            root = await db.scalar(
                select(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == root_client)
            )
            task, binding = await rack_action(db, p, rack, side, root=root, kind="RACK_ROTATE")
        if not reuse_arrival:
            evidence = await db.get(InboundEvidence, eid)
            payload = {
                "transport_task_id": task.transport_task_id,
                "client_request_id": task.client_request_id,
                "kind": task.kind,
                "status": "SUCCEEDED",
                "outcome_version": 1,
            }
            evidence.kind = "TRANSPORT_RESULT"
            evidence.device_code = None
            evidence.source_identity = f"transport:{task.transport_task_id}:outcome:1"
            evidence.transport_task_id = task.transport_task_id
            evidence.picking_task_id = p.task.id
            evidence.normalized_payload = payload
            evidence.payload_digest = canonical_json_digest(payload)
            position = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK", PositionProjection.object_id == rack
                )
            )
            values = {
                "workline_id": p.line.id,
                "position_json": {"kind": "RACK_POSITION", "location_code": "OFFLINE-FIVE"},
                "position_unknown": False,
                "arrival_face": side,
                "source_operation_id": new_uuid7(),
                "source_transport_task_id": task.transport_task_id,
                "source_causal_token": binding.causal_token,
            }
            if position is None:
                db.add(PositionProjection(object_type="RACK", object_id=rack, **values))
            else:
                for k, v in values.items():
                    setattr(position, k, v)
        member = PickingTaskBinSourceRack(
            picking_task_id=p.task.id,
            rack_id=rack,
            rack_face=side,
            plan_revision=revision,
            source_evidence_id=p.task.issued_evidence_id,
        )
        # 同架面多次到位在夹具中也保留不同 revision 的成员身份。
        existing = list(
            (
                await db.scalars(
                    select(PickingTaskBinSourceRack).where(
                        PickingTaskBinSourceRack.picking_task_id == p.task.id,
                        PickingTaskBinSourceRack.rack_id == rack,
                        PickingTaskBinSourceRack.rack_face == side,
                    )
                )
            ).all()
        )
        member.plan_revision = max([m.plan_revision for m in existing] + [revision - 1]) + 1
        db.add(member)
        await db.flush()
        op = new_uuid7()
        feed = AutomaticPickingFeed(
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            source_member_id=member.id,
            operation_id=op,
            phase="FEED_COMPLETE",
        )
        db.add(feed)
        await db.flush()
        intent = wms_operations.outbound_bin_inbound_batch(
            operation_id=op, task_id=p.task.task_id, plan_revision=member.plan_revision, rack_id=rack, rack_face=side
        )
        await BinBatchScheduler(
            WmsConfirmationLifecycleService(workline_owner=BinInboundBatchOwnerService())
        ).create_in_session(db, intent, workline_id=p.line.id, created_at=p.now)
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == op))
        payload = {"operation_id": op, "code": "DECIDED", "timestamp": 1, "data": {"result": "RACK_FACE_DONE"}}
        end = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=request.operation + ":" + op,
            workline_id=p.line.id,
            picking_task_id=p.task.id,
            operation=request.operation,
            operation_id=op,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=p.now,
            published_at=p.now,
            decision_digest="c" * 64,
        )
        db.add(end)
        await db.flush()
        request.status = "COMPLETED"
        request.response_evidence_id = end.id
        request.completed_at = p.now
        feed.response_evidence_id = end.id
        context = N(
            root=root.client_request_id,
            feed_id=feed.id,
            end_id=end.id,
            member_id=member.id,
            transport_id=task.transport_task_id,
            binding_id=binding.id,
            root_id=root.id,
            rack_id=rack,
        )
    if not hasattr(p, "motion_sources"):
        p.motion_sources = {}
    p.motion_sources[eid] = context
    return context
