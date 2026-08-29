"""活动 execution authority 下的 current position projection 服务。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol

from src.app.execution.models.bin_execution import BinExecutionStatus
from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.workline.models.line_run_epoch import LineRunEpochStatus

if TYPE_CHECKING:
    from src.app.transport.contracts import TransportExecutionAuthority


class PositionProjectionAuthorityError(ValueError):
    """冻结 authority 与活动执行或目标对象不匹配。"""


class PositionProjectionRepositoryPort(Protocol):
    async def lock_epoch_lifecycle(self, db: object, line_run_epoch_id: int) -> None: ...

    async def get_epoch_for_update(self, db: object, line_run_epoch_id: int) -> object | None: ...

    async def get_bin_execution_for_update(self, db: object, bin_execution_id: int) -> object | None: ...

    async def lock_projection(self, db: object, object_type: str, object_id: str) -> None: ...

    async def get(
        self, db: object, object_type: str, object_id: str, *, for_update: bool = False
    ) -> PositionProjection | None: ...

    async def get_for_update(self, db: object, object_type: str, object_id: str) -> PositionProjection | None: ...

    async def add(self, db: object, projection: PositionProjection) -> PositionProjection: ...

    async def delete_for_epoch(self, db: object, line_run_epoch_id: int) -> None: ...

    async def flush(self, db: object) -> None: ...


class PositionProjectionService:
    def __init__(self, *, repository: PositionProjectionRepositoryPort = position_projection_repository) -> None:
        self._repository = repository

    async def get_current(
        self,
        db: object,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> PositionProjection | None:
        return await self._repository.get(db, object_type, object_id, for_update=for_update)

    async def apply_transport_result(
        self,
        db: object,
        *,
        authority: TransportExecutionAuthority | None,
        object_type: str,
        object_id: str,
        position: dict[str, Any] | None,
        position_unknown: bool,
        arrival_face: str | None,
        operation_id: str,
        transport_task_id: str,
        updated_at: datetime,
    ) -> PositionProjection | None:
        if authority is None:
            return None
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError(f"unsupported projection object_type: {object_type}")
        if (object_type == "BIN") != (authority.bin_execution_id is not None):
            raise PositionProjectionAuthorityError("BIN projection requires bin execution authority only")

        await self._repository.lock_epoch_lifecycle(db, authority.line_run_epoch_id)
        epoch = await self._repository.get_epoch_for_update(db, authority.line_run_epoch_id)
        self._assert_active_epoch(epoch, authority)

        bin_execution = None
        if authority.bin_execution_id is not None:
            bin_execution = await self._repository.get_bin_execution_for_update(db, authority.bin_execution_id)
            self._assert_active_bin(bin_execution, authority, object_id)

        await self._repository.lock_projection(db, object_type, object_id)
        projection = await self._repository.get_for_update(db, object_type, object_id)

        # advisory/row locks are held until transaction end; the explicit recheck guards future
        # repository changes.
        epoch = await self._repository.get_epoch_for_update(db, authority.line_run_epoch_id)
        self._assert_active_epoch(epoch, authority)
        if authority.bin_execution_id is not None:
            bin_execution = await self._repository.get_bin_execution_for_update(db, authority.bin_execution_id)
            self._assert_active_bin(bin_execution, authority, object_id)

        if projection is None:
            projection = await self._repository.add(
                db,
                PositionProjection(
                    object_type=object_type,
                    object_id=object_id,
                    workline_id=authority.workline_id,
                    line_run_epoch_id=authority.line_run_epoch_id,
                    bin_execution_id=authority.bin_execution_id,
                    source_operation_id=operation_id,
                    source_transport_task_id=transport_task_id,
                    updated_at=updated_at,
                ),
            )
        elif (
            projection.workline_id != authority.workline_id
            or projection.line_run_epoch_id != authority.line_run_epoch_id
            or projection.bin_execution_id != authority.bin_execution_id
        ):
            raise PositionProjectionAuthorityError("current projection belongs to a different execution authority")

        projection.position_json = position
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_operation_id = operation_id
        projection.source_transport_task_id = transport_task_id
        projection.updated_at = updated_at
        await self._repository.flush(db)
        return projection

    async def delete_for_epoch(self, db: object, line_run_epoch_id: int) -> None:
        await self._repository.lock_epoch_lifecycle(db, line_run_epoch_id)
        await self._repository.delete_for_epoch(db, line_run_epoch_id)
        await self._repository.flush(db)

    @staticmethod
    def _assert_active_epoch(epoch: object | None, authority: TransportExecutionAuthority) -> None:
        if (
            epoch is None
            or getattr(epoch, "status", None) != LineRunEpochStatus.ACTIVE
            or getattr(epoch, "workline_id", None) != authority.workline_id
        ):
            raise PositionProjectionAuthorityError("transport authority does not reference an active matching Epoch")

    @staticmethod
    def _assert_active_bin(
        execution: object | None,
        authority: TransportExecutionAuthority,
        object_id: str,
    ) -> None:
        if (
            execution is None
            or getattr(execution, "status", None) != BinExecutionStatus.ACTIVE
            or getattr(execution, "bin_id", None) != object_id
            or getattr(execution, "workline_id", None) != authority.workline_id
            or getattr(execution, "line_run_epoch_id", None) != authority.line_run_epoch_id
        ):
            raise PositionProjectionAuthorityError(
                "transport authority does not reference the active matching BinExecution"
            )


position_projection_service = PositionProjectionService()

__all__ = [
    "PositionProjectionAuthorityError",
    "PositionProjectionService",
    "position_projection_service",
]
