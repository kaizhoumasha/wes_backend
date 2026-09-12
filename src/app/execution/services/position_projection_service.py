"""WorkLine 准入与匹配 Transport 物理结果驱动当前位置。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.repositories.position_projection_repository import position_projection_repository

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.transport.contracts import TransportExecutionAuthority


class PositionProjectionAuthorityError(ValueError):
    """工作线或对象资源不允许该动作。"""


class PositionProjectionService:
    def __init__(self, *, repository=position_projection_repository) -> None:
        self._repository = repository

    async def get_current(self, db, object_type, object_id, *, for_update=False):
        return await self._repository.get(db, object_type, object_id, for_update=for_update)

    async def _lock_authorized_object(self, db, object_type, object_id):
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError("unsupported projection object_type")
        await self._repository.lock_projection(db, object_type, object_id)
        return await self._repository.get_for_update(db, object_type, object_id)

    async def admit_transport_member(self, db, *, authority, object_type):
        """仅验证显式工作线许可；诊断投影不裁决独立请求或改写位置事实。"""
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError("unsupported projection object_type")
        line = await self._repository.get_workline_for_update(db, authority.workline_id)
        if line is None or not line.is_active:
            raise PositionProjectionAuthorityError("transport authority requires active WorkLine")

    async def apply_transport_result(
        self,
        db,
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
    ):
        if authority is None:
            return None
        projection = await self._lock_authorized_object(db, object_type, object_id)
        if projection is not None and projection.source_transport_task_id != transport_task_id:
            # 不同任务的 revision 和到达时间不可比较；各自事实留在任务，聚合仅标记未确认。
            projection.position_unknown = True
            await self._repository.flush(db)
            return projection
        if projection is None:
            if await self._repository.get_workline_for_update(db, authority.workline_id) is None:
                # 原任务事实仍由 Transport 保存；不存在的业务 owner 不能成为新投影外键。
                return None
            projection = await self._repository.add(
                db,
                PositionProjection(
                    object_type=object_type,
                    object_id=object_id,
                    workline_id=authority.workline_id,
                    source_operation_id=operation_id,
                    source_transport_task_id=transport_task_id,
                    updated_at=updated_at,
                ),
            )
        projection.workline_id = authority.workline_id
        projection.position_json = position
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_operation_id = operation_id
        projection.source_transport_task_id = transport_task_id
        projection.updated_at = updated_at
        await self._repository.flush(db)
        return projection


position_projection_service = PositionProjectionService()
