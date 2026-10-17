"""当前分配、成员领取与原可靠对象引用；调用方先持有 WorkLine 锁。"""

import json

from sqlalchemy import or_, select, text

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

from .return_allocation_model import AutomaticPickingReturnAllocation
from .return_repository import ReturnRepository


class ReturnAllocationRepository:
    def __init__(self, returns=None, reader=None):
        self.returns = returns or ReturnRepository()
        self.reader = reader or BinBatchResultReader()

    async def current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnAllocation)
                    .where(
                        AutomaticPickingReturnAllocation.workline_id == line_id,
                        AutomaticPickingReturnAllocation.archived_at.is_(None),
                    )
                    .order_by(AutomaticPickingReturnAllocation.id)
                    .with_for_update()
                )
            ).all()
        )

    async def by_operation(self, db, operation_id):
        return await db.scalar(
            select(AutomaticPickingReturnAllocation)
            .where(AutomaticPickingReturnAllocation.operation_id == operation_id)
            .with_for_update()
        )

    async def evidence(self, db, evidence_id):
        return await db.get(InboundEvidence, evidence_id)

    async def qualified(self, db, current):
        evidence = await self.evidence(db, current.ready_evidence_id) if current.ready_evidence_id else None
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == current.scan4_command_code))
        return (
            evidence is not None
            and evidence.apply_status == "APPLIED"
            and evidence.published_at is not None
            and evidence.kind == "DEVICE_RESULT"
            and evidence.workline_id == current.workline_id
            and command is not None
            and command.status == "SUCCEEDED"
            and command.result_evidence_id == evidence.id
            and command.command_code == evidence.command_code
            and command.workline_id == current.workline_id
            and normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[
                1
            ]
            == evidence.payload_digest
        )

    async def add(self, db, *, line, face, operation_id, rows, completed, now):
        allocation = AutomaticPickingReturnAllocation(
            workline_id=line.id,
            arrival_evidence_id=face.arrival_evidence_id,
            rack_id=face.rack_id,
            rack_face=face.rack_face,
            operation_id=operation_id,
            return_ids=[row.id for row in rows],
        )
        db.add(allocation)
        await db.flush()
        for row in rows:
            row.allocation_id = allocation.id
        for previous in completed:
            previous.archived_at = now
        await db.flush()
        return allocation

    async def confirmation(self, db, allocation):
        return await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.bin.return_batch@v1",
                WmsConfirmation.operation_id == allocation.operation_id,
                WmsConfirmation.workline_id == allocation.workline_id,
            )
        )

    async def apply_decision(self, db, allocation, evidence_id, selected_count, *, now):
        rows = [await self.returns.by_id_for_update(db, return_id) for return_id in allocation.return_ids]
        for current in rows:
            if (
                current is None
                or current.workline_id != allocation.workline_id
                or current.allocation_id != allocation.id
                or current.archived_at is not None
            ):
                raise ValueError("return decision lost its frozen execution claim")
        for current in rows[selected_count:]:
            current.allocation_id = None
        allocation.response_evidence_id, allocation.selected_count = evidence_id, selected_count
        if selected_count == 0:
            allocation.settled_at = now
        await db.flush()

    async def transport(self, db, allocation):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == allocation.transport_task_id)
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
        if (
            task is None
            or task.kind != "BIN_MOVE"
            or task.authority_workline_id != allocation.workline_id
            or binding is None
            or binding.workline_id != allocation.workline_id
            or binding.picking_task_id is not None
            or binding.source_evidence_id != allocation.response_evidence_id
            or binding.resource_fence_id != allocation.operation_id
            or binding.step != "AUTOMATIC_PICKING_RETURN"
        ):
            raise ValueError("return allocation differs from original BIN_MOVE owner")
        members = list(
            (
                await db.scalars(
                    select(TransportMember)
                    .where(TransportMember.transport_task_id == task.transport_task_id)
                    .order_by(TransportMember.ordinal)
                )
            ).all()
        )
        if len(members) != allocation.selected_count or any(m.object_type != "BIN" for m in members):
            raise ValueError("return Transport members differ from frozen allocation")
        return task, members

    async def target_conflict(self, db, moves):
        # 与既有退箱创建路径共享事务锁身份；只检查活跃冻结目标，不建立储位主账。
        targets = sorted((m.target.rack_id, m.target.rack_face, m.target.slot_id, m.bin_code) for m in moves)
        for rack, face, slot, _ in targets:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": "return-target:" + json.dumps((rack, face, slot))},
            )
        for rack, face, slot, bin_code in targets:
            conflict = await db.scalar(
                select(TransportMember.id)
                .join(TransportTask, TransportTask.transport_task_id == TransportMember.transport_task_id)
                .where(
                    or_(
                        TransportTask.status.notin_(("SUCCEEDED", "FAILED", "REJECTED")),
                        TransportTask.outcome_version > TransportTask.published_outcome_version,
                        TransportMember.position_unknown.is_(True),
                    ),
                    TransportMember.object_type == "BIN",
                    TransportMember.object_id != bin_code,
                    TransportMember.target_json["kind"].as_string() == "RACK_BIN_SLOT",
                    TransportMember.target_json["rack_id"].as_string() == rack,
                    TransportMember.target_json["rack_face"].as_string() == face,
                    TransportMember.target_json["slot_id"].as_string() == slot,
                )
                .limit(1)
            )
            if conflict is not None:
                return True
        return False

    async def bind_transport(self, db, allocation, transport_task_id):
        allocation.transport_task_id = transport_task_id
        await db.flush()

    async def handoff(self, db, allocation, members, *, now):
        rows = [
            await self.returns.by_id_for_update(db, rid) for rid in allocation.return_ids[: allocation.selected_count]
        ]
        if any(
            row is None
            or row.workline_id != allocation.workline_id
            or row.allocation_id != allocation.id
            or row.archived_at is not None
            or row.bin_code != member.object_id
            for row, member in zip(rows, members, strict=True)
        ):
            raise ValueError("return handoff lost original member identity")
        for row in rows:
            row.allocation_id, row.archived_at = None, now
        allocation.handed_off_at = now
        await db.flush()

    async def mark_settled(self, db, allocation, *, now):
        allocation.settled_at = now
        await db.flush()

    async def by_transport(self, db, transport_id):
        return await db.scalar(
            select(AutomaticPickingReturnAllocation)
            .where(AutomaticPickingReturnAllocation.transport_task_id == transport_id)
            .with_for_update()
        )

    async def retire_completed(self, db, allocations, faces, *, now):
        current = {(f.arrival_evidence_id, f.rack_id, f.rack_face) for f in faces}
        for allocation in allocations:
            if (
                allocation.settled_at is not None
                and (allocation.arrival_evidence_id, allocation.rack_id, allocation.rack_face) not in current
            ):
                allocation.archived_at = now
        await db.flush()
