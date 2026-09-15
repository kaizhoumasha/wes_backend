"""工作线锁内按已应用货架成员与权威位置推进一个下一动作。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from wes_plugin_sdk import (
    RackDepartureReady,
    RackDepartureWait,
    TransportRackPosition,
    TransportZonePosition,
    wms_operations,
)

from manual_picking.definition import FIVE_RACK, INLET, OUTLET, TRANSFER_RACK
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .passage_repository import PassageRepository
from .rack_readiness import rack_ready, ready_rack_projection

SOURCE_RACK_ROTATE_STEP = "MANUAL_PICKING_SOURCE_RACK_ROTATE"
SOURCE_RACK_OUT_STEP = "MANUAL_PICKING_SOURCE_RACK_OUT"
TRANSFER_RACK_OUT_STEP = "MANUAL_PICKING_TRANSFER_RACK_OUT"


class ManualPickingBatchDriver:
    def __init__(
        self,
        flow: Any,
        *,
        plans: Any,
        positions: Any,
        transports: Any,
        rack_creator: Any,
        departure_scheduler: Any,
        departure_reader: Any,
        passages: Any = None,
        tasks: Any = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._flow = flow
        self._plans = plans
        self._positions = positions
        self._transports = transports
        self._rack_creator = rack_creator
        self._departure_scheduler = departure_scheduler
        self._departure_reader = departure_reader
        self._passages = passages or PassageRepository()
        self._tasks = tasks or picking_task_repository
        self._uuid_factory = uuid_factory

    async def advance_completed_in_session(self, db: Any, line: Any) -> int:
        owner = await self._plans.first_completed_source_owner_at_position(
            db,
            line.id,
            line.position_bindings[FIVE_RACK.slot_key]["location_id"],
        )
        source_count = 0
        if owner is not None:
            task = await self._tasks.get_by_task_id_for_update(db, owner.task_id)
            if (
                task is not None
                and task.id == owner.id
                and task.workline_id == line.id
                and task.status == "EXECUTION_COMPLETED"
            ):
                source_count = await self.advance_in_session(db, line, task, require_target=False)
        owner = await self._plans.first_completed_transfer_owner_at_position(
            db,
            line.id,
            line.position_bindings[TRANSFER_RACK.slot_key]["location_id"],
        )
        if owner is None:
            return source_count
        task = await self._tasks.get_by_task_id_for_update(db, owner.task_id)
        if task is None or task.id != owner.id or task.workline_id != line.id or task.status != "EXECUTION_COMPLETED":
            return source_count
        return source_count + await self._advance_transfer_departure(db, line, task, timezone.now_for_db())

    async def advance_in_session(self, db: Any, line: Any, task: Any, *, require_target: bool = True) -> int:
        if not task.target_rack_id or not task.target_rack_face:
            return 0
        bindings = line.position_bindings
        if require_target and not await rack_ready(
            db,
            line,
            task.target_rack_id,
            task.target_rack_face,
            bindings[TRANSFER_RACK.slot_key]["location_id"],
            positions=self._positions,
            transports=self._transports,
        ):
            return 0
        sources = await self._plans.list_bin_source_racks(db, task.id)
        faces_by_rack: dict[str, list[Any]] = {}
        for row in sources:
            faces_by_rack.setdefault(row.rack_id, []).append(row)
        ordered_sources = [row for faces in faces_by_rack.values() for row in faces]
        current = None
        projection = None
        for row in ordered_sources:
            candidate = await ready_rack_projection(
                db,
                line,
                row.rack_id,
                row.rack_face,
                bindings[FIVE_RACK.slot_key]["location_id"],
                positions=self._positions,
                transports=self._transports,
            )
            if candidate is None or not await self._plans.source_transport_matches(
                db, line.id, row.rack_id, row.source_evidence_id, candidate.source_transport_task_id
            ):
                continue
            if projection is None or (
                candidate.updated_at is not None
                and (projection.updated_at is None or candidate.updated_at > projection.updated_at)
            ):
                current = row
                projection = candidate
        if current is None or projection is None:
            return 0
        now = timezone.now_for_db()
        if await self._flow.advance_in_session(
            db,
            workline_id=line.id,
            workline_code=line.line_code,
            task_id=task.task_id,
            rack_id=current.rack_id,
            rack_face=current.rack_face,
            return_location=bindings[OUTLET.slot_key]["location_id"],
            inlet_location=bindings[INLET.slot_key]["location_id"],
            now=now,
            allow_inbound=task.status == "EXECUTING",
        ):
            return 1
        progress = await self._flow.face_progress(db, line.id, task.task_id, current.rack_id, current.rack_face)
        if (
            progress is None
            or not progress.complete
            or await self._flow.has_unclosed_action(db, line.id)
            or await self._passages.has_bin_before_return_buffer(db, line.id)
        ):
            return 0
        rack_faces = faces_by_rack[current.rack_id]
        current_index = rack_faces.index(current)
        for next_face in rack_faces[current_index + 1 :]:
            next_progress = await self._flow.face_progress(
                db, line.id, task.task_id, next_face.rack_id, next_face.rack_face
            )
            if next_progress is not None:
                return 0
            await self._rack_creator.create_rotate(
                db,
                workline_id=line.id,
                source_evidence_id=next_face.source_evidence_id,
                correlation_id=f"pt:{task.id}:source-face:{next_face.id}",
                step=SOURCE_RACK_ROTATE_STEP,
                rack_id=current.rack_id,
                position=TransportRackPosition(bindings[FIVE_RACK.slot_key]["location_id"]),
                target_face=next_face.rack_face,
            )
            return 1
        await self._rack_creator.create_source_return(
            db,
            workline_id=line.id,
            source_evidence_id=current.source_evidence_id,
            correlation_id=f"pt:{task.id}:source-out:{current.rack_id}",
            step=SOURCE_RACK_OUT_STEP,
            rack_id=current.rack_id,
            destination=TransportZonePosition("WH01"),
        )
        return 1

    async def _advance_transfer_departure(self, db: Any, line: Any, task: Any, now: Any) -> int:
        current_location = line.position_bindings[TRANSFER_RACK.slot_key]["location_id"]
        projection = await ready_rack_projection(
            db,
            line,
            task.target_rack_id,
            task.target_rack_face,
            current_location,
            positions=self._positions,
            transports=self._transports,
        )
        if projection is None:
            return 0
        snapshot = await self._departure_reader.latest(db, task.id, task.target_rack_id)
        if snapshot is not None:
            if snapshot.intent.task_id != task.task_id or snapshot.intent.rack_id != task.target_rack_id:
                raise ValueError("departure result differs from original task and rack")
            if snapshot.status != WmsConfirmationStatus.COMPLETED or snapshot.outcome is None:
                return 0
            result = snapshot.outcome.result
            if isinstance(result, RackDepartureWait):
                unchanged_position = (
                    snapshot.intent.current_location.location_code == current_location
                    and snapshot.intent.current_face == projection.arrival_face
                )
                if unchanged_position and (
                    snapshot.completed_at is None
                    or now < snapshot.completed_at + timedelta(milliseconds=result.retry_after_ms)
                ):
                    return 0
            elif isinstance(result, RackDepartureReady):
                if (
                    snapshot.intent.current_location.location_code != current_location
                    or snapshot.intent.current_face != projection.arrival_face
                    or type(result.rack_destination) not in (TransportZonePosition, TransportRackPosition)
                    or (
                        type(result.rack_destination) is TransportRackPosition
                        and result.rack_destination.location_code == current_location
                    )
                    or snapshot.evidence_id is None
                ):
                    raise ValueError("transfer rack departure role or physical position conflicts with WMS decision")
                await self._rack_creator.create_transfer_departure(
                    db,
                    workline_id=line.id,
                    source_evidence_id=snapshot.evidence_id,
                    operation_id=snapshot.intent.operation_id,
                    step=TRANSFER_RACK_OUT_STEP,
                    rack_id=task.target_rack_id,
                    destination=result.rack_destination,
                )
                return 1
            else:
                return 0
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=self._uuid_factory(),
            task_id=task.task_id,
            rack_id=task.target_rack_id,
            current_location=TransportRackPosition(current_location),
            current_face=projection.arrival_face,
        )
        await self._departure_scheduler.create_in_session(db, intent, picking_task_id=task.id, created_at=now)
        return 1


__all__ = ["SOURCE_RACK_OUT_STEP", "SOURCE_RACK_ROTATE_STEP", "TRANSFER_RACK_OUT_STEP", "ManualPickingBatchDriver"]
