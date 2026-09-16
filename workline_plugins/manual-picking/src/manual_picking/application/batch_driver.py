"""工作线锁内按已应用货架成员与权威位置推进一个下一动作。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from wes_plugin_sdk import (
    PickingTaskRackTransportIntent,
    RackDepartureReady,
    RackDepartureWait,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportZonePosition,
    wms_operations,
)

from manual_picking.definition import DEFINITION, FIVE_RACK, INLET, OUTLET, TRANSFER_RACK
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.app.workline.installed_plugin import parse_position_bindings
from src.app.workline.services.workline_position_service import workline_position_service
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .batch_repository import BatchRepository
from .drain_repository import (
    DRAIN_RACK_IN_STEP,
    DRAIN_RACK_OUT_STEP,
    SOURCE_RACK_IN_STEP,
    SOURCE_RACK_OUT_STEP,
    SOURCE_RACK_ROTATE_STEP,
)
from .passage_repository import PassageRepository
from .rack_readiness import has_single_current_rack, rack_ready, ready_rack_projection

TRANSFER_RACK_OUT_STEP = "MANUAL_PICKING_TRANSFER_RACK_OUT"


@dataclass(frozen=True)
class _DrainRackIntent:
    rack_id: str
    source: TransportRackReference
    target: TransportRackPosition
    target_face: str
    rcs_template_id: TransportRcsTemplateId = TransportRcsTemplateId.CTU01


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
        position_service: Any = None,
        rack_cycles: Any = None,
        bindings: Any = None,
        drain: Any = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._drain = drain
        self._position_service = position_service or workline_position_service
        self._rack_cycles = rack_cycles or BatchRepository()
        self._bindings = bindings or transport_decision_binding_repository
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
        source_count += await self._advance_drain(db, line) if self._drain is not None else 0
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
        filled = await self._fill_source_window(db, line, task) if task.status == "EXECUTING" else 0
        return filled + await self._advance_current_rack(db, line, task, require_target=require_target)

    async def _source_window(self, db: Any, line: Any) -> tuple[int, set[str]]:
        # 调用方持有工作线锁；容量仅限制 CTU01 准入，不代表同时在位的物理货架数。
        configured = parse_position_bindings(line.config, DEFINITION.position_slots)
        capacity = await self._position_service.require_position_capacity(
            db, workline_code=line.line_code, position_code=configured[FIVE_RACK.slot_key]
        )
        occupied = await self._rack_cycles.occupied_source_rack_ids(db, line.id)
        return max(0, capacity - len(occupied)), occupied

    async def _fill_source_window(self, db: Any, line: Any, task: Any) -> int:
        remaining, occupied = await self._source_window(db, line)
        if not remaining:
            return 0
        decided = await self._bindings.list_task_resource_fence_ids(
            db, workline_id=line.id, picking_task_id=task.id, steps=(SOURCE_RACK_IN_STEP,)
        )
        sources = await self._plans.list_bin_source_racks(db, task.id)
        fenced = await self._rack_cycles.fenced_source_rack_ids(db, line.id)
        excluded = decided | occupied | fenced
        pending: dict[str, Any] = {}
        for row in sources:
            if row.plan_revision <= task.last_applied_plan_revision and row.rack_id not in excluded:
                pending.setdefault(row.rack_id, row)
        selected = list(pending.values())[:remaining]
        for row in selected:
            intent = PickingTaskRackTransportIntent(
                task_id=task.task_id,
                fact_id=f"picking-task-plan:{task.id}:{task.last_applied_plan_revision}",
                source_evidence_id=str(row.source_evidence_id),
                position_role=FIVE_RACK.slot_key,
                rack_id=row.rack_id,
                source=TransportRackReference(row.rack_id),
                target=TransportRackPosition(line.position_bindings[FIVE_RACK.slot_key]["location_id"]),
                target_face=row.rack_face,
                rcs_template_id=TransportRcsTemplateId.CTU01,
            )
            await self._rack_creator.create(
                db,
                workline_id=line.id,
                source_evidence_id=row.source_evidence_id,
                correlation_id=f"pt:{task.id}:e:{row.source_evidence_id}:rack:{row.rack_id}",
                step=SOURCE_RACK_IN_STEP,
                resource_fence_id=row.rack_id,
                intent=intent,
            )
        return len(selected)

    async def _advance_drain(self, db: Any, line: Any) -> int:  # noqa: PLR0911
        # 旧批次必须先发布/闭合，否则新 drain 会改变该响应的当前货架归属。
        if await self._flow.has_unclosed_action(db, line.id):
            return 0
        count, decision = await self._drain.decide_in_session(db, line, timezone.now_for_db())
        if decision is None:
            return count
        repository = self._drain.repository
        if await repository.has_unclosed_rack_action(db, line.id):
            return count
        # 已有离场 identity 不再发起回架或新离场，即使接纳/结果仍未知。
        if await repository.transport(db, decision, DRAIN_RACK_OUT_STEP) is not None:
            return count
        rack = decision.result
        ingress = await repository.transport(db, decision, DRAIN_RACK_IN_STEP)
        position = line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        if ingress is None:
            remaining, occupied = await self._source_window(db, line)
            if not remaining or rack.rack_id in occupied:
                return count
            if rack.rack_id in await self._rack_cycles.fenced_source_rack_ids(db, line.id):
                return count
            await self._rack_creator.create(
                db,
                workline_id=line.id,
                source_evidence_id=decision.evidence_id,
                correlation_id=f"drain:{decision.intent.operation_id}",
                step=DRAIN_RACK_IN_STEP,
                resource_fence_id=rack.rack_id,
                intent=_DrainRackIntent(
                    rack.rack_id, TransportRackReference(rack.rack_id), TransportRackPosition(position), rack.rack_face
                ),
            )
            return count + 1
        if ingress.status != "SUCCEEDED":
            return count
        projection = await ready_rack_projection(
            db, line, rack.rack_id, rack.rack_face, position, positions=self._positions, transports=self._transports
        )
        if projection is None or not await repository.arrival_matches(
            db, ingress, projection, rack.rack_id, rack.rack_face
        ):
            return count
        if await self._drain.return_in_session(
            db, line, decision, line.position_bindings[OUTLET.slot_key]["location_id"], timezone.now_for_db()
        ):
            return count + 1
        if await self._passages.has_bin_before_return_buffer(
            db, line.id
        ) or await self._passages.unfinished_return_prefix_for_update(db, line.id):
            return count
        await self._rack_creator.create_source_return(
            db,
            workline_id=line.id,
            source_evidence_id=decision.evidence_id,
            correlation_id=f"drain:{decision.intent.operation_id}",
            step=DRAIN_RACK_OUT_STEP,
            rack_id=rack.rack_id,
            destination=TransportZonePosition("WH01"),
        )
        return count + 1

    async def _advance_current_rack(  # noqa: PLR0911
        self, db: Any, line: Any, task: Any, *, require_target: bool
    ) -> int:
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
        source_location = bindings[FIVE_RACK.slot_key]["location_id"]
        if not ordered_sources or not await has_single_current_rack(
            db, line.id, source_location, positions=self._positions
        ):
            return 0
        current = None
        projection = None
        for row in ordered_sources:
            candidate = await ready_rack_projection(
                db,
                line,
                row.rack_id,
                row.rack_face,
                source_location,
                positions=self._positions,
                transports=self._transports,
                source_cardinality_checked=True,
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
        if await self._flow.has_unclosed_action(db, line.id):
            return 0
        inlet_location = bindings[INLET.slot_key]["location_id"]
        progress = await self._flow.face_progress(
            db, line.id, task.task_id, current.rack_id, current.rack_face, inlet_location
        )
        if progress is None or not progress.feed_complete:
            return int(
                await self._flow.advance_in_session(
                    db,
                    workline_id=line.id,
                    workline_code=line.line_code,
                    task_id=task.task_id,
                    rack_id=current.rack_id,
                    rack_face=current.rack_face,
                    return_location=bindings[OUTLET.slot_key]["location_id"],
                    inlet_location=inlet_location,
                    now=timezone.now_for_db(),
                    allow_inbound=task.status == "EXECUTING",
                )
            )
        rack_faces = faces_by_rack[current.rack_id]
        current_index = rack_faces.index(current)
        for next_face in rack_faces[current_index + 1 :]:
            next_progress = await self._flow.face_progress(
                db, line.id, task.task_id, next_face.rack_id, next_face.rack_face, inlet_location
            )
            if next_progress is not None:
                if next_progress.feed_complete:
                    continue
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
            source_evidence_id=rack_faces[0].source_evidence_id,
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
