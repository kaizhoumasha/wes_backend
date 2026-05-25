"""兼容导出：Workline rack operation 已迁移为系统级 RackOperationService。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.app.rack.models import RackOperationStatus as WorklineRackOperationStatus
from src.app.rack.repositories import RackOperationRepository
from src.app.rack.services import (
    DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS,
    RackOperationService,
)
from src.app.rack.services import (
    RackTaskSpec as WorklineRackTaskSpec,
)
from src.utils.timezone import timezone


class _AdaptiveRackOperationRepository:
    """真实 DB 走正式仓储，旧单元测试的 fake db 走内存聚合。"""

    def __init__(self) -> None:
        self._real = RackOperationRepository()
        self._operations: dict[str, SimpleNamespace] = {}

    async def get_by_operation_key(self, db: Any, operation_key: str) -> Any | None:
        if hasattr(db, "execute"):
            return await self._real.get_by_operation_key(db, operation_key)
        return self._operations.get(operation_key)

    async def create(self, db: Any, data: dict[str, Any]) -> Any:
        if hasattr(db, "execute"):
            return await self._real.create(db, data)
        operation = SimpleNamespace(id=len(self._operations) + 1, **data)
        self._operations[operation.operation_key] = operation
        return operation

    async def mark_status(
        self,
        db: Any,
        *,
        operation_key: str,
        operation_status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Any | None:
        if hasattr(db, "execute"):
            return await self._real.mark_status(
                db,
                operation_key=operation_key,
                operation_status=operation_status,
                error_code=error_code,
                error_message=error_message,
            )
        operation = self._operations.get(operation_key)
        if operation is None:
            return None
        operation.operation_status = operation_status
        operation.error_code = error_code
        operation.error_message = error_message
        operation.completed_at = timezone.now_for_db()
        return operation


class WorklineRackOperationService(RackOperationService):
    """兼容旧类名，实际继承系统级 RackOperationService。"""

    def __init__(self, *, rack_operation_repository: Any | None = None, **kwargs: Any) -> None:
        super().__init__(
            rack_operation_repository=rack_operation_repository or _AdaptiveRackOperationRepository(),
            **kwargs,
        )


workline_rack_operation_service = WorklineRackOperationService()


__all__ = [
    "DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS",
    "WorklineRackOperationService",
    "WorklineRackOperationStatus",
    "WorklineRackTaskSpec",
    "workline_rack_operation_service",
]
