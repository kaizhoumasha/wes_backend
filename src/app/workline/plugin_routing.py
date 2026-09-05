"""按持久化 Epoch 精确选择插件专属的后续处理。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.execution.repositories.material_execution_repository import material_execution_repository
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.transport.contracts import TRANSPORT_DEBUG_CALLER_WORKLINE_ID
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin_version
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.execution.models import WmsConfirmation
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationFollowUp
    from src.app.transport.contracts import TransportOutcome


class EpochRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, epoch_id: int) -> Any | None: ...


class MaterialExecutionRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, execution_id: int) -> Any | None: ...


class TransportBindingRepositoryPort(Protocol):
    async def get_by_client_request_id(self, db: Any, client_request_id: str) -> Any | None: ...


class InstalledPluginWmsFollowUpPlanner:
    """从 WMS confirmation 的原 execution/Epoch 选择后继规划器。"""

    def __init__(
        self,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        *,
        execution_repository: MaterialExecutionRepositoryPort = cast(
            "MaterialExecutionRepositoryPort", material_execution_repository
        ),
        epoch_repository: EpochRepositoryPort = cast("EpochRepositoryPort", line_run_epoch_repository),
    ) -> None:
        self._plugins = plugins
        self._executions = execution_repository
        self._epochs = epoch_repository

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
        epoch = await self._epochs.get_by_id(db, execution.line_run_epoch_id)
        if epoch is None:
            raise LookupError("WMS follow-up Epoch 不存在")
        plugin = resolve_installed_plugin_version(self._plugins, epoch.plugin_key, epoch.plugin_version)
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
    """从 Transport binding 的原 Epoch 选择 outcome publisher。"""

    def __init__(
        self,
        session_factory: Any,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        *,
        binding_repository: TransportBindingRepositoryPort = cast(
            "TransportBindingRepositoryPort", transport_decision_binding_repository
        ),
        epoch_repository: EpochRepositoryPort = cast("EpochRepositoryPort", line_run_epoch_repository),
    ) -> None:
        self._sessions = session_factory
        self._plugins = plugins
        self._bindings = binding_repository
        self._epochs = epoch_repository

    async def publish(self, outcome: TransportOutcome) -> None:
        if outcome.caller.workline_id == TRANSPORT_DEBUG_CALLER_WORKLINE_ID:
            return
        async with self._sessions.begin() as db:
            binding = await self._bindings.get_by_client_request_id(db, outcome.client_request_id)
            if binding is None:
                raise LookupError("Transport outcome 缺少业务 binding")
            epoch = await self._epochs.get_by_id(db, binding.line_run_epoch_id)
            if epoch is None:
                raise LookupError("Transport outcome Epoch 不存在")
            plugin = resolve_installed_plugin_version(self._plugins, epoch.plugin_key, epoch.plugin_version)
            publisher = plugin.transport_outcome_publisher
            if publisher is None:
                raise LookupError(
                    f"plugin has no Transport outcome publisher: {plugin.plugin_key}@{plugin.plugin_version}"
                )
        await publisher.publish(outcome)


__all__ = ["InstalledPluginTransportOutcomePublisher", "InstalledPluginWmsFollowUpPlanner"]
