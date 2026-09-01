"""资源关系投影服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.app.resource.models import (
    RackBinMountStatus,
    RackPlacement,
    RackPlacementStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
    ResourceType,
)
from src.app.resource.repositories import (
    RackBinMountRepository,
    RackPlacementRepository,
    ResourceStateEventRepository,
    rack_bin_mount_repository,
    rack_placement_repository,
    resource_state_event_repository,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_str


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
    reason_code: str | None = None
    message: str | None = None


class ResourceRelationService:
    """统一处理资源事实写入与当前投影更新。"""

    def __init__(
        self,
        *,
        state_event_repo: ResourceStateEventRepository = resource_state_event_repository,
        rack_placement_repo: RackPlacementRepository = rack_placement_repository,
        rack_bin_mount_repo: RackBinMountRepository = rack_bin_mount_repository,
    ) -> None:
        self.state_event_repo = state_event_repo
        self.rack_placement_repo = rack_placement_repo
        self.rack_bin_mount_repo = rack_bin_mount_repo

    @staticmethod
    def _event_code(source_system: ResourceSourceSystem, source_event_id: str) -> str:
        return f"{source_system.value}:{source_event_id}"

    async def record_rack_arrived(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        location_code: str,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        occurred_at: Any,
        source_version: str | None = None,
        source_task_id: str | None = None,
        trace_id: str | None = None,
        workline_session_id: int | None = None,
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
                "workline_session_id": workline_session_id,
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
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                projection=active_placement,
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
                "workline_session_id": workline_session_id,
                "started_at": occurred_at,
                "ended_at": None,
            },
        )
        return ResourceProjectionResult(
            status=ResourceProjectionStatus.PROJECTED,
            event=event,
            projection=projection,
        )

    async def record_empty_rack_verified(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        bin_mounts: Sequence[Mapping[str, Any]],
        source_event_id: str,
        occurred_at: Any,
        source_version: str | None = None,
        source_task_id: str | None = None,
        trace_id: str | None = None,
        workline_session_id: int | None = None,
    ) -> ResourceProjectionResult:
        """记录 ECS 验空事实，并投影空架上的 4 个 active 料箱挂载关系。"""

        source_system = ResourceSourceSystem.ECS
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
                "event_type": ResourceStateEventType.BIN_MOUNTED.value,
                "resource_type": ResourceType.RACK.value,
                "resource_code": rack_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "workline_session_id": workline_session_id,
                "payload_json": {
                    "rack_code": rack_code,
                    "source_task_id": source_task_id,
                    "bin_mounts": [dict(mount) for mount in bin_mounts],
                },
                "occurred_at": occurred_at,
                "received_at": timezone.now_for_db(),
            },
        )

        normalized_mounts = _extract_bin_mounts({"rack_code": rack_code, "bin_mounts": list(bin_mounts)})
        if len(normalized_mounts) != 4:
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                reason_code="EMPTY_RACK_BIN_MOUNTS_INVALID",
                message="ECS 验空事实未提供 4 个可投影的料箱挂载关系",
            )

        conflict = await self._first_bin_mount_conflict(db, normalized_mounts)
        if conflict is not None:
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                reason_code=conflict["reason_code"],
                message=conflict["message"],
            )

        for mount in normalized_mounts:
            if await self._same_active_bin_mount_exists(db, mount):
                continue
            _ = await self.rack_bin_mount_repo.create(
                db,
                {
                    "rack_code": mount["rack_code"],
                    "rack_slot_code": mount["rack_slot_code"],
                    "bin_code": mount["bin_code"],
                    "mount_status": RackBinMountStatus.MOUNTED.value,
                    "source_system": ResourceSourceSystem.ECS.value,
                    "source_event_id": source_event_id,
                    "source_version": source_version,
                    "trace_id": trace_id,
                    "workline_session_id": workline_session_id,
                    "started_at": occurred_at,
                    "ended_at": None,
                },
            )

        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def _first_bin_mount_conflict(
        self,
        db: AsyncSession,
        bin_mounts: Sequence[dict[str, str]],
    ) -> dict[str, Any] | None:
        """检查交换后关系是否会覆盖已有 active 料箱挂载投影。"""

        for mount in bin_mounts:
            active_slot = await self.rack_bin_mount_repo.get_active_by_rack_slot(
                db,
                rack_code=mount["rack_code"],
                rack_slot_code=mount["rack_slot_code"],
            )
            if active_slot is not None and active_slot.bin_code != mount["bin_code"]:
                return {
                    "reason_code": "RACK_BIN_SLOT_CONFLICT",
                    "message": "货架槽位已有不同 active bin mount，不覆盖当前投影",
                    "rack_code": mount["rack_code"],
                    "rack_slot_code": mount["rack_slot_code"],
                    "incoming_bin_code": mount["bin_code"],
                    "active_bin_code": active_slot.bin_code,
                    "active_rack_code": active_slot.rack_code,
                    "active_rack_slot_code": active_slot.rack_slot_code,
                    "active_source_event_id": active_slot.source_event_id,
                }

            active_bin = await self.rack_bin_mount_repo.get_active_by_bin_code(db, mount["bin_code"])
            if active_bin is not None and (
                active_bin.rack_code != mount["rack_code"] or active_bin.rack_slot_code != mount["rack_slot_code"]
            ):
                return {
                    "reason_code": "BIN_ACTIVE_MOUNT_CONFLICT",
                    "message": "料箱已有不同 active mount，不覆盖当前投影",
                    "rack_code": mount["rack_code"],
                    "rack_slot_code": mount["rack_slot_code"],
                    "incoming_bin_code": mount["bin_code"],
                    "active_bin_code": active_bin.bin_code,
                    "active_rack_code": active_bin.rack_code,
                    "active_rack_slot_code": active_bin.rack_slot_code,
                    "active_source_event_id": active_bin.source_event_id,
                }
        return None

    async def _same_active_bin_mount_exists(self, db: AsyncSession, mount: dict[str, str]) -> bool:
        """同一 active 关系已存在时保持幂等，不重复创建投影。"""

        active_slot = await self.rack_bin_mount_repo.get_active_by_rack_slot(
            db,
            rack_code=mount["rack_code"],
            rack_slot_code=mount["rack_slot_code"],
        )
        return active_slot is not None and active_slot.bin_code == mount["bin_code"]


def _extract_bin_mounts(post_exchange_relations: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_mounts = None
    for key in ("bin_mounts", "rack_bin_mounts", "mounts", "relations"):
        candidate = post_exchange_relations.get(key)
        if candidate is not None:
            raw_mounts = candidate
            break
    if not isinstance(raw_mounts, Sequence) or isinstance(raw_mounts, (str, bytes)):
        return []

    default_rack_code = coerce_optional_str(post_exchange_relations.get("rack_code"))
    mounts: list[dict[str, str]] = []
    for item in raw_mounts:
        if not isinstance(item, Mapping):
            continue
        rack_code = coerce_optional_str(item.get("rack_code")) or default_rack_code
        rack_slot_code = coerce_optional_str(item.get("rack_slot_code")) or coerce_optional_str(item.get("slot_code"))
        bin_code = coerce_optional_str(item.get("bin_code")) or coerce_optional_str(item.get("bin_id"))
        if rack_code is None or rack_slot_code is None or bin_code is None:
            continue
        mounts.append(
            {
                "rack_code": rack_code,
                "rack_slot_code": rack_slot_code,
                "bin_code": bin_code,
            }
        )
    return mounts


resource_relation_service = ResourceRelationService()

__all__ = [
    "ResourceProjectionResult",
    "ResourceProjectionStatus",
    "ResourceRelationService",
    "resource_relation_service",
]
