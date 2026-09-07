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

    async def _lock_authorized_object(self, db, authority, object_type, object_id):
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError("unsupported projection object_type")
        line = await self._repository.get_workline_for_update(db, authority.workline_id)
        if line is None or not line.is_active:
            raise PositionProjectionAuthorityError("transport authority requires active WorkLine")
        await self._repository.lock_projection(db, object_type, object_id)
        projection = await self._repository.get_for_update(db, object_type, object_id)
        if (
            projection is not None
            and projection.workline_id != authority.workline_id
            and (
                projection.position_unknown
                or await self._repository.is_workline_position(db, projection.workline_id, projection.position_json)
            )
        ):
            raise PositionProjectionAuthorityError("object occupies another WorkLine")
        return projection

    async def admit_transport_member(self, db, *, authority, object_type, object_id, source):
        """在可发送义务持久化前验证当前准入，沿用对象锁阻止并行动作。"""
        projection = await self._lock_authorized_object(db, authority, object_type, object_id)
        if projection is None:
            return
        if projection.position_unknown:
            raise PositionProjectionAuthorityError("object position is unknown")
        rack_reference = source.get("kind") == "RACK" and source.get("location_code") == object_id
        if not rack_reference and projection.position_json != source:
            raise PositionProjectionAuthorityError("transport source does not match current position")

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
        projection = await self._lock_authorized_object(db, authority, object_type, object_id)
        if projection is None:
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
