"""计划准入所需的精确读取；任务锁后不反向锁 confirmation。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from itertools import batched
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceConflict,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
)
from src.app.transport.models import TransportTask
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import (
    DirectPickExecution,
    DirectPickFaceCompletion,
    PickingTask,
    PickingTaskBinSourceRack,
    PickingTaskStatus,
)
from src.app.workline.models import WorkLine

MEMBER_BATCH_SIZE = 250


class PickingTaskPlanDeltaRepository:
    async def source_transport_matches(
        self, db: AsyncSession, workline_id: int, rack_id: str, source_evidence_id: int, transport_task_id: str
    ) -> bool:
        bindings = TransportDecisionBinding.__table__.c
        transports = TransportTask.__table__.c
        return (
            await db.scalar(
                select(bindings.id)
                .join(TransportTask, transports.client_request_id == bindings.client_request_id)
                .where(
                    bindings.workline_id == workline_id,
                    bindings.resource_fence_id == rack_id,
                    bindings.source_evidence_id == source_evidence_id,
                    bindings.step.in_(("PICKING_TASK_BIN_SOURCE_RACK_IN", "MANUAL_PICKING_SOURCE_RACK_ROTATE")),
                    transports.transport_task_id == transport_task_id,
                    transports.status == "SUCCEEDED",
                )
                .limit(1)
            )
            is not None
        )

    async def first_completed_source_owner_at_position(
        self, db: AsyncSession, workline_id: int, location_code: str
    ) -> PickingTask | None:
        members = PickingTaskBinSourceRack.__table__.c
        tasks = PickingTask.__table__.c
        bindings = TransportDecisionBinding.__table__.c
        transports = TransportTask.__table__.c
        projections = PositionProjection.__table__.c
        return await db.scalar(
            select(PickingTask)
            .join(PickingTaskBinSourceRack, members.picking_task_id == tasks.id)
            .join(
                TransportDecisionBinding,
                (bindings.source_evidence_id == members.source_evidence_id)
                & (bindings.resource_fence_id == members.rack_id)
                & (bindings.workline_id == tasks.workline_id),
            )
            .join(TransportTask, transports.client_request_id == bindings.client_request_id)
            .join(
                PositionProjection,
                (projections.object_type == "RACK")
                & (projections.object_id == members.rack_id)
                & (projections.workline_id == tasks.workline_id)
                & (projections.source_transport_task_id == transports.transport_task_id),
            )
            .where(
                tasks.workline_id == workline_id,
                tasks.status == PickingTaskStatus.EXECUTION_COMPLETED,
                members.rack_face == projections.arrival_face,
                members.plan_revision <= tasks.last_applied_plan_revision,
                bindings.step.in_(("PICKING_TASK_BIN_SOURCE_RACK_IN", "MANUAL_PICKING_SOURCE_RACK_ROTATE")),
                transports.status == "SUCCEEDED",
                projections.position_unknown.is_(False),
                projections.position_json["kind"].as_string() == "RACK_POSITION",
                projections.position_json["location_code"].as_string() == location_code,
            )
            .order_by(projections.updated_at.desc(), tasks.id, members.id)
            .limit(1)
        )

    async def first_completed_transfer_owner_at_position(
        self, db: AsyncSession, workline_id: int, location_code: str
    ) -> PickingTask | None:
        tasks = PickingTask.__table__.c
        bindings = TransportDecisionBinding.__table__.c
        transports = TransportTask.__table__.c
        projections = PositionProjection.__table__.c
        return await db.scalar(
            select(PickingTask)
            .join(
                TransportDecisionBinding,
                (bindings.workline_id == tasks.workline_id)
                & (bindings.resource_fence_id == tasks.target_rack_id)
                & (bindings.source_evidence_id == tasks.initial_plan_evidence_id),
            )
            .join(TransportTask, transports.client_request_id == bindings.client_request_id)
            .join(
                PositionProjection,
                (projections.object_type == "RACK")
                & (projections.object_id == tasks.target_rack_id)
                & (projections.workline_id == tasks.workline_id)
                & (projections.source_transport_task_id == transports.transport_task_id),
            )
            .where(
                tasks.workline_id == workline_id,
                tasks.status == PickingTaskStatus.EXECUTION_COMPLETED,
                bindings.step == "PICKING_TASK_TARGET_RACK_IN",
                transports.status == "SUCCEEDED",
                projections.position_unknown.is_(False),
                projections.arrival_face == tasks.target_rack_face,
                projections.position_json["kind"].as_string() == "RACK_POSITION",
                projections.position_json["location_code"].as_string() == location_code,
            )
            .order_by(tasks.id)
            .limit(1)
        )

    async def has_applied_source_face(self, db: AsyncSession, workline_id: int, rack_id: str, rack_face: str) -> bool:
        members = PickingTaskBinSourceRack.__table__.c
        tasks = PickingTask.__table__.c
        return (
            await db.scalar(
                select(members.id)
                .join(PickingTask, tasks.id == members.picking_task_id)
                .where(
                    tasks.workline_id == workline_id,
                    tasks.status.in_((PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED)),
                    members.rack_id == rack_id,
                    members.rack_face == rack_face,
                    members.cancelled_evidence_id.is_(None),
                    members.plan_revision <= tasks.last_applied_plan_revision,
                )
                .limit(1)
            )
            is not None
        )

    async def has_active_direct_pick_face(
        self, db: AsyncSession, *, picking_task_id: int, rack_id: str, rack_face: str
    ) -> bool:
        columns = DirectPickExecution.__table__.c
        return (
            await db.scalar(
                select(columns.id)
                .where(
                    columns.picking_task_id == picking_task_id,
                    columns.rack_id == rack_id,
                    columns.rack_face == rack_face,
                    columns.cancelled_evidence_id.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    async def has_direct_pick_face_completion(
        self, db: AsyncSession, *, picking_task_id: int, rack_id: str, rack_face: str
    ) -> bool:
        columns = DirectPickFaceCompletion.__table__.c
        return (
            await db.scalar(
                select(columns.id)
                .where(
                    columns.picking_task_id == picking_task_id,
                    columns.rack_id == rack_id,
                    columns.rack_face == rack_face,
                )
                .limit(1)
            )
            is not None
        )

    async def add_direct_pick_face_completion(
        self,
        db: AsyncSession,
        *,
        picking_task_id: int,
        rack_id: str,
        rack_face: str,
        completed_at: datetime,
        source_evidence_id: int,
    ) -> None:
        db.add(
            DirectPickFaceCompletion(
                picking_task_id=picking_task_id,
                rack_id=rack_id,
                rack_face=rack_face,
                completed_at=completed_at,
                source_evidence_id=source_evidence_id,
            )
        )
        await db.flush()

    async def list_bin_source_racks(self, db: AsyncSession, task_id: int) -> list[PickingTaskBinSourceRack]:
        columns = PickingTaskBinSourceRack.__table__.c
        result = await db.scalars(
            select(PickingTaskBinSourceRack)
            .where(columns.picking_task_id == task_id)
            .order_by(columns.plan_revision, columns.id)
        )
        return list(result.all())

    async def list_active_bin_source_racks(self, db: AsyncSession, task_id: int) -> list[PickingTaskBinSourceRack]:
        columns = PickingTaskBinSourceRack.__table__.c
        result = await db.scalars(
            select(PickingTaskBinSourceRack)
            .where(columns.picking_task_id == task_id, columns.cancelled_evidence_id.is_(None))
            .order_by(columns.plan_revision, columns.id)
        )
        return list(result.all())

    async def list_active_direct_picks(self, db: AsyncSession, task_id: int) -> list[DirectPickExecution]:
        columns = DirectPickExecution.__table__.c
        result = await db.scalars(
            select(DirectPickExecution)
            .where(columns.picking_task_id == task_id, columns.cancelled_evidence_id.is_(None))
            .order_by(columns.plan_revision, columns.id)
        )
        return list(result.all())

    async def get_evidence(self, db: AsyncSession, evidence_id: int) -> InboundEvidence | None:
        return await db.get(InboundEvidence, evidence_id)

    async def first_rejection(self, db: AsyncSession, evidence_id: int) -> str | None:
        columns = InboundEvidenceConflict.__table__.c
        return await db.scalar(
            select(columns.reason_code)
            .where(
                columns.first_evidence_id == evidence_id,
                columns.reason_code.not_in(
                    ("SOURCE_IDENTITY_PAYLOAD_CONFLICT", "SOURCE_IDENTITY_CORRELATION_CONFLICT")
                ),
            )
            .order_by(columns.id)
            .limit(1)
        )

    async def prepare_context(self, db: AsyncSession, task: Any) -> tuple[list[WmsConfirmation], WorkLine | None]:
        columns = WmsConfirmation.__table__.c
        confirmations = list(
            (
                await db.scalars(
                    select(WmsConfirmation).where(
                        columns.picking_task_id == task.id, columns.operation == PICKING_TASK_PREPARE_OPERATION
                    )
                )
            ).all()
        )
        line = await db.get(WorkLine, task.workline_id) if task.workline_id else None
        return confirmations, line

    async def source_identities(
        self,
        db: AsyncSession,
        task_id: int,
        *,
        direct_picks: list[tuple[str, str, str]],
        bin_racks: list[tuple[str, str]],
    ) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
        direct = DirectPickExecution.__table__.c
        bins = PickingTaskBinSourceRack.__table__.c
        picks: set[tuple[str, str, str]] = set()
        racks: set[tuple[str, str]] = set()
        # 仅查询本次候选身份，保持批次上限与原文精确比较。
        for batch in batched(direct_picks, MEMBER_BATCH_SIZE, strict=False):
            candidates = list(batch)
            rows = (
                await db.execute(
                    select(direct.rack_id, direct.rack_face, direct.slot_id).where(
                        direct.picking_task_id == task_id,
                        tuple_(direct.rack_id, direct.rack_face, direct.slot_id).in_(candidates),
                    )
                )
            ).all()
            picks.update(tuple(row) for row in rows)
        for batch in batched(bin_racks, MEMBER_BATCH_SIZE, strict=False):
            candidates = list(batch)
            rows = (
                await db.execute(
                    select(bins.rack_id, bins.rack_face).where(
                        bins.picking_task_id == task_id,
                        tuple_(bins.rack_id, bins.rack_face).in_(candidates),
                    )
                )
            ).all()
            racks.update(tuple(row) for row in rows)
        return picks, racks

    async def add_members(self, db: AsyncSession, task_id: int, data: Any, evidence_id: int) -> None:
        for batch in batched(data.added_direct_picks or (), MEMBER_BATCH_SIZE, strict=False):
            for pick in batch:
                locator = pick.source_locator
                db.add(
                    DirectPickExecution(
                        picking_task_id=task_id,
                        rack_id=locator.rack_id,
                        rack_face=locator.rack_face,
                        slot_id=locator.slot_id,
                        plan_revision=data.plan_revision,
                        source_evidence_id=evidence_id,
                    )
                )
            await db.flush()
        bin_racks = (
            (rack.rack_id, rack_face) for rack in data.added_bin_source_racks or () for rack_face in rack.rack_faces
        )
        for batch in batched(bin_racks, MEMBER_BATCH_SIZE, strict=False):
            for rack in batch:
                db.add(
                    PickingTaskBinSourceRack(
                        picking_task_id=task_id,
                        rack_id=rack[0],
                        rack_face=rack[1],
                        plan_revision=data.plan_revision,
                        source_evidence_id=evidence_id,
                    )
                )
            await db.flush()
        # 仅初始 target 的 revision 也要持久化任务/Evidence。
        await db.flush()
