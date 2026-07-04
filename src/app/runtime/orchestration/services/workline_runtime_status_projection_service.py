"""WorkLine runtime_status 兼容投影服务。

该字段仍存放在 WorkLine 表上，但 Phase4 起只允许把它当作 runtime/orchestration
拥有的兼容投影使用，避免新的业务能力继续把 WorkLine 当作运行状态 owner。
"""

from __future__ import annotations

from typing import Any

from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_str


class WorkLineRuntimeStatusProjectionService:
    """集中维护 WorkLine.runtime_status 兼容投影。"""

    def status_value(self, workline: Any) -> str | None:
        return enum_str(getattr(workline, "runtime_status", None))

    def is_ready(self, workline: Any) -> bool:
        return self.status_value(workline) == WorkLineRuntimeStatus.READY.value

    def is_estopped(self, workline: Any) -> bool:
        return self.status_value(workline) == WorkLineRuntimeStatus.ESTOPPED.value

    def project_ready_after_start(self, workline: Any, *, occurred_at: Any | None = None) -> None:
        workline.runtime_status = WorkLineRuntimeStatus.READY
        workline.stopped_reason = None
        workline.resumed_at = occurred_at or timezone.now_for_db()

    def project_stopped_waiting_start(self, workline: Any) -> None:
        workline.runtime_status = WorkLineRuntimeStatus.STOPPED
        workline.resumed_at = None
        workline.stopped_reason = "RECOVERY_CLEARED_WAITING_START"

    def project_reconciling(self, workline: Any, *, occurred_at: Any | None = None, reason: str) -> bool:
        if self.is_estopped(workline):
            return False
        workline.runtime_status = WorkLineRuntimeStatus.RECONCILING
        workline.stopped_at = getattr(workline, "stopped_at", None) or occurred_at or timezone.now_for_db()
        workline.stopped_reason = reason
        return True

    def project_estopped_active_hold(self, workline: Any, *, reason: str | None) -> None:
        workline.runtime_status = WorkLineRuntimeStatus.ESTOPPED
        workline.stopped_reason = reason


workline_runtime_status_projection_service = WorkLineRuntimeStatusProjectionService()


__all__ = [
    "WorkLineRuntimeStatusProjectionService",
    "workline_runtime_status_projection_service",
]
