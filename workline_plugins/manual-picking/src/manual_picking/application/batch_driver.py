"""工作线锁内按已应用货架成员与权威位置推进一个下一动作。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from wes_plugin_sdk import (
    FactRecorded,
    PickingTaskRackTransportIntent,
    RackDepartureReady,
    RackDepartureWait,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportZonePosition,
    wms_operations,
)

from manual_picking.definition import DEFINITION, FIVE_RACK, INLET, OUTLET, RETURN_RACK, TRANSFER_RACK
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
    DRAIN_RACK_ROTATE_STEP,
    RETURN_RACK_OUT_STEP,
    RETURN_RACK_ROTATE_STEP,
    SOURCE_RACK_IN_STEP,
    SOURCE_RACK_OUT_STEP,
    SOURCE_RACK_ROTATE_STEP,
)
from .passage_repository import PassageRepository
from .rack_readiness import has_single_current_rack, ready_rack_projection

TRANSFER_RACK_OUT_STEP = "MANUAL_PICKING_TRANSFER_RACK_OUT"

logger = logging.getLogger(__name__)


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
        arrival_scheduler: Any = None,
        arrival_reader: Any = None,
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
        self._arrival_scheduler = arrival_scheduler
        self._arrival_reader = arrival_reader
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
        advanced_task_id = None
        if owner is not None:
            task = await self._tasks.get_by_task_id_for_update(db, owner.task_id)
            if (
                task is not None
                and task.id == owner.id
                and task.workline_id == line.id
                and task.status == "EXECUTION_COMPLETED"
            ):
                advanced_task_id = task.id
                source_count = await self.advance_in_session(db, line, task)
        # 只含直接取料的任务不会被五层架来源查询找到，退料货架子流程必须独立再试一次。
        source_count += await self._advance_completed_return_rack(db, line, advanced_task_id)
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

    async def _advance_completed_return_rack(self, db: Any, line: Any, advanced_task_id: int | None) -> int:
        location = (line.position_bindings.get(RETURN_RACK.slot_key) or {}).get("location_id")
        if not location:
            return 0
        owner = await self._plans.first_completed_direct_pick_owner_at_position(db, line.id, location)
        if owner is None or owner.id == advanced_task_id:
            return 0
        task = await self._tasks.get_by_task_id_for_update(db, owner.task_id)
        if task is None or task.id != owner.id or task.workline_id != line.id or task.status != "EXECUTION_COMPLETED":
            return 0
        return await self._advance_return_rack(db, line, task)

    async def advance_in_session(self, db: Any, line: Any, task: Any) -> int:
        # 子流程 A（五层架）与子流程 B（退料货架）物理并行，任一条被自身条件挡住都不影响另一条。
        filled = await self._fill_source_window(db, line, task) if task.status == "EXECUTING" else 0
        advanced = await self._advance_current_rack(db, line, task)
        return filled + advanced + await self._advance_return_rack(db, line, task)

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
        sources = await self._plans.list_active_bin_source_racks(db, task.id)
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
                picking_task_id=task.id,
                source_evidence_id=row.source_evidence_id,
                correlation_id=f"pt:{task.id}:e:{row.source_evidence_id}:rack:{row.rack_id}",
                step=SOURCE_RACK_IN_STEP,
                resource_fence_id=row.rack_id,
                intent=intent,
            )
        return len(selected)

    async def _advance_drain(self, db: Any, line: Any) -> int:  # noqa: PLR0911, PLR0912
        # 旧批次必须先发布/闭合，否则新 drain 会改变该响应的当前货架归属。
        if await self._flow.has_unclosed_action(db, line.id):
            return 0
        count, decision = await self._drain.decide_in_session(db, line, timezone.now_for_db())
        if decision is None:
            return count
        repository = self._drain.repository
        if await repository.has_unclosed_rack_action(db, decision):
            return count
        active = await self._drain.active_rack_face(db, line, decision)
        if active is None:
            return count
        rack_id, rack_face = active
        racks = [rack.rack_id for rack in decision.result.racks]
        for previous_rack in racks[: racks.index(rack_id)]:
            departure = await repository.transport(db, decision, DRAIN_RACK_OUT_STEP, previous_rack)
            if departure is None:
                previous_projection = await self._positions.get(db, "RACK", previous_rack)
                if (
                    previous_projection is None
                    or previous_projection.workline_id != line.id
                    or previous_projection.position_unknown
                    or not previous_projection.arrival_face
                ):
                    return count
                return count + await self._advance_workline_departure(
                    db,
                    line,
                    rack_id=previous_rack,
                    current_face=previous_projection.arrival_face,
                    correlation_id=f"drain:{decision.intent.operation_id}:rack:{previous_rack}",
                    step=DRAIN_RACK_OUT_STEP,
                    picking_task_id=None,
                    now=timezone.now_for_db(),
                )
            if departure is None or departure.status not in {"ACCEPTED", "SUCCEEDED", "FAILED"}:
                return count
        ingress = await repository.transport(db, decision, DRAIN_RACK_IN_STEP, rack_id)
        position = line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        if ingress is None:
            remaining, occupied = await self._source_window(db, line)
            if not remaining or rack_id in occupied:
                return count
            if rack_id in await self._rack_cycles.fenced_source_rack_ids(db, line.id):
                return count
            await self._rack_creator.create(
                db,
                workline_id=line.id,
                source_evidence_id=decision.evidence_id,
                correlation_id=f"drain:{decision.intent.operation_id}:rack:{rack_id}",
                step=DRAIN_RACK_IN_STEP,
                resource_fence_id=rack_id,
                intent=_DrainRackIntent(
                    rack_id, TransportRackReference(rack_id), TransportRackPosition(position), rack_face
                ),
            )
            return count + 1
        if ingress.status != "SUCCEEDED":
            return count
        projection = await self._positions.get(db, "RACK", rack_id)
        if (
            projection is None
            or projection.workline_id != line.id
            or projection.position_unknown
            or projection.position_json != {"kind": "RACK_POSITION", "location_code": position}
            or not projection.source_transport_task_id
        ):
            return count
        projection_transport = await self._transports.get_task(db, projection.source_transport_task_id)
        if projection_transport is None or projection_transport.status != "SUCCEEDED":
            return count
        if projection.arrival_face != rack_face:
            rotation = await repository.transport(db, decision, DRAIN_RACK_ROTATE_STEP, rack_id, rack_face)
            if rotation is None:
                await self._rack_creator.create_rotate(
                    db,
                    workline_id=line.id,
                    source_evidence_id=decision.evidence_id,
                    correlation_id=f"drain:{decision.intent.operation_id}:rack:{rack_id}:face:{rack_face}",
                    step=DRAIN_RACK_ROTATE_STEP,
                    rack_id=rack_id,
                    position=TransportRackPosition(position),
                    target_face=rack_face,
                )
                return count + 1
            if rotation.status != "SUCCEEDED":
                return count
            projection = await ready_rack_projection(
                db, line, rack_id, rack_face, position, positions=self._positions, transports=self._transports
            )
            if projection is None or not await repository.arrival_matches(db, rotation, projection, rack_id, rack_face):
                return count
        elif not await repository.arrival_matches(db, ingress, projection, rack_id, rack_face):
            return count
        if await self._drain.return_in_session(
            db, line, decision, line.position_bindings[OUTLET.slot_key]["location_id"], timezone.now_for_db()
        ):
            return count + 1
        if await self._passages.has_bin_before_return_buffer(
            db, line.id
        ) or await self._passages.unfinished_return_prefix_for_update(db, line.id):
            return count
        return count + await self._advance_workline_departure(
            db,
            line,
            rack_id=rack_id,
            current_face=projection.arrival_face,
            correlation_id=f"drain:{decision.intent.operation_id}:rack:{rack_id}",
            step=DRAIN_RACK_OUT_STEP,
            picking_task_id=None,
            now=timezone.now_for_db(),
        )

    async def _advance_current_rack(self, db: Any, line: Any, task: Any) -> int:
        if not task.target_rack_id or not task.target_rack_face:
            return 0
        bindings = line.position_bindings
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
                picking_task_id=task.id,
                source_evidence_id=next_face.source_evidence_id,
                correlation_id=f"pt:{task.id}:source-face:{next_face.id}",
                step=SOURCE_RACK_ROTATE_STEP,
                rack_id=current.rack_id,
                position=TransportRackPosition(bindings[FIVE_RACK.slot_key]["location_id"]),
                target_face=next_face.rack_face,
            )
            return 1
        return await self._advance_workline_departure(
            db,
            line,
            rack_id=current.rack_id,
            current_face=projection.arrival_face,
            correlation_id=f"pt:{task.id}:source-out:{current.rack_id}",
            step=SOURCE_RACK_OUT_STEP,
            picking_task_id=task.id,
            now=timezone.now_for_db(),
        )

    async def _advance_return_rack(self, db: Any, line: Any, task: Any) -> int:  # noqa: PLR0911
        """取货由 PDA 黑盒完成；WES 只识别到位、上报事实，并按 WMS 面级完成事实换面或离场。

        已知缺口：到位识别依赖退料货架已有 PositionProjection，而当前无任何代码
        创建对应的入线 Transport（见 TODOS.md「退料货架入线 Transport 缺口」），
        生产环境暂时无法触发本子流程。
        """
        location = (line.position_bindings.get(RETURN_RACK.slot_key) or {}).get("location_id")
        if not location or self._arrival_scheduler is None or self._arrival_reader is None:
            return 0
        faces_by_rack: dict[str, list[Any]] = {}
        for row in await self._plans.list_active_direct_picks(db, task.id):
            # 完成事实是面级的，同面多个 slot_id 只占一个换面位置。
            faces = faces_by_rack.setdefault(row.rack_id, [])
            if all(face.rack_face != row.rack_face for face in faces):
                faces.append(row)
        ordered = [row for faces in faces_by_rack.values() for row in faces]
        if not ordered or not await has_single_current_rack(db, line.id, location, positions=self._positions):
            return 0
        current = projection = None
        for row in ordered:
            projection = await ready_rack_projection(
                db, line, row.rack_id, row.rack_face, location, positions=self._positions, transports=self._transports
            )
            if projection is not None:
                current = row
                break
        if current is None or projection is None:
            return 0
        now = timezone.now_for_db()
        snapshot = await self._arrival_reader.latest(db, task.id, current.rack_id)
        if snapshot is None:
            transport = await self._transports.get_task(db, projection.source_transport_task_id)
            if transport.published_outcome_version <= 0:
                return 0
            intent = wms_operations.outbound_return_rack_arrival_report(
                operation_id=self._uuid_factory(),
                task_id=task.task_id,
                transport_task_id=projection.source_transport_task_id,
                outcome_revision=transport.published_outcome_version,
                rack_id=current.rack_id,
                final_position=TransportRackPosition(location),
                arrival_face=projection.arrival_face,
            )
            await self._arrival_scheduler.create_in_session(db, intent, picking_task_id=task.id, created_at=now)
            return 1
        if snapshot.intent.task_id != task.task_id or snapshot.intent.rack_id != current.rack_id:
            # 本子流程与五层架子流程共用事务，抛异常会回滚对方本拍已完成的推进，因此只记录并让位。
            logger.error(
                "manual_picking.return_rack_arrival_identity_drift",
                extra={
                    "picking_task_id": task.id,
                    "task_id": task.task_id,
                    "rack_id": current.rack_id,
                    "operation_id": snapshot.intent.operation_id,
                    "confirmation_task_id": snapshot.intent.task_id,
                    "confirmation_rack_id": snapshot.intent.rack_id,
                },
            )
            return 0
        if snapshot.status != WmsConfirmationStatus.COMPLETED:
            # PENDING/DISPATCHING 是正常在途；RECONCILING/SUPERSEDED 不会自愈，货架将永久滞留。
            if snapshot.status in {WmsConfirmationStatus.RECONCILING, WmsConfirmationStatus.SUPERSEDED}:
                logger.warning(
                    "manual_picking.return_rack_arrival_stuck",
                    extra={
                        "picking_task_id": task.id,
                        "task_id": task.task_id,
                        "rack_id": current.rack_id,
                        "operation_id": snapshot.intent.operation_id,
                        "status": snapshot.status,
                    },
                )
            return 0
        if snapshot.outcome is None or not isinstance(snapshot.outcome.result, FactRecorded):
            # REJECTED/CONFLICT/UNAVAILABLE 派生的结果同样不会自愈，必须留下可检索的告警。
            logger.warning(
                "manual_picking.return_rack_arrival_not_recorded",
                extra={
                    "picking_task_id": task.id,
                    "task_id": task.task_id,
                    "rack_id": current.rack_id,
                    "operation_id": snapshot.intent.operation_id,
                    "result": type(snapshot.outcome.result).__name__ if snapshot.outcome is not None else None,
                },
            )
            return 0
        if not await self._plans.has_direct_pick_face_completion(
            db, picking_task_id=task.id, rack_id=current.rack_id, rack_face=current.rack_face
        ):
            return 0
        # 到位面由外部搬运决定，不保证是计划首面；遍历全部面而非 index 之后的切片，
        # 否则更早的未结面会被静默跳过。
        for next_face in faces_by_rack[current.rack_id]:
            if next_face is current or await self._plans.has_direct_pick_face_completion(
                db, picking_task_id=task.id, rack_id=current.rack_id, rack_face=next_face.rack_face
            ):
                continue
            await self._rack_creator.create_rotate(
                db,
                workline_id=line.id,
                picking_task_id=task.id,
                source_evidence_id=next_face.source_evidence_id,
                correlation_id=f"pt:{task.id}:return-face:{next_face.id}",
                step=RETURN_RACK_ROTATE_STEP,
                rack_id=current.rack_id,
                position=TransportRackPosition(location),
                target_face=next_face.rack_face,
            )
            return 1
        return await self._advance_workline_departure(
            db,
            line,
            rack_id=current.rack_id,
            current_face=projection.arrival_face,
            correlation_id=f"pt:{task.id}:return-out:{current.rack_id}",
            step=RETURN_RACK_OUT_STEP,
            picking_task_id=task.id,
            now=now,
            current_location=location,
        )

    async def _advance_workline_departure(
        self,
        db: Any,
        line: Any,
        *,
        rack_id: str,
        current_face: str,
        correlation_id: str,
        step: str,
        picking_task_id: int | None,
        now: Any,
        current_location: str | None = None,
    ) -> int:
        location = current_location or line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        snapshot = await self._departure_reader.latest_for_workline(db, line.id, rack_id)
        if snapshot is None:
            intent = wms_operations.outbound_rack_departure_decide(
                operation_id=self._uuid_factory(),
                task_id=None,
                rack_id=rack_id,
                current_location=TransportRackPosition(location),
                current_face=current_face,
            )
            await self._departure_scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
            return 1
        if snapshot.intent.task_id is not None or snapshot.intent.rack_id != rack_id:
            raise ValueError("source/drain departure must be WorkLine-owned with null task_id")
        if snapshot.status != WmsConfirmationStatus.COMPLETED or snapshot.outcome is None:
            return 0
        result = snapshot.outcome.result
        if isinstance(result, RackDepartureWait):
            if snapshot.completed_at is None or now < snapshot.completed_at + timedelta(
                milliseconds=result.retry_after_ms
            ):
                return 0
            intent = wms_operations.outbound_rack_departure_decide(
                operation_id=self._uuid_factory(),
                task_id=None,
                rack_id=rack_id,
                current_location=TransportRackPosition(location),
                current_face=current_face,
            )
            await self._departure_scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
            return 1
        if not isinstance(result, RackDepartureReady) or snapshot.evidence_id is None:
            return 0
        await self._rack_creator.create_source_return(
            db,
            workline_id=line.id,
            picking_task_id=picking_task_id,
            source_evidence_id=snapshot.evidence_id,
            correlation_id=correlation_id,
            step=step,
            rack_id=rack_id,
            destination=result.rack_destination,
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
                    picking_task_id=task.id,
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


__all__ = [
    "RETURN_RACK_OUT_STEP",
    "RETURN_RACK_ROTATE_STEP",
    "SOURCE_RACK_OUT_STEP",
    "SOURCE_RACK_ROTATE_STEP",
    "TRANSFER_RACK_OUT_STEP",
    "ManualPickingBatchDriver",
]
