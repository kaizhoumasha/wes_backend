"""资源关系投影服务。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.app.resource.models import (
    RackPlacement,
    RackPlacementStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
    ResourceType,
)
from src.app.resource.repositories import (
    RackPlacementRepository,
    ResourceStateEventRepository,
    rack_placement_repository,
    resource_state_event_repository,
)
from src.app.workline.services.runtime_hold_creation_service import (
    runtime_hold_creation_service as default_runtime_hold_creation_service,
)
from src.utils.timezone import timezone


class ResourceProjectionStatus(str, Enum):
    """资源事实投影处理状态。"""

    PROJECTED = "PROJECTED"
    DUPLICATE = "DUPLICATE"
    RECONCILING = "RECONCILING"


class ResourceProjectionResult(BaseModel):
    """资源事实投影处理结果。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: ResourceProjectionStatus
    event: ResourceStateEvent | None = None
    projection: RackPlacement | None = None
    runtime_hold: Any | None = None
    reason_code: str | None = None
    message: str | None = None


class ResourceRelationService:
    """统一处理资源事实写入与当前投影更新。"""

    def __init__(
        self,
        *,
        state_event_repo: ResourceStateEventRepository = resource_state_event_repository,
        rack_placement_repo: RackPlacementRepository = rack_placement_repository,
        runtime_hold_creator: Any = default_runtime_hold_creation_service,
    ) -> None:
        self.state_event_repo = state_event_repo
        self.rack_placement_repo = rack_placement_repo
        self.runtime_hold_creator = runtime_hold_creator

    @staticmethod
    def _event_code(source_system: ResourceSourceSystem, source_event_id: str) -> str:
        return f"{source_system.value}:{source_event_id}"

    async def record_rack_arrived(
        self,
        db: object,
        *,
        rack_code: str,
        location_code: str,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        occurred_at: Any,
        source_version: str | None = None,
        source_task_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_id: int | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录货架到达事实，并在无冲突时创建 active placement 投影。"""

        existing_event = await self.state_event_repo.get_by_source_event_id(
            db,
            source_system=source_system,
            source_event_id=source_event_id,
        )
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(source_system, source_event_id),
                "event_type": ResourceStateEventType.RACK_ARRIVED.value,
                "resource_type": ResourceType.RACK.value,
                "resource_code": rack_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "payload_json": {
                    "rack_code": rack_code,
                    "location_code": location_code,
                    "source_task_id": source_task_id,
                },
                "occurred_at": occurred_at,
                "received_at": timezone.now_for_db(),
            },
        )

        active_placement = await self.rack_placement_repo.get_active_by_rack_code(db, rack_code)
        if active_placement is not None and active_placement.location_code != location_code:
            runtime_hold = await self._create_rack_placement_conflict_hold(
                db,
                rack_code=rack_code,
                incoming_location_code=location_code,
                active_placement=active_placement,
                source_system=source_system,
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=workline_session_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                projection=active_placement,
                runtime_hold=runtime_hold,
                reason_code="RACK_PLACEMENT_CONFLICT",
                message="货架已有不同 active placement，已追加事实但不覆盖当前投影",
            )

        if active_placement is not None:
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.PROJECTED,
                event=event,
                projection=active_placement,
            )

        projection = await self.rack_placement_repo.create(
            db,
            {
                "rack_code": rack_code,
                "location_code": location_code,
                "placement_status": RackPlacementStatus.ARRIVED.value,
                "source_system": source_system,
                "source_task_id": source_task_id,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "started_at": occurred_at,
                "ended_at": None,
            },
        )
        return ResourceProjectionResult(
            status=ResourceProjectionStatus.PROJECTED,
            event=event,
            projection=projection,
        )

    async def _create_rack_placement_conflict_hold(
        self,
        db: object,
        *,
        rack_code: str,
        incoming_location_code: str,
        active_placement: RackPlacement,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        trace_id: str | None,
        session_id: str | None,
        workline_id: int | None,
        workline_session_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        """有 WorkLine 上下文时，为货架位置冲突创建 RuntimeHold。"""

        if workline_id is None:
            return None

        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=workline_session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason="RACK_PLACEMENT_CONFLICT",
            source_event_id=source_event_id,
            evidence={
                "resource_type": ResourceType.RACK.value,
                "rack_code": rack_code,
                "active_location_code": active_placement.location_code,
                "incoming_location_code": incoming_location_code,
                "active_source_event_id": active_placement.source_event_id,
                "incoming_source_event_id": source_event_id,
                "source_system": source_system.value,
                "trace_id": trace_id,
                "session_id": session_id,
            },
        )


resource_relation_service = ResourceRelationService()

__all__ = [
    "ResourceProjectionResult",
    "ResourceProjectionStatus",
    "ResourceRelationService",
    "resource_relation_service",
]
