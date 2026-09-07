"""按持久化 WorkLine 精确选择插件专属的后续处理。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.execution.repositories.material_execution_repository import material_execution_repository
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.transport.contracts import TRANSPORT_DEBUG_CALLER_WORKLINE_ID
from src.app.transport.repository import TransportRepository
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin_version
from src.app.workline.repositories.workline_repository import workline_repository
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.execution.models import WmsConfirmation
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationFollowUp
    from src.app.transport.contracts import TransportOutcome


class WorkLineRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, workline_id: int) -> Any | None: ...


class MaterialExecutionRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, execution_id: int) -> Any | None: ...


class TransportBindingRepositoryPort(Protocol):
    async def get_by_client_request_id(self, db: Any, client_request_id: str) -> Any | None: ...


class InstalledPluginWmsFollowUpPlanner:
    """从 WMS confirmation 的原 execution/WorkLine 选择后继规划器。"""

    def __init__(
        self,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        *,
        execution_repository: MaterialExecutionRepositoryPort = cast(
            "MaterialExecutionRepositoryPort", material_execution_repository
        ),
        workline_repository: WorkLineRepositoryPort = cast("WorkLineRepositoryPort", workline_repository),
    ) -> None:
        self._plugins = plugins
        self._executions = execution_repository
        self._worklines = workline_repository

    async def plan(
        self,
        db: object,
        confirmation: WmsConfirmation,
        *,
        response_result: str,
        retry_after_ms: int,
        received_at: datetime,
    ) -> WmsConfirmationFollowUp | None:
        execution_id = confirmation.material_execution_id
        if execution_id is None:
            raise ValueError("WMS follow-up confirmation 缺少 MaterialExecution owner")
        execution = await self._executions.get_by_id(db, execution_id)
        if execution is None:
            raise LookupError("WMS follow-up MaterialExecution 不存在")
        workline = await self._worklines.get_by_id(db, execution.workline_id)
        if workline is None:
            raise LookupError("WMS follow-up WorkLine 不存在")
        plugin = resolve_installed_plugin_version(self._plugins, workline.plugin_key, workline.plugin_version)
        planner = plugin.wms_confirmation_follow_up_planner
        if planner is None:
            raise LookupError(f"plugin has no WMS follow-up planner: {plugin.plugin_key}@{plugin.plugin_version}")
        return await planner.plan(
            db,
            confirmation,
            response_result=response_result,
            retry_after_ms=retry_after_ms,
            received_at=received_at,
        )


class InstalledPluginTransportOutcomePublisher:
    """从 Transport binding 的原 WorkLine 选择 outcome publisher。"""

    def __init__(
        self,
        session_factory: Any,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        *,
        transport_repository: TransportRepository | None = None,
        binding_repository: TransportBindingRepositoryPort = cast(
            "TransportBindingRepositoryPort", transport_decision_binding_repository
        ),
        workline_repository: WorkLineRepositoryPort = cast("WorkLineRepositoryPort", workline_repository),
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self._queue = queue_gateway
        self._sessions = session_factory
        self._plugins = plugins
        self._tasks = transport_repository or TransportRepository()
        self._bindings = binding_repository
        self._worklines = workline_repository

    async def publish(self, outcome: TransportOutcome) -> None:
        if outcome.caller.workline_id == TRANSPORT_DEBUG_CALLER_WORKLINE_ID:
            return
        async with self._sessions.begin() as db:
            task = await self._tasks.get_task(db, outcome.transport_task_id, for_update=True)
            if task is None or task.client_request_id != outcome.client_request_id:
                raise LookupError("Transport outcome 缺少匹配原任务")
            if task.published_outcome_version >= outcome.outcome_version:
                return
            binding = await self._bindings.get_by_client_request_id(db, outcome.client_request_id)
            if binding is None:
                raise LookupError("Transport outcome 缺少业务 binding")
            workline = await self._worklines.get_by_id(db, binding.workline_id)
            if workline is None:
                raise LookupError("Transport outcome WorkLine 不存在")
            plugin = resolve_installed_plugin_version(self._plugins, workline.plugin_key, workline.plugin_version)
            publisher = plugin.transport_outcome_publisher
            if publisher is None:
                raise LookupError(
                    f"plugin has no Transport outcome publisher: {plugin.plugin_key}@{plugin.plugin_version}"
                )
            # Share the transaction: workers have one database connection. Keep the task
            # locked through evidence commit so another publisher cannot admit a plugin switch.
            should_wake = await publisher.publish(db, outcome)
        if should_wake:
            try:
                self._queue.enqueue_execution_facts()
            except Exception:
                logger.exception(
                    "transport.execution_wake_failed",
                    extra={"transport_task_id": outcome.transport_task_id},
                )


__all__ = ["InstalledPluginTransportOutcomePublisher", "InstalledPluginWmsFollowUpPlanner"]
