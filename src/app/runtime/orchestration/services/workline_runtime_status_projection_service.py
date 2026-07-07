"""WorkLine runtime status native projection service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.workline_runtime_status_projection_repository import (
    EnsureDefaultProjectionResult,
    WorklineRuntimeStatusProjectionRepository,
    workline_runtime_status_projection_repository,
)
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import optional_enum_str, optional_int

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class WorkLineRuntimeStatusSnapshot:
    """Runtime/orchestration-owned WorkLine status projection snapshot."""

    runtime_status: str | None
    source: str
    stopped_at: Any | None
    stopped_reason: str | None
    resumed_at: Any | None
    active_safety_incident_id: int | None


class WorkLineRuntimeStatusProjectionService:
    """集中维护 runtime/orchestration 原生 WorkLine 状态投影。"""

    def __init__(
        self,
        *,
        repository: WorklineRuntimeStatusProjectionRepository = workline_runtime_status_projection_repository,
    ) -> None:
        self.repository = repository

    async def ensure_default(self, db: Any, *, workline_id: int) -> WorklineRuntimeStatusProjection:
        """显式确保新建/恢复 WorkLine 具备 STOPPED 默认投影。"""

        return await self.repository.ensure_default(db, workline_id)

    async def ensure_default_result(self, db: Any, *, workline_id: int) -> EnsureDefaultProjectionResult:
        """显式确保默认投影存在，并保留是否实际插入的信息。"""

        return await self.repository.ensure_default_result(db, workline_id)

    async def runtime_status_snapshot(self, db: Any, *, workline_id: int) -> WorkLineRuntimeStatusSnapshot:
        """读取 runtime 状态快照；缺失投影不隐式创建。"""

        projection = await self.repository.get_by_workline_id(db, workline_id)
        return _snapshot_from_projection(projection)

    async def runtime_status_snapshot_map(
        self,
        db: Any,
        *,
        workline_ids: Sequence[int],
    ) -> dict[int, WorkLineRuntimeStatusSnapshot]:
        """批量读取 runtime 状态快照；缺失项返回显式 missing snapshot。"""

        ids = [int(workline_id) for workline_id in dict.fromkeys(workline_ids)]
        projections = await self.repository.list_by_workline_ids(db, ids)
        return {workline_id: _snapshot_from_projection(projections.get(workline_id)) for workline_id in ids}

    async def is_ready(self, db: Any, *, workline_id: int) -> bool:
        snapshot = await self.runtime_status_snapshot(db, workline_id=workline_id)
        return snapshot.runtime_status == WorkLineRuntimeStatus.READY.value

    async def is_estopped(self, db: Any, *, workline_id: int) -> bool:
        snapshot = await self.runtime_status_snapshot(db, workline_id=workline_id)
        return snapshot.runtime_status == WorkLineRuntimeStatus.ESTOPPED.value

    async def assert_accepting_runtime_work(
        self,
        db: Any,
        *,
        workline_id: int,
        blocked_error: type[Exception] = RuntimeError,
    ) -> None:
        """校验 runtime/orchestration 投影处于可接收新工作状态。"""

        snapshot = await self.runtime_status_snapshot(db, workline_id=workline_id)
        if snapshot.runtime_status == WorkLineRuntimeStatus.READY.value:
            return
        status = snapshot.runtime_status or "UNKNOWN"
        raise blocked_error(f"WORKLINE_{status}: workline_id={workline_id}")

    async def project_ready_after_start(
        self,
        db: Any,
        *,
        workline_id: int,
        occurred_at: Any | None = None,
        evidence_json: dict[str, Any] | None = None,
    ) -> WorklineRuntimeStatusProjection:
        current = await self.repository.get_by_workline_id(db, workline_id, for_update=True)
        return await self.repository.upsert_status(
            db,
            workline_id=workline_id,
            runtime_status=WorkLineRuntimeStatus.READY.value,
            source="runtime/orchestration",
            stopped_at=getattr(current, "stopped_at", None),
            stopped_reason=None,
            resumed_at=occurred_at or timezone.now_for_db(),
            active_safety_incident_id=None,
            evidence_json=evidence_json,
        )

    async def project_stopped_waiting_start(
        self,
        db: Any,
        *,
        workline_id: int,
        evidence_json: dict[str, Any] | None = None,
    ) -> WorklineRuntimeStatusProjection:
        current = await self.repository.get_by_workline_id(db, workline_id, for_update=True)
        return await self.repository.upsert_status(
            db,
            workline_id=workline_id,
            runtime_status=WorkLineRuntimeStatus.STOPPED.value,
            source="runtime/orchestration",
            stopped_at=getattr(current, "stopped_at", None),
            stopped_reason="RECOVERY_CLEARED_WAITING_START",
            resumed_at=None,
            active_safety_incident_id=None,
            evidence_json=evidence_json,
        )

    async def project_reconciling(
        self,
        db: Any,
        *,
        workline_id: int,
        occurred_at: Any | None = None,
        reason: str,
        evidence_json: dict[str, Any] | None = None,
    ) -> bool:
        current = await self.repository.get_by_workline_id(db, workline_id, for_update=True)
        if _status_value(getattr(current, "runtime_status", None)) == WorkLineRuntimeStatus.ESTOPPED.value:
            return False
        await self.repository.upsert_status(
            db,
            workline_id=workline_id,
            runtime_status=WorkLineRuntimeStatus.RECONCILING.value,
            source="runtime/orchestration",
            stopped_at=getattr(current, "stopped_at", None) or occurred_at or timezone.now_for_db(),
            stopped_reason=reason,
            resumed_at=None,
            active_safety_incident_id=optional_int(getattr(current, "active_safety_incident_id", None)),
            evidence_json=evidence_json,
        )
        return True

    async def project_estopped_active_hold(
        self,
        db: Any,
        *,
        workline_id: int,
        reason: str | None,
        active_safety_incident_id: int | None = None,
        occurred_at: Any | None = None,
        evidence_json: dict[str, Any] | None = None,
    ) -> WorklineRuntimeStatusProjection:
        current = await self.repository.get_by_workline_id(db, workline_id, for_update=True)
        return await self.repository.upsert_status(
            db,
            workline_id=workline_id,
            runtime_status=WorkLineRuntimeStatus.ESTOPPED.value,
            source="runtime/orchestration",
            stopped_at=getattr(current, "stopped_at", None) or occurred_at or timezone.now_for_db(),
            stopped_reason=reason,
            resumed_at=None,
            active_safety_incident_id=active_safety_incident_id,
            evidence_json=evidence_json,
        )


def _snapshot_from_projection(
    projection: WorklineRuntimeStatusProjection | None,
) -> WorkLineRuntimeStatusSnapshot:
    if projection is None:
        return WorkLineRuntimeStatusSnapshot(
            runtime_status=None,
            source="runtime/orchestration:missing",
            stopped_at=None,
            stopped_reason=None,
            resumed_at=None,
            active_safety_incident_id=None,
        )
    return WorkLineRuntimeStatusSnapshot(
        runtime_status=_status_value(getattr(projection, "runtime_status", None)),
        source=str(getattr(projection, "source", None) or "runtime/orchestration"),
        stopped_at=getattr(projection, "stopped_at", None),
        stopped_reason=getattr(projection, "stopped_reason", None),
        resumed_at=getattr(projection, "resumed_at", None),
        active_safety_incident_id=optional_int(getattr(projection, "active_safety_incident_id", None)),
    )


def _status_value(value: Any) -> str | None:
    return optional_enum_str(value)


workline_runtime_status_projection_service = WorkLineRuntimeStatusProjectionService()


__all__ = [
    "EnsureDefaultProjectionResult",
    "WorkLineRuntimeStatusProjectionService",
    "WorkLineRuntimeStatusSnapshot",
    "workline_runtime_status_projection_service",
]
