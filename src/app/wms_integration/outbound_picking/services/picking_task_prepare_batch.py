"""按已安装业务能力驱动 PickingTask prepare。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.repositories import workline_repository as default_workline_repository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.core.task_queue_gateway import TaskQueueGateway

_PREPARE_BATCH_LIMIT = 100


class PickingTaskPrepareBatchService:
    """宿主只选择活动插件上下文；具体准入由插件 Policy 决定。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        task_queue_gateway: TaskQueueGateway,
        workline_repository: WorkLineRepository | None = None,
        workline_reserved: Callable[[AsyncSession, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._task_queue = task_queue_gateway
        self._worklines = workline_repository or default_workline_repository
        self._workline_reserved = workline_reserved
        self._policies = {
            (plugin.plugin_key, plugin.plugin_version): plugin.picking_task_prepare_policy
            for plugin in plugins
            if plugin.picking_task_prepare_policy is not None
        }
        self._business_blockers = {
            (plugin.plugin_key, plugin.plugin_version): getattr(plugin, "business_blocker", None)
            for plugin in plugins
            if plugin.picking_task_prepare_policy is not None
        }

    @property
    def plugin_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._policies))

    async def prepare_batch(self, *, limit: int = _PREPARE_BATCH_LIMIT) -> int:
        if type(limit) is not int or limit != _PREPARE_BATCH_LIMIT:
            raise ValueError(f"prepare batch limit must be {_PREPARE_BATCH_LIMIT}")
        identities = self.plugin_identities
        if not identities:
            return 0
        async with self._sessions.begin() as db:
            worklines = await self._worklines.list_active_for_plugin_identities(db, identities, limit=limit)

        coordinators: dict[tuple[str, str], PickingTaskPrepareCoordinator] = {}
        prepared = 0
        for workline_id, plugin_key, plugin_version in worklines:
            identity = (plugin_key, plugin_version)
            policy = self._policies[identity]
            coordinator = coordinators.get(identity)
            if coordinator is None:
                coordinator = PickingTaskPrepareCoordinator(
                    self._sessions,
                    policy=policy,
                    workline_repository=self._worklines,
                    task_queue_gateway=self._task_queue,
                    workline_reserved=self._workline_reserved,
                    business_blocker=self._business_blockers[identity],
                )
                coordinators[identity] = coordinator
            result = await coordinator.prepare_next_for_workline(workline_id)
            prepared += int(result.prepared)
        return prepared


__all__ = ["PickingTaskPrepareBatchService"]
