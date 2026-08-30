"""通用 WorkLine START 与历史 Epoch replay。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.device.repositories.command_repository import device_command_repository
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.workline_repository import workline_repository
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.workline.epoch_activation import (
        LineRunEpochDeviceBindingInput,
        LineRunEpochPositionBindingInput,
        WorkLineEpochActivationPlan,
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

    async def get_active_for_workline(self, db: Any, workline_id: int) -> LineRunEpoch | None: ...

    async def lock_epoch_lifecycle(self, db: Any, line_run_epoch_id: int) -> None: ...

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


class SafetyRepositoryPort(Protocol):
    async def get_active_for_workline(self, db: Any, workline_id: int) -> Any | None: ...


class WorkLineStartPlanBuilderPort(Protocol):
    async def build(self, db: Any, workline: Any) -> WorkLineEpochActivationPlan: ...


class EpochServicePort(Protocol):
    async def close_active_epoch(self, db: Any, **kwargs: Any) -> LineRunEpoch | None: ...

    async def activate_epoch(self, db: Any, **kwargs: Any) -> LineRunEpoch: ...


class UnclosedCommandRepositoryPort(Protocol):
    async def has_unclosed_for_epoch_for_update(self, db: Any, line_run_epoch_id: int) -> bool: ...


class WorkLineStartService:
    """在调用方事务内分类 replay 或创建完整 Epoch。"""

    def __init__(
        self,
        *,
        plan_builder: WorkLineStartPlanBuilderPort,
        epoch_repository: EpochRepositoryPort = cast("EpochRepositoryPort", line_run_epoch_repository),
        workline_repository: WorkLineRepositoryPort = cast("WorkLineRepositoryPort", workline_repository),
        safety_repository: SafetyRepositoryPort = cast("SafetyRepositoryPort", workline_safety_incident_repository),
        epoch_service: EpochServicePort | None = None,
        command_repository: UnclosedCommandRepositoryPort = cast(
            "UnclosedCommandRepositoryPort", device_command_repository
        ),
        clock: Any = timezone.now_for_db,
    ) -> None:
        self._plan_builder = plan_builder
        self._epochs = epoch_repository
        self._worklines = workline_repository
        self._safety = safety_repository
        self._epoch_service = epoch_service or LineRunEpochService(repository=epoch_repository)
        self._commands = command_repository
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
        await self._lock_active_epoch_lifecycle(db, workline_id)
        await self._assert_startable(db, workline)
        started_at = self._clock()
        try:
            await self._epoch_service.close_active_epoch(
                db,
                workline_id=workline_id,
                closed_at=started_at,
                command_repository=self._commands,
            )
        except ActiveLineRunEpochExistsError as exc:
            raise WorkLineStartInvalidStateError(str(exc)) from exc
        plan = await self._plan_builder.build(db, workline)
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
        return WorkLineStartResult(
            epoch=epoch,
            current_workline_runtime_status="READY",
            created=True,
        )

    async def _lock_active_epoch_lifecycle(self, db: Any, workline_id: int) -> None:
        candidate = await self._epochs.get_active_for_workline(db, workline_id)
        if candidate is None:
            return
        if candidate.id is None:
            raise WorkLineStartInvalidStateError("活动 Epoch 缺少持久化主键")
        await self._epochs.lock_epoch_lifecycle(db, candidate.id)
        active = await self._epochs.get_active_for_workline_for_update(db, workline_id)
        if active is None or active.id != candidate.id:
            raise WorkLineStartInvalidStateError("活动 Epoch 在 lifecycle fence 前发生变化")

    async def _assert_startable(self, db: Any, workline: Any) -> None:
        if not bool(getattr(workline, "is_active", False)):
            raise WorkLineStartInvalidStateError("WorkLine 未静态启用")
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


__all__ = [
    "WorkLineStartConfigurationError",
    "WorkLineStartIdempotencyConflictError",
    "WorkLineStartInvalidStateError",
    "WorkLineStartNotFoundError",
    "WorkLineStartPlanBuilderPort",
    "WorkLineStartResult",
    "WorkLineStartService",
]
