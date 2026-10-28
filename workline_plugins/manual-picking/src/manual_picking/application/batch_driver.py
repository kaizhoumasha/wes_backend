"""工作线锁内按已应用货架成员与权威位置推进一个下一动作。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from wes_plugin_sdk import (
    BinReturnBatchReady,
    FactRecorded,
    PickingTaskRackTransportIntent,
    RackDepartureReady,
    RackDepartureWait,
    ReturnBufferDrainReady,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportZonePosition,
    wms_operations,
)

from manual_picking.definition import FIVE_RACK, INLET, OUTLET, RETURN_RACK, TRANSFER_RACK
from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import filter_undecided_candidates
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .batch_repository import BatchRepository
from .departure_retry_repository import DepartureRetryRepository
from .drain_repository import (
    DRAIN_RACK_IN_STEP,
    DRAIN_RACK_OUT_STEP,
    DRAIN_RACK_ROTATE_STEP,
    RETIRED_DRAIN_RACK_OUT_STEP,
    RETURN_RACK_OUT_STEP,
    RETURN_RACK_ROTATE_STEP,
    SOURCE_RACK_IN_STEP,
    SOURCE_RACK_OUT_STEP,
    SOURCE_RACK_ROTATE_STEP,
)
from .manual_source_arrival_model import ManualPickingSourceArrival, matches_source_arrival
from .passage_repository import PassageRepository
from .rack_readiness import ready_rack_projection

TRANSFER_RACK_OUT_STEP = "MANUAL_PICKING_TRANSFER_RACK_OUT"

logger = logging.getLogger(__name__)


async def pending_first_source_candidates(
    db: Any,
    plans: Any,
    bindings: Any,
    *,
    workline_id: int,
    task: Any,
    target_location_code: str | None = None,
    exclude_reused_window_candidates: bool = False,
) -> list[Any]:
    """尚未形成首次进场绑定的有效来源候选，按 (source_evidence_id, rack_id) 去重（R3 首次候选）。

    BatchDriver 的正常补位与 ScanFlow 的进场结果重试共用这一入口，保证重试候选不会抢在
    未决的首次候选前面拿到刚释放的名额。
    """
    decided = await bindings.list_task_member_bindings(
        db, workline_id=workline_id, picking_task_id=task.id, steps=(SOURCE_RACK_IN_STEP,)
    )
    sources = await plans.list_active_bin_source_racks(db, task.id)
    filtered = filter_undecided_candidates(sources, decided, last_applied_plan_revision=task.last_applied_plan_revision)
    active_racks: set[str] = set()
    if exclude_reused_window_candidates:
        assert target_location_code is not None
        active_windows = await bindings.list_active_window_for_target(
            db, workline_id=workline_id, target_location_code=target_location_code
        )
        active_racks = {row.resource_fence_id for row in active_windows}
    pending: dict[tuple[int, str], Any] = {}
    for row in filtered:
        if row.rack_id in active_racks:
            continue
        pending.setdefault((row.source_evidence_id, row.rack_id), row)
    return list(pending.values())


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
        bindings: Any = None,
        drain: Any = None,
        evidences: Any = None,
        batches: Any = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._departure_retries = DepartureRetryRepository()
        self._drain = drain
        self._batches = batches or BatchRepository()
        self._evidences = evidences or inbound_evidence_repository
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

    async def project_exits_in_session(self, db: Any, line: Any) -> int:
        """扫描持久离场义务；原 Transport 接纳只完成交接，不声明物理取走。"""
        count = await self._batches.release_completed_source_windows(db, workline_id=line.id, now=timezone.now_for_db())
        count += await self._advance_cancelled_source_windows(db, line)
        count += await self._advance_retired_drain_clearance(db, line) if self._drain is not None else 0
        outlet = line.position_bindings[OUTLET.slot_key]["location_id"]
        for row in await self._passages.requested_for_update(db, line.id):
            if row.return_batch_evidence_id is None:
                continue
            evidence = await self._evidences.get_by_id(db, row.return_batch_evidence_id)
            if (
                evidence is None
                or evidence.kind != InboundEvidenceKind.WMS_RESULT
                or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
                or evidence.operation != BIN_RETURN_BATCH_OPERATION
            ):
                continue
            binding = await self._bindings.get_by_decision_identity_for_update(
                db,
                workline_id=line.id,
                correlation_id=evidence.operation_id,
                step="MANUAL_PICKING_RETURN_BATCH",
            )
            if (
                binding is None
                or binding.source_evidence_id != evidence.id
                or binding.resource_fence_id != evidence.operation_id
            ):
                continue
            task = await self._transports.get_task_by_client_request(db, binding.client_request_id)
            if (
                task is None
                or task.kind != "BIN_MOVE"
                or task.authority_workline_id != line.id
                or task.client_request_id != binding.client_request_id
            ):
                continue
            members = await self._transports.list_members(db, task.transport_task_id)
            member = next((member for member in members if member.object_id == row.bin_code), None)
            if (
                member is None
                or member.object_type != "BIN"
                or member.source_json != {"kind": "HANDOFF_POSITION", "location_code": outlet}
            ):
                continue
            if task.status in {"ACCEPTED", "SUCCEEDED", "FAILED"} or (
                task.status == "RECONCILING"
                and (task.result_deadline_at is not None or task.last_applied_wms_outcome_revision > 0)
            ):
                # deadline 仅在 ACK/权威进度后建立；发送未知本身不能证明已交接。
                row.return_state = "EXITED"
                count += 1
        return count

    async def _advance_retired_drain_clearance(self, db: Any, line: Any) -> int:
        count = 0
        for root, current, position in await self._drain.repository.retired_clearance_racks(db, workline_id=line.id):
            if (
                current.status not in {"SUCCEEDED", "FAILED", "REJECTED"}
                or current.outcome_version < 1
                or current.published_outcome_version < current.outcome_version
                or await self._batches.has_current_rack_dependency(db, line.id, root.resource_fence_id, root.created_at)
            ):
                continue
            count += await self._advance_workline_departure(
                db,
                line,
                rack_id=root.resource_fence_id,
                current_face=position.arrival_face,
                step=RETIRED_DRAIN_RACK_OUT_STEP,
                picking_task_id=None,
                source_evidence_id=root.source_evidence_id,
                arrival_transport_task_id=current.transport_task_id,
                rejected_window=root if current.status == "REJECTED" else None,
                now=timezone.now_for_db(),
                current_location=root.window_target_location_code,
                round_started_at=root.created_at,
            )
        return count

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
        if TRANSFER_RACK.slot_key not in line.position_bindings:
            return source_count
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

    async def _advance_cancelled_source_windows(self, db: Any, line: Any) -> int:
        contexts = [
            (context, SOURCE_RACK_OUT_STEP)
            for context in await self._batches.cancelled_source_windows(
                db, workline_id=line.id, target_location_code=line.position_bindings[FIVE_RACK.slot_key]["location_id"]
            )
        ]
        return_location = (line.position_bindings.get(RETURN_RACK.slot_key) or {}).get("location_id")
        if return_location:
            contexts += [
                (context, RETURN_RACK_OUT_STEP)
                for context in await self._batches.cancelled_return_windows(
                    db, workline_id=line.id, target_location_code=return_location
                )
            ]
        count = 0
        for (window, current, position), step in contexts:
            if (
                (
                    not isinstance(position, ManualPickingSourceArrival)
                    and (current.status not in {"SUCCEEDED", "FAILED", "REJECTED"} or current.outcome_version < 1)
                )
                or current.published_outcome_version < current.outcome_version
                or await self._batches.has_current_rack_dependency(
                    db,
                    line.id,
                    window.resource_fence_id,
                    window.created_at,
                    arrival_transport_task_id=(
                        position.source_transport_task_id if isinstance(position, ManualPickingSourceArrival) else None
                    ),
                )
            ):
                continue
            if step == SOURCE_RACK_OUT_STEP and window.picking_task_id is not None:
                owner = await self._tasks.get_by_id_for_update(db, window.picking_task_id)
                if owner is not None and owner.status != "ARCHIVED":
                    pending_source = None
                    for source in await self._plans.list_bin_source_racks(db, owner.id):
                        if (source.rack_id, source.rack_face) != (window.resource_fence_id, position.arrival_face):
                            continue
                        progress = await self._flow.face_progress(
                            db,
                            line.id,
                            owner.task_id,
                            source.plan_revision,
                            source.rack_id,
                            source.rack_face,
                            line.position_bindings[INLET.slot_key]["location_id"],
                        )
                        if progress is not None and not progress.feed_complete:
                            pending_source = source
                            break
                    if pending_source is not None:
                        count += await self._flow.advance_in_session(
                            db,
                            workline_id=line.id,
                            workline_code=line.line_code,
                            picking_task_id=owner.id,
                            task_id=owner.task_id,
                            plan_revision=pending_source.plan_revision,
                            source_evidence_id=pending_source.source_evidence_id,
                            rack_id=pending_source.rack_id,
                            rack_face=pending_source.rack_face,
                            return_location=line.position_bindings[OUTLET.slot_key]["location_id"],
                            inlet_location=line.position_bindings[INLET.slot_key]["location_id"],
                            now=timezone.now_for_db(),
                            allow_inbound=False,
                        )
                        continue
            count += await self._advance_workline_departure(
                db,
                line,
                rack_id=window.resource_fence_id,
                current_face=position.arrival_face,
                step=step,
                picking_task_id=window.picking_task_id,
                arrival_transport_task_id=current.transport_task_id,
                rejected_window=window if current.status == "REJECTED" else None,
                now=timezone.now_for_db(),
                current_location=window.window_target_location_code,
                round_started_at=window.created_at,
            )
        return count

    async def _advance_completed_return_rack(self, db: Any, line: Any, advanced_task_id: int | None) -> int:
        # 注意：与 advance_in_session 共享同 workline lock，但同一 task 的退料货架推进
        # 在 advance_in_session → _advance_return_rack 已经处理过；这里仅补『未通过五层架
        # 来源查询找到的纯直接取料任务』，靠 owner.id == advanced_task_id 防御 double-call。
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
        filled = await self._submit_source_racks(db, line, task) if task.status == "EXECUTING" else 0
        advanced = await self._advance_current_rack(db, line, task)
        return filled + advanced + await self._advance_return_rack(db, line, task)

    async def _submit_source_racks(self, db: Any, line: Any, task: Any) -> int:
        pending = await pending_first_source_candidates(
            db,
            self._plans,
            self._bindings,
            workline_id=line.id,
            task=task,
        )
        created = 0
        for row in pending:
            if self._drain is not None and await self._drain.repository.has_retired_unclosed_inbound(
                db,
                workline_id=line.id,
                rack_id=row.rack_id,
                target_location_code=line.position_bindings[FIVE_RACK.slot_key]["location_id"],
                target_face=row.rack_face,
            ):
                continue
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
            admission = await self._rack_creator.create_windowed_inbound(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                picking_task_id=task.id,
                source_evidence_id=row.source_evidence_id,
                correlation_id=f"pt:{task.id}:e:{row.source_evidence_id}:rack:{row.rack_id}",
                step=SOURCE_RACK_IN_STEP,
                resource_fence_id=row.rack_id,
                intent=intent,
            )
            created += admission == "CREATED"
        return created

    async def _resume_drain_return(self, db: Any, line: Any, decision: Any, rack_id: str) -> int | None:  # noqa: PLR0911
        repository = self._drain.repository
        executions = repository.executions
        execution = await executions.for_evidence(db, workline_id=line.id, evidence_id=decision.evidence_id)
        pending = await executions.pending_return_for_rack(
            db, execution_id=execution.id, workline_id=line.id, rack_id=rack_id
        )
        if pending is None:
            return None
        intent, outcome, evidence = pending
        if intent.workline_code != line.line_code or (rack_id, intent.rack_face) not in {
            (rack.rack_id, face) for rack in decision.result.racks for face in rack.rack_faces
        }:
            return 0
        if outcome is None or not isinstance(outcome.result, BinReturnBatchReady):
            return 0
        if await repository.has_unclosed_rack_action(db, decision, rack_id):
            return 0
        ingress = await repository.transport(db, decision, DRAIN_RACK_IN_STEP, rack_id)
        source = await self._positions.get(db, "RACK", rack_id)
        if ingress is None or ingress.status != "SUCCEEDED" or source is None or source.position_unknown:
            return 0
        if (
            source.workline_id != line.id
            or source.position_json
            != {"kind": "RACK_POSITION", "location_code": line.position_bindings[FIVE_RACK.slot_key]["location_id"]}
            or source.arrival_face is None
        ):
            return 0
        current = ingress
        if source.source_transport_task_id != ingress.transport_task_id:
            current = await repository.transport(db, decision, DRAIN_RACK_ROTATE_STEP, rack_id, source.arrival_face)
        if current is None or not await repository.arrival_matches(db, current, source, rack_id, source.arrival_face):
            return 0
        if (
            await self._batches.has_newer_rack_action(db, rack_id=rack_id, causal_token=source.source_causal_token)
            or await self._batches.has_current_rack_dependency(db, line.id, rack_id, ingress.created_at)
            or await self._batches.has_unclosed_return_dependency_for_rack(
                db,
                workline_id=line.id,
                rack_id=rack_id,
                round_started_at=ingress.created_at,
                exclude_operation_id=intent.operation_id,
            )
            or await self._batches.has_return_batch_transport(db, workline_id=line.id, operation_id=intent.operation_id)
        ):
            return 0
        members = await executions.members_for_return_batch(db, workline_id=line.id, operation_id=intent.operation_id)
        moves = outcome.result.moves
        rows = await self._passages.by_ids_for_update(
            db, workline_id=line.id, return_ids=tuple(member.return_id for member in members[: len(moves)])
        )
        if len(rows) != len(moves) or tuple(row.bin_code for row in rows) != tuple(move.bin_code for move in moves):
            return 0
        for row in rows:
            current_return = await self._passages.current_bin_for_update(db, line.id, row.bin_code)
            if row.return_state != "READY" or current_return is None or current_return.id != row.id:
                return 0
        if source.arrival_face != intent.rack_face:
            # 恢复冻结 READY 的到位前提；原动作终态后以当前事实冻结新身份。
            correlation_id = f"drain:{decision.intent.operation_id}:rack:{rack_id}:face:{intent.rack_face}:after:{source.source_transport_task_id}"
            if (
                await self._bindings.get_by_decision_identity_for_update(
                    db, workline_id=line.id, correlation_id=correlation_id, step=DRAIN_RACK_ROTATE_STEP
                )
                is not None
            ):
                return 0
            await self._rack_creator.create_rotate(
                db,
                workline_id=line.id,
                source_evidence_id=decision.evidence_id,
                correlation_id=correlation_id,
                step=DRAIN_RACK_ROTATE_STEP,
                rack_id=rack_id,
                position=TransportRackPosition(line.position_bindings[FIVE_RACK.slot_key]["location_id"]),
                target_face=intent.rack_face,
            )
            return 1
        if evidence is not None and evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING:
            # WorkLine-owned WMS 结果由 processed_at=NULL 的标准 Decision 领取链重领。
            await self._evidences.mark_applied(db, evidence, processed_at=None)
            return 1
        return 0

    async def _advance_drain(self, db: Any, line: Any) -> int:  # noqa: PLR0911, PLR0912
        if await self._drain.retire_exception_in_session(db, line, timezone.now_for_db()):
            return 1
        # Drain rack 是无序 reservation；按各自权威到位事实独立推进。
        count, decision = await self._drain.decide_in_session(db, line, timezone.now_for_db())
        if decision is None:
            return count
        repository = self._drain.repository
        if await repository.complete_if_departed(db, decision, closed_at=timezone.now_for_db()):
            return count + 1
        filled = await self._submit_drain_racks(db, line, decision)
        if filled:
            return count + filled
        position = line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        for reserved_rack in decision.result.racks:
            rack_id = reserved_rack.rack_id
            resumed = await self._resume_drain_return(db, line, decision, rack_id)
            if resumed is not None:
                count += resumed
                continue
            if await repository.has_unclosed_rack_action(db, decision, rack_id):
                continue
            active = await self._drain.active_rack_face(db, line, decision, rack_id)
            if active is None:
                continue
            _, rack_face, exhausted = active
            ingress = await repository.transport(db, decision, DRAIN_RACK_IN_STEP, rack_id)
            if ingress is None or ingress.status != "SUCCEEDED":
                continue
            projection = await self._positions.get(db, "RACK", rack_id)
            if (
                projection is None
                or projection.workline_id != line.id
                or projection.position_unknown
                or projection.position_json != {"kind": "RACK_POSITION", "location_code": position}
                or not projection.source_transport_task_id
            ):
                continue
            projection_transport = await self._transports.get_task(db, projection.source_transport_task_id)
            if projection_transport is None or projection_transport.status != "SUCCEEDED":
                continue
            if (
                await self._batches.working_rack_position(
                    db, workline_id=line.id, rack_id=rack_id, location_code=position
                )
                is None
            ):
                continue
            if await self._batches.has_newer_rack_action(
                db, rack_id=rack_id, causal_token=getattr(projection, "source_causal_token", None)
            ):
                continue
            arrived_at = await self._batches.current_rack_arrival_at(db, workline_id=line.id, rack_id=rack_id)
            if (
                arrived_at is not None
                and await self._departure_reader.latest_for_workline(db, line.id, rack_id, arrived_at=arrived_at)
                is not None
            ):
                return count + await self._advance_workline_departure(
                    db,
                    line,
                    rack_id=rack_id,
                    current_face=projection.arrival_face,
                    step=DRAIN_RACK_OUT_STEP,
                    picking_task_id=None,
                    source_evidence_id=decision.evidence_id,
                    arrival_transport_task_id=projection.source_transport_task_id,
                    now=timezone.now_for_db(),
                )
            if projection.arrival_face != rack_face:
                rotation = await repository.transport(db, decision, DRAIN_RACK_ROTATE_STEP, rack_id, rack_face)
                if rotation is None:
                    returned = await self._return_before_rack_motion(
                        db,
                        line,
                        rack_id,
                        projection.arrival_face,
                        timezone.now_for_db(),
                    )
                    if returned is not None:
                        return count + returned
                    if await self._batches.has_unclosed_return_dependency_for_rack(
                        db, workline_id=line.id, rack_id=rack_id
                    ):
                        continue
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
                    continue
                projection = await ready_rack_projection(
                    db, line, rack_id, rack_face, position, positions=self._positions, transports=self._transports
                )
                if projection is None or not await repository.arrival_matches(
                    db, rotation, projection, rack_id, rack_face
                ):
                    continue
            elif projection.source_transport_task_id != ingress.transport_task_id:
                rotation = await repository.transport(db, decision, DRAIN_RACK_ROTATE_STEP, rack_id, rack_face)
                if rotation is None or not await repository.arrival_matches(
                    db, rotation, projection, rack_id, rack_face
                ):
                    continue
            elif not await repository.arrival_matches(db, ingress, projection, rack_id, rack_face):
                continue
            if await self._batches.has_unclosed_action_for_face(db, line.id, None, None, rack_id, rack_face):
                continue
            if await self._batches.has_unclosed_return_transport_for_face(
                db, workline_id=line.id, rack_id=rack_id, rack_face=rack_face
            ):
                continue
            if not exhausted and await self._drain.return_in_session(
                db,
                line,
                decision,
                rack_id,
                line.position_bindings[OUTLET.slot_key]["location_id"],
                timezone.now_for_db(),
            ):
                return count + 1
            return count + await self._advance_workline_departure(
                db,
                line,
                rack_id=rack_id,
                current_face=projection.arrival_face,
                step=DRAIN_RACK_OUT_STEP,
                picking_task_id=None,
                source_evidence_id=decision.evidence_id,
                arrival_transport_task_id=projection.source_transport_task_id,
                now=timezone.now_for_db(),
            )
        return count

    async def _submit_drain_racks(self, db: Any, line: Any, decision: Any) -> int:
        if not isinstance(decision.result, ReturnBufferDrainReady):
            return 0
        repository = self._drain.repository
        selected = [
            rack
            for rack in decision.result.racks
            if await repository.transport(db, decision, DRAIN_RACK_IN_STEP, rack.rack_id) is None
        ]
        position = line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        created = 0
        for rack in selected:
            if await repository.has_retired_unclosed_inbound(
                db,
                workline_id=line.id,
                rack_id=rack.rack_id,
                target_location_code=position,
                target_face=rack.rack_faces[0],
            ):
                continue
            admission = await self._rack_creator.create_windowed_inbound(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                picking_task_id=None,
                source_evidence_id=decision.evidence_id,
                correlation_id=f"drain:{decision.intent.operation_id}:rack:{rack.rack_id}",
                step=DRAIN_RACK_IN_STEP,
                resource_fence_id=rack.rack_id,
                intent=_DrainRackIntent(
                    rack.rack_id,
                    TransportRackReference(rack.rack_id),
                    TransportRackPosition(position),
                    rack.rack_faces[0],
                ),
            )
            created += admission == "CREATED"
        return created

    async def _advance_current_rack(self, db: Any, line: Any, task: Any) -> int:  # noqa: PLR0911, PLR0912
        bindings = line.position_bindings
        sources = await self._plans.list_bin_source_racks(db, task.id)
        faces_by_rack: dict[str, list[Any]] = {}
        for row in sources:
            faces_by_rack.setdefault(row.rack_id, []).append(row)
        ordered_sources = [row for faces in faces_by_rack.values() for row in faces]
        source_location = bindings[FIVE_RACK.slot_key]["location_id"]
        if not ordered_sources:
            return 0
        clearance_racks = {
            window.resource_fence_id
            for window, _, _ in await self._batches.cancelled_source_windows(
                db, workline_id=line.id, target_location_code=source_location
            )
        }
        manual_arrival = await self._batches.manual_source_arrival(db, workline_id=line.id)
        if manual_arrival is not None and (
            manual_arrival.picking_task_id != task.id
            or manual_arrival.location_code != source_location
            or not isinstance(
                await self._batches.working_rack_position(
                    db, workline_id=line.id, rack_id=manual_arrival.rack_id, location_code=source_location
                ),
                ManualPickingSourceArrival,
            )
        ):
            manual_arrival = None
        current = None
        projection = None
        current_rack_ids: set[str] = set()
        active_faces = {
            (row.rack_id, row.rack_face)
            for row in ordered_sources
            if getattr(row, "cancelled_evidence_id", None) is None
        }
        for row in ordered_sources:
            if getattr(row, "cancelled_evidence_id", None) is not None and (row.rack_id, row.rack_face) in active_faces:
                continue
            if manual_arrival is not None:
                if not matches_source_arrival(
                    manual_arrival, picking_task_id=task.id, source=row, location_code=source_location
                ):
                    continue
                current, projection = row, manual_arrival
                break
            if row.rack_id in clearance_racks:
                continue
            candidate = await ready_rack_projection(
                db,
                line,
                row.rack_id,
                row.rack_face,
                source_location,
                positions=self._positions,
                transports=self._transports,
            )
            if candidate is None or not await self._plans.source_transport_matches(
                db, line.id, row.rack_id, task.id, candidate.source_transport_task_id
            ):
                continue
            current_rack_ids.add(row.rack_id)
            if projection is None or (
                candidate.updated_at is not None
                and (projection.updated_at is None or candidate.updated_at > projection.updated_at)
            ):
                current = row
                projection = candidate
        if len(current_rack_ids) > 1:
            logger.warning(
                "manual_picking.current_source_rack_ambiguous",
                extra={"picking_task_id": task.id, "rack_ids": sorted(current_rack_ids)},
            )
            return 0
        if current is None or projection is None:
            return 0
        if (
            await self._batches.working_rack_position(
                db, workline_id=line.id, rack_id=current.rack_id, location_code=source_location
            )
            is None
        ):
            return 0
        if await self._batches.has_newer_rack_action(
            db, rack_id=current.rack_id, causal_token=getattr(projection, "source_causal_token", None)
        ):
            return 0
        arrived_at = await self._batches.current_rack_arrival_at(db, workline_id=line.id, rack_id=current.rack_id)
        if (
            arrived_at is not None
            and await self._departure_reader.latest_for_workline(db, line.id, current.rack_id, arrived_at=arrived_at)
            is not None
        ):
            return await self._advance_workline_departure(
                db,
                line,
                rack_id=current.rack_id,
                current_face=projection.arrival_face,
                step=SOURCE_RACK_OUT_STEP,
                picking_task_id=task.id,
                arrival_transport_task_id=projection.source_transport_task_id,
                now=timezone.now_for_db(),
            )
        if await self._flow.has_unclosed_action_for_face(
            db, line.id, task.task_id, current.plan_revision, current.rack_id, current.rack_face
        ):
            return 0
        inlet_location = bindings[INLET.slot_key]["location_id"]
        progress = await self._flow.face_progress(
            db, line.id, task.task_id, current.plan_revision, current.rack_id, current.rack_face, inlet_location
        )
        if progress is None or not progress.feed_complete:
            advanced = await self._flow.advance_in_session(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                picking_task_id=task.id,
                task_id=task.task_id,
                plan_revision=current.plan_revision,
                source_evidence_id=current.source_evidence_id,
                rack_id=current.rack_id,
                rack_face=current.rack_face,
                return_location=bindings[OUTLET.slot_key]["location_id"],
                inlet_location=inlet_location,
                now=timezone.now_for_db(),
                allow_inbound=getattr(current, "cancelled_evidence_id", None) is None,
            )
            if advanced or getattr(current, "cancelled_evidence_id", None) is None:
                return int(advanced)
        service_finished_at = getattr(progress, "last_chunk_created_at", None)
        next_rotation = None
        for next_face in faces_by_rack[current.rack_id]:
            if next_face is current:
                continue
            if getattr(next_face, "cancelled_evidence_id", None) is not None:
                continue
            next_progress = await self._flow.face_progress(
                db,
                line.id,
                task.task_id,
                next_face.plan_revision,
                next_face.rack_id,
                next_face.rack_face,
                inlet_location,
            )
            if next_progress is not None and next_progress.feed_complete:
                if next_face.rack_face == projection.arrival_face:
                    finished_at = getattr(next_progress, "last_chunk_created_at", None)
                    if finished_at is not None:
                        service_finished_at = (
                            max(service_finished_at, finished_at) if service_finished_at else finished_at
                        )
                continue
            if next_face.rack_face == projection.arrival_face:
                return int(
                    await self._flow.advance_in_session(
                        db,
                        workline_id=line.id,
                        workline_code=line.line_code,
                        picking_task_id=task.id,
                        task_id=task.task_id,
                        plan_revision=next_face.plan_revision,
                        source_evidence_id=next_face.source_evidence_id,
                        rack_id=next_face.rack_id,
                        rack_face=next_face.rack_face,
                        return_location=bindings[OUTLET.slot_key]["location_id"],
                        inlet_location=inlet_location,
                        now=timezone.now_for_db(),
                        allow_inbound=next_progress is None,
                    )
                )
            if next_progress is not None:
                return 0
            if next_rotation is None:
                next_rotation = next_face
        returned = await self._return_before_rack_motion(
            db, line, current.rack_id, projection.arrival_face, timezone.now_for_db(), after=service_finished_at
        )
        if returned is not None:
            return returned
        if next_rotation is not None:
            next_face = next_rotation
            if await self._batches.has_unclosed_return_dependency_for_rack(
                db, workline_id=line.id, rack_id=current.rack_id
            ):
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
            step=SOURCE_RACK_OUT_STEP,
            picking_task_id=task.id,
            arrival_transport_task_id=projection.source_transport_task_id,
            now=timezone.now_for_db(),
        )

    async def _advance_return_rack(self, db: Any, line: Any, task: Any) -> int:  # noqa: PLR0911, PLR0912
        """取货由 PDA 完成；WES 按到位及 WMS 面级或整架业务完成事实推进换面、离场。"""
        location = (line.position_bindings.get(RETURN_RACK.slot_key) or {}).get("location_id")
        if not location or self._arrival_scheduler is None or self._arrival_reader is None:
            return 0
        faces_by_rack: dict[str, list[Any]] = {}
        for row in await self._plans.list_active_direct_picks(db, task.id):
            # 同面多个 slot_id 只占一个换面位置。
            faces = faces_by_rack.setdefault(row.rack_id, [])
            if all((face.plan_revision, face.rack_face) != (row.plan_revision, row.rack_face) for face in faces):
                faces.append(row)
        ordered = [row for faces in faces_by_rack.values() for row in faces]
        if not ordered:
            return 0
        current = projection = None
        current_rack_id = None
        for row in ordered:
            candidate = await ready_rack_projection(
                db, line, row.rack_id, row.rack_face, location, positions=self._positions, transports=self._transports
            )
            if candidate is None:
                continue
            arrival_source_id = await self._plans.return_rack_transport_source(
                db, line.id, task.id, row.rack_id, candidate.source_transport_task_id
            )
            if arrival_source_id is not None:
                if current_rack_id is not None and current_rack_id != row.rack_id:
                    logger.warning(
                        "manual_picking.current_return_rack_ambiguous",
                        extra={"picking_task_id": task.id, "rack_ids": sorted((current_rack_id, row.rack_id))},
                    )
                    return 0
                if current_rack_id is not None:
                    continue
                current_rack_id = row.rack_id
                projection = candidate
                matching_faces = [
                    face for face in faces_by_rack[row.rack_id] if face.rack_face == candidate.arrival_face
                ]
                current = matching_faces[0] if matching_faces else None
                for face in matching_faces:
                    if not await self._plans.is_direct_pick_face_business_closed(
                        db,
                        picking_task_id=task.id,
                        plan_revision=face.plan_revision,
                        rack_id=face.rack_id,
                        rack_face=face.rack_face,
                    ):
                        current = face
                        break
        if current is None or projection is None:
            return 0
        if await self._batches.has_newer_rack_action(
            db, rack_id=current.rack_id, causal_token=getattr(projection, "source_causal_token", None)
        ):
            return 0
        now = timezone.now_for_db()
        await self._batches.supersede_return_rack_requests(
            db,
            workline_id=line.id,
            rack_id=current.rack_id,
            round_started_at=await self._batches.current_round_started_at(
                db, workline_id=line.id, rack_id=current.rack_id
            ),
            now=now,
        )
        snapshot = await self._arrival_reader.latest(db, task.id, current.rack_id, projection.source_transport_task_id)
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
        if snapshot.status in {WmsConfirmationStatus.RECONCILING, WmsConfirmationStatus.SUPERSEDED}:
            logger.warning(
                "manual_picking.return_rack_arrival_stuck", extra={"operation_id": snapshot.intent.operation_id}
            )
        elif snapshot.status == WmsConfirmationStatus.COMPLETED and (
            snapshot.outcome is None or not isinstance(snapshot.outcome.result, FactRecorded)
        ):
            logger.warning(
                "manual_picking.return_rack_arrival_not_recorded", extra={"operation_id": snapshot.intent.operation_id}
            )
        arrived_at = await self._batches.current_rack_arrival_at(db, workline_id=line.id, rack_id=current.rack_id)
        if (
            arrived_at is not None
            and await self._departure_reader.latest_for_workline(db, line.id, current.rack_id, arrived_at=arrived_at)
            is not None
        ):
            return await self._advance_workline_departure(
                db,
                line,
                rack_id=current.rack_id,
                current_face=projection.arrival_face,
                step=RETURN_RACK_OUT_STEP,
                picking_task_id=task.id,
                arrival_transport_task_id=projection.source_transport_task_id,
                now=now,
                current_location=location,
            )
        if not await self._plans.is_direct_pick_face_business_closed(
            db,
            picking_task_id=task.id,
            plan_revision=current.plan_revision,
            rack_id=current.rack_id,
            rack_face=current.rack_face,
        ):
            return 0
        if await self._batches.has_current_rack_dependency(
            db,
            line.id,
            current.rack_id,
            await self._batches.current_round_started_at(db, workline_id=line.id, rack_id=current.rack_id),
        ):
            return 0
        # 到位面由外部搬运决定，不保证是计划首面；遍历全部面而非 index 之后的切片，
        # 否则更早的未结面会被静默跳过。
        for next_face in faces_by_rack[current.rack_id]:
            if next_face is current or await self._plans.is_direct_pick_face_business_closed(
                db,
                picking_task_id=task.id,
                plan_revision=next_face.plan_revision,
                rack_id=current.rack_id,
                rack_face=next_face.rack_face,
            ):
                continue
            if next_face.rack_face == projection.arrival_face:
                return 0
            if await self._batches.has_unclosed_return_dependency_for_rack(
                db, workline_id=line.id, rack_id=current.rack_id
            ):
                return 0
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
            step=RETURN_RACK_OUT_STEP,
            picking_task_id=task.id,
            arrival_transport_task_id=projection.source_transport_task_id,
            now=now,
            current_location=location,
        )

    async def _advance_workline_departure(  # noqa: PLR0911
        self,
        db: Any,
        line: Any,
        *,
        rack_id: str,
        current_face: str | None,
        step: str,
        picking_task_id: int | None,
        arrival_transport_task_id: str,
        now: Any,
        source_evidence_id: int | None = None,
        current_location: str | None = None,
        round_started_at: Any = None,
        rejected_window: Any = None,
    ) -> int:
        location = current_location or line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        current_position = await self._batches.working_rack_position(
            db, workline_id=line.id, rack_id=rack_id, location_code=location
        )
        if current_position is None or current_position.arrival_face != current_face:
            return 0
        confirmed_arrival_id = (
            current_position.source_transport_task_id
            if isinstance(current_position, ManualPickingSourceArrival)
            else None
        )
        round_scope = {} if round_started_at is None else {"round_started_at": round_started_at}
        if step == RETURN_RACK_OUT_STEP:
            await self._batches.supersede_return_rack_requests(
                db,
                workline_id=line.id,
                rack_id=rack_id,
                round_started_at=round_started_at
                or await self._batches.current_round_started_at(db, workline_id=line.id, rack_id=rack_id),
                now=now,
            )
        if (
            step == RETURN_RACK_OUT_STEP
            and await self._batches.has_current_rack_dependency(
                db,
                line.id,
                rack_id,
                round_started_at
                or await self._batches.current_round_started_at(db, workline_id=line.id, rack_id=rack_id),
            )
        ) or await self._batches.has_unclosed_return_dependency_for_rack(
            db, workline_id=line.id, rack_id=rack_id, **round_scope
        ):
            return 0
        location = current_location or line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        arrival = await self._transports.get_task(db, arrival_transport_task_id)
        # 拒绝不会建立新进场轮次；取消窗口和退役 drain 仍按原窗口读取决定。
        arrived_at = arrival.created_at if rejected_window is None else rejected_window.created_at
        snapshot = await self._departure_reader.latest_for_workline(db, line.id, rack_id, arrived_at=arrived_at)
        if step != RETURN_RACK_OUT_STEP:
            returned = await self._return_before_rack_motion(
                db,
                line,
                rack_id,
                current_face,
                now,
                round_started_at=round_started_at,
                location_code=location,
            )
            if returned is not None:
                return returned
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
        if step in {SOURCE_RACK_OUT_STEP, DRAIN_RACK_OUT_STEP} and await self._batches.has_current_rack_dependency(
            db, line.id, rack_id, arrival.created_at, arrival_transport_task_id=confirmed_arrival_id
        ):
            return 0
        departure_identity = f"departure:{snapshot.intent.operation_id}"
        rejected = await self._departure_rejected(db, line.id, departure_identity, step)
        if rejected is not None:
            return await self._redecide_rejected_departure(
                db,
                line,
                rejected,
                scope_identity=(
                    f"window:{await self._batches.current_round_client_request_id(db, workline_id=line.id, rack_id=rack_id)}"
                    if rejected_window is None
                    else f"window:{rejected_window.client_request_id}"
                ),
                rack_id=rack_id,
                step=step,
                location=location,
                face=current_face,
                now=now,
            )
        await self._rack_creator.create_source_return(
            db,
            workline_id=line.id,
            picking_task_id=picking_task_id,
            source_evidence_id=snapshot.evidence_id if source_evidence_id is None else source_evidence_id,
            correlation_id=departure_identity,
            step=step,
            rack_id=rack_id,
            destination=result.rack_destination,
            rcs_template_id=(
                TransportRcsTemplateId.F01 if step == RETURN_RACK_OUT_STEP else TransportRcsTemplateId.CTU03
            ),
        )
        return 1

    async def _return_before_rack_motion(
        self,
        db: Any,
        line: Any,
        rack_id: str,
        rack_face: str | None,
        now: Any,
        *,
        round_started_at: Any = None,
        after: Any = None,
        location_code: str | None = None,
    ) -> int | None:
        location = location_code or line.position_bindings[FIVE_RACK.slot_key]["location_id"]
        position = await self._batches.working_rack_position(
            db, workline_id=line.id, rack_id=rack_id, location_code=location
        )
        if position is None or position.arrival_face != rack_face:
            return 0
        confirmed_arrival_id = (
            position.source_transport_task_id if isinstance(position, ManualPickingSourceArrival) else None
        )
        if round_started_at is None:
            round_started_at = await self._batches.current_round_started_at(db, workline_id=line.id, rack_id=rack_id)
        if await self._batches.has_current_rack_dependency(
            db, line.id, rack_id, round_started_at, arrival_transport_task_id=confirmed_arrival_id
        ) or await self._batches.has_unclosed_return_dependency_for_rack(
            db, workline_id=line.id, rack_id=rack_id, round_started_at=round_started_at
        ):
            return 0
        arrived_at = await self._batches.current_rack_arrival_at(db, workline_id=line.id, rack_id=rack_id)
        if arrived_at is None:
            return 0
        if await self._batches.has_newer_rack_action(
            db, rack_id=rack_id, causal_token=getattr(position, "source_causal_token", None)
        ):
            return 0
        if await self._departure_reader.latest_for_workline(db, line.id, rack_id, arrived_at=arrived_at) is not None:
            return None
        return await self._flow.return_before_motion(
            db,
            workline_id=line.id,
            workline_code=line.line_code,
            rack_id=rack_id,
            rack_face=rack_face,
            return_location=line.position_bindings[OUTLET.slot_key]["location_id"],
            arrived_at=max(arrived_at, after) if after is not None else arrived_at,
            now=now,
        )

    async def _advance_transfer_departure(self, db: Any, line: Any, task: Any, now: Any) -> int:  # noqa: PLR0911
        if task.target_rack_id is None:
            return 0
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
        returned = await self._return_before_rack_motion(
            db,
            line,
            task.target_rack_id,
            projection.arrival_face,
            now,
            location_code=current_location,
        )
        if returned is not None:
            return returned
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
                rejected = await self._departure_rejected(
                    db, line.id, snapshot.intent.operation_id, TRANSFER_RACK_OUT_STEP
                )
                if rejected is not None:
                    return await self._redecide_rejected_departure(
                        db,
                        line,
                        rejected,
                        scope_identity=f"task:{task.id}:rack:{task.target_rack_id}",
                        rack_id=task.target_rack_id,
                        step=TRANSFER_RACK_OUT_STEP,
                        location=current_location,
                        face=projection.arrival_face,
                        now=now,
                        picking_task=task,
                    )
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

    async def _departure_rejected(
        self, db: Any, workline_id: int, correlation_id: str, step: str
    ) -> tuple[Any, Any] | None:
        binding = await self._bindings.get_by_decision_identity_for_update(
            db, workline_id=workline_id, correlation_id=correlation_id, step=step
        )
        if binding is None:
            return None
        task = await self._transports.get_task_by_client_request(db, binding.client_request_id)
        return (binding, task) if task is not None and task.status == "REJECTED" else None

    async def _redecide_rejected_departure(
        self,
        db: Any,
        line: Any,
        rejected: tuple[Any, Any],
        *,
        scope_identity: str,
        rack_id: str,
        step: str,
        location: str,
        face: str | None,
        now: Any,
        picking_task: Any = None,
    ) -> int:
        binding, transport = rejected
        if transport.outcome_version < 1 or transport.outcome_version != transport.published_outcome_version:
            return 0
        if binding.picking_task_id is not None:
            owner = await self._tasks.get_by_id_for_update(db, binding.picking_task_id)
            if owner is None or owner.status == "ARCHIVED":
                return 0
        if step in {DRAIN_RACK_OUT_STEP, RETIRED_DRAIN_RACK_OUT_STEP}:
            if self._drain is None:
                return 0
            execution = await self._drain.repository.executions.for_evidence(
                db, workline_id=line.id, evidence_id=binding.source_evidence_id
            )
            allowed = {"EXCEPTION_RETIRED"} if step == RETIRED_DRAIN_RACK_OUT_STEP else {"ACTIVE", "COMPLETED"}
            if execution is None or execution.status not in allowed:
                return 0
        prior_attempt = (
            int(binding.correlation_id.split(":", 2)[1]) if binding.correlation_id.startswith("retry:") else 0
        )
        cursor = await self._departure_retries.observe_rejection(
            db,
            workline_id=line.id,
            scope_identity=scope_identity,
            rack_id=rack_id,
            step=step,
            transport_task_id=transport.transport_task_id,
            causal_token=binding.causal_token,
            prior_attempt=prior_attempt,
            now=now,
        )
        if cursor is None or cursor.decision_operation_id is not None or now < cursor.next_attempt_at:
            return 0
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=self._uuid_factory(),
            task_id=picking_task.task_id if picking_task is not None else None,
            rack_id=rack_id,
            current_location=TransportRackPosition(location),
            current_face=face,
        )
        owner_kwargs = {"picking_task_id": picking_task.id} if picking_task is not None else {"workline_id": line.id}
        await self._departure_scheduler.create_in_session(db, intent, created_at=now, **owner_kwargs)
        await self._departure_retries.record_decision(db, cursor, intent.operation_id, now)
        return 1


__all__ = [
    "RETURN_RACK_OUT_STEP",
    "RETURN_RACK_ROTATE_STEP",
    "SOURCE_RACK_OUT_STEP",
    "SOURCE_RACK_ROTATE_STEP",
    "TRANSFER_RACK_OUT_STEP",
    "ManualPickingBatchDriver",
    "pending_first_source_candidates",
]
