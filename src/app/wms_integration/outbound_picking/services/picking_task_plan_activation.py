"""把已提交 PickingTask 计划交给冻结插件，并可靠创建货架 Transport。"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanHandlingResult,
    PickingTaskPlanRack,
    PositionBindingSnapshot,
)

from src.app.execution.repositories import transport_decision_binding_repository
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import picking_task_repository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.app.workline.repositories import workline_repository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
    from src.app.workline.installed_plugin import InstalledWorkLinePlugin

_PLAN_BATCH_LIMIT = 100
TARGET_RACK_IN_STEP = "PICKING_TASK_TARGET_RACK_IN"
BIN_SOURCE_RACK_IN_STEP = "PICKING_TASK_BIN_SOURCE_RACK_IN"


class PickingTaskPlanActivationService:
    """宿主拥有任务领取和可靠对象；插件只把 fact 映射为 intent。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        transport_creator: ReliableRackTransportCreator,
        workline_repository: Any = workline_repository,
        task_repository: Any = picking_task_repository,
        plan_repository: Any = None,
        transport_binding_repository: Any = transport_decision_binding_repository,
        workline_reserved: Callable[[AsyncSession, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._worklines = workline_repository
        self._tasks = task_repository
        self._plans = plan_repository or PickingTaskPlanDeltaRepository()
        self._bindings = transport_binding_repository
        self._transport_creator = transport_creator
        self._workline_reserved = workline_reserved
        self._handlers = {
            (plugin.plugin_key, plugin.plugin_version): plugin.picking_task_plan_applied_handler
            for plugin in plugins
            if plugin.picking_task_plan_applied_handler is not None
        }
        self._batch_drivers = {
            (plugin.plugin_key, plugin.plugin_version): driver
            for plugin in plugins
            if (driver := getattr(plugin, "picking_task_batch_driver", None)) is not None
        }
        self._completion_drivers = {
            (plugin.plugin_key, plugin.plugin_version): driver
            for plugin in plugins
            if (driver := getattr(plugin, "picking_task_completion_driver", None)) is not None
        }

    @property
    def plugin_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._handlers))

    async def activate_batch(self, *, limit: int = _PLAN_BATCH_LIMIT) -> int:
        if type(limit) is not int or limit != _PLAN_BATCH_LIMIT:
            raise ValueError(f"plan activation batch limit must be {_PLAN_BATCH_LIMIT}")
        identities = self.plugin_identities
        if not identities:
            return 0
        async with self._sessions.begin() as db:
            worklines = await self._worklines.list_active_for_plugin_identities(db, identities, limit=limit)
        created = 0
        for workline_id, plugin_key, plugin_version in worklines:
            created += await self._activate_workline(
                workline_id,
                handler=self._handlers[(plugin_key, plugin_version)],
                plugin_identity=(plugin_key, plugin_version),
            )
        return created

    async def _activate_workline(self, workline_id: int, *, handler: Any, plugin_identity: tuple[str, str]) -> int:
        async with self._sessions.begin() as db:
            line = await self._worklines.get_for_update(db, workline_id)
            if (
                line is None
                or not line.is_active
                or line.is_deleted
                or (line.plugin_key, line.plugin_version) != plugin_identity
            ):
                return 0
            if self._workline_reserved is not None and await self._workline_reserved(db, workline_id):
                return 0
            task = await self._tasks.get_executing_for_workline_for_update(db, workline_id)
            if task is None:
                driver = self._batch_drivers.get(plugin_identity)
                return await driver.advance_completed_in_session(db, line) if driver is not None else 0
            if (
                task.status != PickingTaskStatus.EXECUTING
                or task.plan_blocked_evidence_id is not None
                or task.last_applied_plan_revision < 1
            ):
                return 0
            steps = (TARGET_RACK_IN_STEP, BIN_SOURCE_RACK_IN_STEP)
            decided_racks = await self._bindings.list_task_resource_fence_ids(
                db,
                workline_id=workline_id,
                picking_task_id=task.id,
                steps=steps,
            )
            pending_racks = await self._pending_bin_racks(db, task, decided_racks)
            target_rack = (
                PickingTaskPlanRack(
                    rack_id=task.target_rack_id,
                    rack_faces=(task.target_rack_face,),
                    source_evidence_id=str(task.initial_plan_evidence_id),
                    plan_revision=1,
                )
                if task.target_rack_id not in decided_racks
                else None
            )
            fact = PickingTaskPlanAppliedFact(
                fact_id=f"picking-task-plan:{task.id}:{task.last_applied_plan_revision}",
                evidence_id=str(task.last_plan_evidence_id),
                fact_version="1.0",
                task_id=task.task_id,
                plan_revision=task.last_applied_plan_revision,
                target_rack=target_rack,
                pending_bin_source_racks=pending_racks,
                position_bindings=tuple(
                    PositionBindingSnapshot(position_role=role, **binding)
                    for role, binding in sorted(line.position_bindings.items())
                ),
            )
            result = handler(fact)
            self._validate_result(fact, result)
            for intent in result.transports:
                step = (
                    TARGET_RACK_IN_STEP
                    if fact.target_rack is not None and intent.rack_id == fact.target_rack.rack_id
                    else BIN_SOURCE_RACK_IN_STEP
                )
                _ = await self._transport_creator.create(
                    db,
                    workline_id=workline_id,
                    source_evidence_id=int(intent.source_evidence_id),
                    correlation_id=(f"pt:{task.id}:e:{intent.source_evidence_id}:rack:{intent.rack_id}"),
                    step=step,
                    resource_fence_id=intent.rack_id,
                    intent=intent,
                )
            driver = self._batch_drivers.get(plugin_identity)
            completion = self._completion_drivers.get(plugin_identity)
            batch_count = await driver.advance_in_session(db, line, task) if driver is not None else 0
            completion_count = await completion.advance_in_session(db, line, task) if completion is not None else 0
            return len(result.transports) + batch_count + completion_count

    async def _pending_bin_racks(self, db: Any, task: Any, decided_racks: set[str]) -> tuple[PickingTaskPlanRack, ...]:
        rows = await self._plans.list_bin_source_racks(db, task.id)
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            if row.rack_id not in decided_racks:
                grouped[row.rack_id].append(row)
        ordered = sorted(grouped.items(), key=lambda item: (min(row.plan_revision for row in item[1]), item[0]))
        return tuple(
            PickingTaskPlanRack(
                rack_id=rack_id,
                rack_faces=tuple(sorted({row.rack_face for row in rack_rows})),
                source_evidence_id=str(
                    min(rack_rows, key=lambda row: (row.plan_revision, getattr(row, "id", 0) or 0)).source_evidence_id
                ),
                plan_revision=min(row.plan_revision for row in rack_rows),
            )
            for rack_id, rack_rows in ordered
        )

    @staticmethod
    def _validate_result(fact: PickingTaskPlanAppliedFact, result: object) -> None:
        if type(result) is not PickingTaskPlanHandlingResult:
            raise TypeError("plan handler must return PickingTaskPlanHandlingResult")
        candidates = {rack.rack_id: rack for rack in ((fact.target_rack,) if fact.target_rack is not None else ())}
        candidates.update({rack.rack_id: rack for rack in fact.pending_bin_source_racks})
        seen: set[str] = set()
        bindings = {binding.position_role: binding for binding in fact.position_bindings}
        for intent in result.transports:
            candidate = candidates.get(intent.rack_id)
            if (
                candidate is None
                or intent.rack_id in seen
                or intent.task_id != fact.task_id
                or intent.fact_id != fact.fact_id
                or intent.source_evidence_id != candidate.source_evidence_id
            ):
                raise ValueError("plan handler returned an intent outside the frozen fact")
            binding = bindings.get(intent.position_role)
            if (
                binding is None
                or binding.location_type != "RACK_POSITION"
                or intent.target.location_code != binding.location_id
                or intent.target_face not in candidate.rack_faces
            ):
                raise ValueError("plan handler returned an intent outside the frozen fact")
            seen.add(intent.rack_id)
        if seen != set(candidates):
            raise ValueError("plan handler omitted a pending rack")


__all__ = [
    "BIN_SOURCE_RACK_IN_STEP",
    "TARGET_RACK_IN_STEP",
    "PickingTaskPlanActivationService",
]
