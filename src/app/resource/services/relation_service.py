"""资源关系投影服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.app.resource.models import (
    RackBinMountStatus,
    RackPlacement,
    RackPlacementStatus,
    ResourceRelationSourceSystem,
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
        rack_bin_mount_repo: RackBinMountRepository = rack_bin_mount_repository,
        runtime_hold_creator: Any = default_runtime_hold_creation_service,
    ) -> None:
        self.state_event_repo = state_event_repo
        self.rack_placement_repo = rack_placement_repo
        self.rack_bin_mount_repo = rack_bin_mount_repo
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

    async def record_full_box_exchange_physical_completed(
        self,
        db: object,
        *,
        exchange_request_code: str,
        rack_release_id: str,
        post_exchange_relations: Mapping[str, Any],
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
        """记录满箱交换物理完成事实，并投影交换后的料箱挂载关系。"""

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
                "event_type": ResourceStateEventType.EXCHANGE_STATUS_UPDATED.value,
                "resource_type": ResourceType.EXCHANGE_TASK.value,
                "resource_code": exchange_request_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "payload_json": {
                    "exchange_request_code": exchange_request_code,
                    "rack_release_id": rack_release_id,
                    "source_task_id": source_task_id,
                    "post_exchange_relations": dict(post_exchange_relations),
                },
                "occurred_at": occurred_at,
                "received_at": timezone.now_for_db(),
            },
        )

        bin_mounts = _extract_bin_mounts(post_exchange_relations)
        if not bin_mounts:
            runtime_hold = await self._create_post_exchange_relations_missing_hold(
                db,
                exchange_request_code=exchange_request_code,
                rack_release_id=rack_release_id,
                post_exchange_relations=post_exchange_relations,
                source_system=source_system,
                source_event_id=source_event_id,
                source_task_id=source_task_id,
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
                runtime_hold=runtime_hold,
                reason_code="POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS",
                message="满箱交换回调缺少可投影的料箱挂载关系",
            )

        payload_conflict = _first_payload_bin_mount_conflict(bin_mounts)
        if payload_conflict is not None:
            runtime_hold = await self._create_bin_mount_conflict_hold(
                db,
                operation="FULL_BOX_EXCHANGE_PHYSICAL_COMPLETED",
                exchange_request_code=exchange_request_code,
                rack_release_id=rack_release_id,
                conflict=payload_conflict,
                source_system=source_system,
                source_event_id=source_event_id,
                source_task_id=source_task_id,
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
                runtime_hold=runtime_hold,
                reason_code=payload_conflict["reason_code"],
                message=payload_conflict["message"],
            )

        conflict = await self._first_bin_mount_conflict(db, bin_mounts)
        if conflict is not None:
            runtime_hold = await self._create_bin_mount_conflict_hold(
                db,
                operation="FULL_BOX_EXCHANGE_PHYSICAL_COMPLETED",
                exchange_request_code=exchange_request_code,
                rack_release_id=rack_release_id,
                conflict=conflict,
                source_system=source_system,
                source_event_id=source_event_id,
                source_task_id=source_task_id,
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
                runtime_hold=runtime_hold,
                reason_code=conflict["reason_code"],
                message=conflict["message"],
            )

        for mount in bin_mounts:
            if await self._same_active_bin_mount_exists(db, mount):
                continue
            await self.rack_bin_mount_repo.create(
                db,
                {
                    "rack_code": mount["rack_code"],
                    "rack_slot_code": mount["rack_slot_code"],
                    "bin_code": mount["bin_code"],
                    "mount_status": RackBinMountStatus.MOUNTED.value,
                    "source_system": ResourceRelationSourceSystem.WMS_RCS.value,
                    "source_event_id": source_event_id,
                    "source_version": source_version,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "started_at": occurred_at,
                    "ended_at": None,
                },
            )

        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_empty_rack_verified(
        self,
        db: object,
        *,
        rack_code: str,
        bin_mounts: Sequence[Mapping[str, Any]],
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
                "session_id": session_id,
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
            runtime_hold = await self._create_bin_mount_conflict_hold(
                db,
                operation="EMPTY_RACK_VERIFIED",
                conflict=conflict,
                source_system=source_system,
                source_event_id=source_event_id,
                source_task_id=source_task_id,
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
                runtime_hold=runtime_hold,
                reason_code=conflict["reason_code"],
                message=conflict["message"],
            )

        for mount in normalized_mounts:
            if await self._same_active_bin_mount_exists(db, mount):
                continue
            await self.rack_bin_mount_repo.create(
                db,
                {
                    "rack_code": mount["rack_code"],
                    "rack_slot_code": mount["rack_slot_code"],
                    "bin_code": mount["bin_code"],
                    "mount_status": RackBinMountStatus.MOUNTED.value,
                    "source_system": ResourceRelationSourceSystem.ECS.value,
                    "source_event_id": source_event_id,
                    "source_version": source_version,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "started_at": occurred_at,
                    "ended_at": None,
                },
            )

        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def _first_bin_mount_conflict(
        self,
        db: object,
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

    async def _same_active_bin_mount_exists(self, db: object, mount: dict[str, str]) -> bool:
        """同一 active 关系已存在时保持幂等，不重复创建投影。"""

        active_slot = await self.rack_bin_mount_repo.get_active_by_rack_slot(
            db,
            rack_code=mount["rack_code"],
            rack_slot_code=mount["rack_slot_code"],
        )
        return active_slot is not None and active_slot.bin_code == mount["bin_code"]

    async def _create_post_exchange_relations_missing_hold(
        self,
        db: object,
        *,
        exchange_request_code: str,
        rack_release_id: str,
        post_exchange_relations: Mapping[str, Any],
        source_system: ResourceSourceSystem,
        source_event_id: str,
        source_task_id: str | None,
        trace_id: str | None,
        session_id: str | None,
        workline_id: int | None,
        workline_session_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        """有 WorkLine 上下文时，为交换后关系缺失创建 RuntimeHold。"""

        if workline_id is None:
            return None

        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=workline_session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason="POST_EXCHANGE_RELATIONS_MISSING_BIN_MOUNTS",
            source_event_id=source_event_id,
            evidence={
                "resource_type": ResourceType.EXCHANGE_TASK.value,
                "exchange_request_code": exchange_request_code,
                "rack_release_id": rack_release_id,
                "post_exchange_relations": dict(post_exchange_relations),
                "incoming_source_event_id": source_event_id,
                "source_system": source_system.value,
                "source_task_id": source_task_id,
                "trace_id": trace_id,
                "session_id": session_id,
            },
        )

    async def _create_bin_mount_conflict_hold(
        self,
        db: object,
        *,
        operation: str,
        conflict: Mapping[str, Any],
        source_system: ResourceSourceSystem,
        source_event_id: str,
        exchange_request_code: str | None = None,
        rack_release_id: str | None = None,
        source_task_id: str | None = None,
        trace_id: str | None,
        session_id: str | None,
        workline_id: int | None,
        workline_session_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        """有 WorkLine 上下文时，为料箱挂载投影冲突创建 RuntimeHold。"""

        if workline_id is None:
            return None

        evidence = {
            "operation": operation,
            "resource_type": ResourceType.BIN.value,
            "rack_code": conflict.get("rack_code"),
            "rack_slot_code": conflict.get("rack_slot_code"),
            "incoming_bin_code": conflict.get("incoming_bin_code"),
            "active_bin_code": conflict.get("active_bin_code"),
            "active_rack_code": conflict.get("active_rack_code"),
            "active_rack_slot_code": conflict.get("active_rack_slot_code"),
            "active_source_event_id": conflict.get("active_source_event_id"),
            "incoming_source_event_id": source_event_id,
            "source_system": source_system.value,
            "source_task_id": source_task_id,
            "trace_id": trace_id,
            "session_id": session_id,
        }
        if exchange_request_code is not None:
            evidence["exchange_request_code"] = exchange_request_code
        if rack_release_id is not None:
            evidence["rack_release_id"] = rack_release_id

        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=workline_session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=str(conflict["reason_code"]),
            source_event_id=source_event_id,
            evidence=evidence,
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


def _extract_bin_mounts(post_exchange_relations: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_mounts = None
    for key in ("bin_mounts", "rack_bin_mounts", "mounts", "relations"):
        candidate = post_exchange_relations.get(key)
        if candidate is not None:
            raw_mounts = candidate
            break
    if not isinstance(raw_mounts, Sequence) or isinstance(raw_mounts, (str, bytes)):
        return []

    default_rack_code = _text_or_none(post_exchange_relations.get("rack_code"))
    mounts: list[dict[str, str]] = []
    for item in raw_mounts:
        if not isinstance(item, Mapping):
            continue
        rack_code = _text_or_none(item.get("rack_code")) or default_rack_code
        rack_slot_code = _text_or_none(item.get("rack_slot_code")) or _text_or_none(item.get("slot_code"))
        bin_code = _text_or_none(item.get("bin_code")) or _text_or_none(item.get("bin_id"))
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


def _first_payload_bin_mount_conflict(bin_mounts: Sequence[dict[str, str]]) -> dict[str, Any] | None:
    seen_slots: dict[tuple[str, str], dict[str, str]] = {}
    seen_bins: dict[str, dict[str, str]] = {}
    for mount in bin_mounts:
        slot_key = (mount["rack_code"], mount["rack_slot_code"])
        active_slot = seen_slots.get(slot_key)
        if active_slot is not None:
            return {
                "reason_code": "RACK_BIN_SLOT_DUPLICATED",
                "message": "交换后关系中同一货架槽位被重复挂载",
                "rack_code": mount["rack_code"],
                "rack_slot_code": mount["rack_slot_code"],
                "incoming_bin_code": mount["bin_code"],
                "active_bin_code": active_slot["bin_code"],
                "active_rack_code": active_slot["rack_code"],
                "active_rack_slot_code": active_slot["rack_slot_code"],
                "active_source_event_id": None,
            }
        seen_slots[slot_key] = mount

        active_bin = seen_bins.get(mount["bin_code"])
        if active_bin is not None:
            return {
                "reason_code": "BIN_MOUNT_DUPLICATED",
                "message": "交换后关系中同一料箱被重复挂载",
                "rack_code": mount["rack_code"],
                "rack_slot_code": mount["rack_slot_code"],
                "incoming_bin_code": mount["bin_code"],
                "active_bin_code": active_bin["bin_code"],
                "active_rack_code": active_bin["rack_code"],
                "active_rack_slot_code": active_bin["rack_slot_code"],
                "active_source_event_id": None,
            }
        seen_bins[mount["bin_code"]] = mount
    return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


resource_relation_service = ResourceRelationService()

__all__ = [
    "ResourceProjectionResult",
    "ResourceProjectionStatus",
    "ResourceRelationService",
    "resource_relation_service",
]
