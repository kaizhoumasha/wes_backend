"""通用 WorkLine START 与历史 Epoch replay。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin
from src.app.workline.models.workline import LineType
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.workline_repository import workline_repository
from src.app.workline.services.line_run_epoch_service import LineRunEpochService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.workline.epoch_activation import (
        LineRunEpochDeviceBindingInput,
        LineRunEpochPositionBindingInput,
    )
    from src.app.workline.models.line_run_epoch import LineRunEpoch


class WorkLineStartNotFoundError(LookupError):
    """首次 START 找不到未删除 WorkLine。"""


class WorkLineStartInvalidStateError(ValueError):
    """WorkLine 当前通用运行门禁不允许首次 START。"""


class WorkLineStartConfigurationError(ValueError):
    """业务 builder 判定配置或必需 Device 无效。"""


class WorkLineStartIdempotencyConflictError(ValueError):
    """全局 request identity 已属于其他 WorkLine。"""


@dataclass(frozen=True, slots=True)
class WorkLineStartResult:
    epoch: LineRunEpoch
    current_workline_runtime_status: str | None
    created: bool


class EpochRepositoryPort(Protocol):
    async def lock_start_request(self, db: Any, request_id: str) -> None: ...

    async def get_by_epoch_code_for_update(self, db: Any, epoch_code: str) -> LineRunEpoch | None: ...

    async def get_active_for_workline_for_update(self, db: Any, workline_id: int) -> LineRunEpoch | None: ...

    async def has_active_epoch(self, db: Any) -> bool: ...

    async def add_complete_epoch(
        self,
        db: Any,
        epoch: LineRunEpoch,
        device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
    ) -> LineRunEpoch: ...

    async def close_epoch(self, db: Any, epoch: LineRunEpoch, *, closed_at: datetime) -> LineRunEpoch: ...


class WorkLineRepositoryPort(Protocol):
    async def get_for_update(self, db: Any, workline_id: int) -> Any | None: ...

    async def get_unfinished_workload_summary(self, db: Any, workline_id: int) -> dict[str, Any]: ...

    async def set_active_for_start(self, db: Any, workline: Any) -> Any: ...


class SafetyRepositoryPort(Protocol):
    async def get_active_for_workline(self, db: Any, workline_id: int) -> Any | None: ...


class EpochServicePort(Protocol):
    async def activate_epoch(self, db: Any, **kwargs: Any) -> LineRunEpoch: ...


class WorkLineStartService:
    """在调用方事务内分类 replay 或创建完整 Epoch。"""

    def __init__(
        self,
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        epoch_repository: EpochRepositoryPort = cast("EpochRepositoryPort", line_run_epoch_repository),
        workline_repository: WorkLineRepositoryPort = cast("WorkLineRepositoryPort", workline_repository),
        safety_repository: SafetyRepositoryPort = cast("SafetyRepositoryPort", workline_safety_incident_repository),
        epoch_service: EpochServicePort | None = None,
        clock: Any = timezone.now_for_db,
    ) -> None:
        self._plugins = plugins
        self._epochs = epoch_repository
        self._worklines = workline_repository
        self._safety = safety_repository
        self._epoch_service = epoch_service or LineRunEpochService(repository=cast("Any", epoch_repository))
        self._clock = clock

    async def start(self, db: Any, *, workline_id: int, request_id: str) -> WorkLineStartResult:
        normalized_request_id = request_id.strip()
        await self._epochs.lock_start_request(db, normalized_request_id)
        existing = await self._epochs.get_by_epoch_code_for_update(db, normalized_request_id)
        if existing is not None:
            if existing.workline_id != workline_id:
                raise WorkLineStartIdempotencyConflictError(
                    f"request_id {normalized_request_id} 已属于 WorkLine {existing.workline_id}"
                )
            return WorkLineStartResult(
                epoch=existing,
                current_workline_runtime_status=(
                    "READY" if getattr(existing.status, "value", existing.status) == "ACTIVE" else None
                ),
                created=False,
            )

        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineStartNotFoundError(f"WorkLine {workline_id} 不存在")
        active_epoch = await self._epochs.get_active_for_workline_for_update(db, workline_id)
        await self._assert_startable(db, workline, active_epoch=active_epoch)
        plugin = self._resolve_plugin(workline)
        if plugin.business_blocker is not None:
            business = await plugin.business_blocker.get_unfinished_workload_summary(db, workline_id)
            if business["count"] > 0:
                raise WorkLineStartInvalidStateError(f"WorkLine 存在未闭合插件业务任务: {business.get('sample')}")
        started_at = self._clock()
        plan = await plugin.start_plan_builder.build(db, workline)
        if (plan.plugin_key, plan.plugin_version) != (plugin.plugin_key, plugin.plugin_version):
            raise WorkLineStartConfigurationError("START plan 的插件身份与部署插件不一致")
        epoch = await self._epoch_service.activate_epoch(
            db,
            epoch_code=normalized_request_id,
            workline_id=workline_id,
            plugin_key=plan.plugin_key,
            plugin_version=plan.plugin_version,
            flow_mode=plan.flow_mode,
            configuration_snapshot=plan.configuration_snapshot,
            device_bindings=plan.device_bindings,
            position_bindings=plan.position_bindings,
            started_at=started_at,
        )
        await self._worklines.set_active_for_start(db, workline)
        return WorkLineStartResult(
            epoch=epoch,
            current_workline_runtime_status="READY",
            created=True,
        )

    async def _assert_startable(self, db: Any, workline: Any, *, active_epoch: LineRunEpoch | None) -> None:
        if bool(getattr(workline, "is_active", False)):
            raise WorkLineStartInvalidStateError("WorkLine 已启用")
        if active_epoch is not None:
            raise WorkLineStartInvalidStateError("停用 WorkLine 仍存在 ACTIVE Epoch")
        if await self._safety.get_active_for_workline(db, workline.id) is not None:
            raise WorkLineStartInvalidStateError("WorkLine 存在 active safety incident")
        unfinished = await self._worklines.get_unfinished_workload_summary(db, workline.id)
        blockers = [
            owner_type
            for owner_type, blocked in unfinished["by_type"].items()
            if owner_type != "line_run_epochs" and bool(blocked)
        ]
        if blockers:
            sample = unfinished.get("sample")
            raise WorkLineStartInvalidStateError(
                f"WorkLine 存在未闭合 execution owner: {', '.join(blockers)}; sample={sample}"
            )

    def _resolve_plugin(self, workline: Any) -> InstalledWorkLinePlugin:
        plugin_key = getattr(workline, "plugin_key", None)
        if not isinstance(plugin_key, str) or not plugin_key.strip():
            raise WorkLineStartConfigurationError("WorkLine 未选择业务插件")
        try:
            plugin = resolve_installed_plugin(self._plugins, plugin_key)
        except (LookupError, ValueError) as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc
        line_type = getattr(workline, "line_type", None)
        try:
            normalized_line_type = line_type if isinstance(line_type, LineType) else LineType(line_type)
        except ValueError as exc:
            raise WorkLineStartConfigurationError(f"WorkLine line_type 无效: {line_type}") from exc
        if not plugin.supports(normalized_line_type):
            raise WorkLineStartConfigurationError(
                f"plugin {plugin.plugin_key} 不支持 WorkLine line_type {normalized_line_type.value}"
            )
        return plugin


__all__ = [
    "WorkLineStartConfigurationError",
    "WorkLineStartIdempotencyConflictError",
    "WorkLineStartInvalidStateError",
    "WorkLineStartNotFoundError",
    "WorkLineStartResult",
    "WorkLineStartService",
]
