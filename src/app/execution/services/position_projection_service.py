"""WorkLine 准入与匹配 Transport 物理结果驱动当前位置。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.repositories.position_projection_repository import position_projection_repository
from src.app.execution.repositories.transport_decision_binding_repository import (
    transport_decision_binding_repository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.transport.contracts import TransportExecutionAuthority


class PositionProjectionAuthorityError(ValueError):
    """工作线或对象资源不允许该动作。"""


class PositionProjectionInvariantViolation(ValueError):
    """不可变 Transport identity 与权威 Binding 不一致。"""


class PositionProjectionRetryableError(RuntimeError):
    """Tx2 的明确瞬态失败；调用方只能在下一次 candidate tick 重试。"""


class ProjectionEffectPhase(StrEnum):
    """同一 Transport/member source 的 projection effect 顺序。"""

    ACK_INVALIDATION = "ACK_INVALIDATION"
    FINAL_RESULT = "FINAL_RESULT"


class ProjectionCausalRelation(StrEnum):
    SAME = "SAME"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    INCOMPARABLE = "INCOMPARABLE"


@dataclass(frozen=True, slots=True)
class ProjectionSource:
    """用于比较同一 projection object 的不可变 source identity。"""

    object_type: str
    object_id: str
    source_identity: str
    causal_token: int
    effect_phase: ProjectionEffectPhase


def compare_projection_sources(incoming: ProjectionSource, current: ProjectionSource) -> ProjectionCausalRelation:
    """只在同一 object domain 内比较 authoritative causal token。"""

    if (incoming.object_type, incoming.object_id) != (current.object_type, current.object_id):
        return ProjectionCausalRelation.INCOMPARABLE
    if incoming.causal_token < current.causal_token:
        return ProjectionCausalRelation.BEFORE
    if incoming.causal_token > current.causal_token:
        return ProjectionCausalRelation.AFTER
    if incoming.source_identity != current.source_identity:
        return ProjectionCausalRelation.INCOMPARABLE
    if incoming.effect_phase == current.effect_phase:
        return ProjectionCausalRelation.SAME
    if incoming.effect_phase == ProjectionEffectPhase.ACK_INVALIDATION:
        return ProjectionCausalRelation.BEFORE
    return ProjectionCausalRelation.AFTER


class PositionProjectionService:
    def __init__(
        self,
        *,
        repository=position_projection_repository,
        binding_repository=transport_decision_binding_repository,
    ) -> None:
        self._repository = repository
        self._bindings = binding_repository

    async def _transport_source(
        self,
        db,
        *,
        authority,
        object_type: str,
        object_id: str,
        client_request_id: str,
        transport_task_id: str,
        effect_phase: ProjectionEffectPhase,
    ) -> ProjectionSource:
        binding = await self._bindings.get_by_client_request_id(db, client_request_id)
        if binding is None or binding.workline_id != authority.workline_id:
            raise PositionProjectionInvariantViolation("Transport identity has no matching authoritative Binding")
        return ProjectionSource(
            object_type=object_type,
            object_id=object_id,
            source_identity=transport_task_id,
            causal_token=binding.causal_token,
            effect_phase=effect_phase,
        )

    @staticmethod
    def _projection_source(projection: PositionProjection) -> ProjectionSource:
        if projection.source_causal_token is None or projection.source_effect_phase is None:
            raise PositionProjectionInvariantViolation("projection provenance is incomplete")
        return ProjectionSource(
            object_type=projection.object_type,
            object_id=projection.object_id,
            source_identity=projection.source_transport_task_id,
            causal_token=projection.source_causal_token,
            effect_phase=ProjectionEffectPhase(projection.source_effect_phase),
        )

    async def get_current(self, db, object_type, object_id, *, for_update=False):
        return await self._repository.get(db, object_type, object_id, for_update=for_update)

    async def _lock_object_authority(self, db, object_type, object_id):
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError("unsupported projection object_type")
        await self._repository.lock_object_authority(db, object_type, object_id)

    async def admit_transport_member(self, db, *, authority, object_type):
        """仅验证显式工作线许可；诊断投影不裁决独立请求或改写位置事实。"""
        if object_type not in {"RACK", "BIN"}:
            raise PositionProjectionAuthorityError("unsupported projection object_type")
        line = await self._repository.get_workline_for_update(db, authority.workline_id)
        if line is None or not line.is_active:
            raise PositionProjectionAuthorityError("transport authority requires active WorkLine")

    async def apply_device_position_result(
        self,
        db,
        *,
        workline_id: int,
        object_type: str,
        object_id: str,
        position: dict[str, Any],
        updated_at: datetime,
    ) -> PositionProjection:
        """Apply a verified device position fact through the projection owner."""
        authority_line = await self._repository.get_workline_for_update(db, workline_id)
        if authority_line is None:
            raise PositionProjectionInvariantViolation("device position fact has no WorkLine authority")
        await self._lock_object_authority(db, object_type, object_id)
        projection = await self._repository.get_for_update(db, object_type, object_id)
        if projection is None or projection.workline_id != workline_id:
            raise PositionProjectionInvariantViolation("device position fact has no matching projection authority")
        projection.position_json = position
        projection.position_unknown = False
        projection.arrival_face = None
        projection.updated_at = updated_at
        await self._repository.flush(db)
        return projection

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
        client_request_id: str,
        operation_id: str,
        transport_task_id: str,
        updated_at: datetime,
    ):
        if authority is None:
            return None
        authority_line = await self._repository.get_workline_for_update(db, authority.workline_id)
        await self._lock_object_authority(db, object_type, object_id)
        incoming_source = await self._transport_source(
            db,
            authority=authority,
            object_type=object_type,
            object_id=object_id,
            client_request_id=client_request_id,
            transport_task_id=transport_task_id,
            effect_phase=ProjectionEffectPhase.FINAL_RESULT,
        )
        projection = await self._repository.get_for_update(db, object_type, object_id)
        if projection is None and incoming_source.causal_token == 0:
            return None
        if projection is not None:
            relation = compare_projection_sources(incoming_source, self._projection_source(projection))
            if relation is ProjectionCausalRelation.INCOMPARABLE:
                raise PositionProjectionInvariantViolation("projection sources are not causally comparable")
            if relation in {ProjectionCausalRelation.SAME, ProjectionCausalRelation.BEFORE}:
                return projection
        if projection is None:
            if authority_line is None:
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
                    source_causal_token=incoming_source.causal_token,
                    source_effect_phase=incoming_source.effect_phase.value,
                    updated_at=updated_at,
                ),
            )
        projection.workline_id = authority.workline_id
        projection.position_json = position
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_operation_id = operation_id
        projection.source_transport_task_id = transport_task_id
        projection.source_causal_token = incoming_source.causal_token
        projection.source_effect_phase = incoming_source.effect_phase.value
        projection.updated_at = updated_at
        await self._repository.flush(db)
        return projection

    async def invalidate_transport_member(
        self,
        db,
        *,
        authority: TransportExecutionAuthority | None,
        object_type: str,
        object_id: str,
        client_request_id: str,
        operation_id: str,
        transport_task_id: str,
        updated_at: datetime,
    ):
        """动作已被接纳或送达状态未知时，撤销原位置的确定性。"""
        if authority is None:
            return None
        _ = await self._repository.get_workline_for_update(db, authority.workline_id)
        await self._lock_object_authority(db, object_type, object_id)
        incoming_source = await self._transport_source(
            db,
            authority=authority,
            object_type=object_type,
            object_id=object_id,
            client_request_id=client_request_id,
            transport_task_id=transport_task_id,
            effect_phase=ProjectionEffectPhase.ACK_INVALIDATION,
        )
        projection = await self._repository.get_for_update(db, object_type, object_id)
        if projection is None:
            return None
        relation = compare_projection_sources(incoming_source, self._projection_source(projection))
        if relation is ProjectionCausalRelation.INCOMPARABLE:
            raise PositionProjectionInvariantViolation("projection sources are not causally comparable")
        if relation in {ProjectionCausalRelation.SAME, ProjectionCausalRelation.BEFORE}:
            return projection
        projection.position_unknown = True
        projection.source_operation_id = operation_id
        projection.source_transport_task_id = transport_task_id
        projection.source_causal_token = incoming_source.causal_token
        projection.source_effect_phase = incoming_source.effect_phase.value
        projection.updated_at = updated_at
        await self._repository.flush(db)
        return projection


position_projection_service = PositionProjectionService()
