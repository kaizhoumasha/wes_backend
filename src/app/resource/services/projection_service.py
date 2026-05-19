"""资源事实与 active 投影服务。"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.resource.models import (
    BinMaterialMountStatus,
    RackBinMountStatus,
    RackKind,
    RackPlacement,
    RackPlacementStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
    ResourceType,
)
from src.app.resource.repositories import (
    BinMaterialMountRepository,
    RackBinMountRepository,
    RackPlacementRepository,
    ResourceStateEventRepository,
    bin_material_mount_repository,
    rack_bin_mount_repository,
    rack_placement_repository,
    resource_state_event_repository,
)
from src.app.resource.services.relation_service import ResourceProjectionResult, ResourceProjectionStatus
from src.app.resource.services.snapshot_service import ResourceSnapshotService, resource_snapshot_service
from src.app.workline.services.rack_position_service import (
    WorklineRackPositionService,
    workline_rack_position_service,
)
from src.app.workline.services.runtime_hold_creation_service import (
    runtime_hold_creation_service as default_runtime_hold_creation_service,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _as_source_system(value: Any) -> ResourceSourceSystem:
    if isinstance(value, ResourceSourceSystem):
        return value
    return ResourceSourceSystem(str(value))


def _as_rack_kind(value: Any) -> RackKind:
    if isinstance(value, RackKind):
        return value
    return RackKind(str(value))


def _db_time(value: Any) -> Any:
    return timezone.to_db_datetime(value) or timezone.now_for_db()


class ResourceProjectionService:
    """统一处理资源事实写入、active 投影和冲突 RuntimeHold。"""

    def __init__(
        self,
        *,
        state_event_repo: ResourceStateEventRepository = resource_state_event_repository,
        rack_placement_repo: RackPlacementRepository = rack_placement_repository,
        rack_bin_mount_repo: RackBinMountRepository = rack_bin_mount_repository,
        bin_material_mount_repo: BinMaterialMountRepository = bin_material_mount_repository,
        rack_position_service: WorklineRackPositionService = workline_rack_position_service,
        runtime_hold_creator: Any = default_runtime_hold_creation_service,
        snapshot_service: ResourceSnapshotService = resource_snapshot_service,
    ) -> None:
        self.state_event_repo = state_event_repo
        self.rack_placement_repo = rack_placement_repo
        self.rack_bin_mount_repo = rack_bin_mount_repo
        self.bin_material_mount_repo = bin_material_mount_repo
        self.rack_position_service = rack_position_service
        self.runtime_hold_creator = runtime_hold_creator
        self.snapshot_service = snapshot_service

    @staticmethod
    def _event_code(
        *,
        event_type: ResourceStateEventType,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        resource_code: str,
    ) -> str:
        raw = f"{event_type.value}:{source_system.value}:{source_event_id}:{resource_code}"
        if len(raw) <= 160:
            return raw

        digest = sha256(raw.encode("utf-8")).hexdigest()[:16]
        resource_fragment = resource_code[:80]
        prefix = f"{event_type.value}:{source_system.value}"
        source_budget = max(0, 160 - len(prefix) - len(resource_fragment) - len(digest) - 3)
        source_fragment = source_event_id[:source_budget]
        return f"{prefix}:{source_fragment}:{resource_fragment}:{digest}"

    async def _get_duplicate_event(
        self,
        db: AsyncSession,
        *,
        idempotency_key: str | None,
    ) -> ResourceStateEvent | None:
        if not idempotency_key:
            return None
        return await self.state_event_repo.get_by_idempotency_key(db, idempotency_key)

    async def record_rack_arrived_at_workline_position(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        rack_kind: RackKind,
        workline_code: str,
        position_code: str,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        source_version: str | None = None,
        source_task_id: str | None = None,
        external_location_code: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_id: int | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录货架到达工作线停靠位事实，并更新 active placement。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        position = await self.rack_position_service.require_enabled_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=rack_kind,
        )
        resolved_workline_id = workline_id if workline_id is not None else getattr(position, "workline_id", None)
        resolved_external_location = external_location_code or getattr(position, "external_location_code", None)
        occurred_at_for_db = _db_time(occurred_at)
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.RACK_ARRIVED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=rack_code,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.RACK_ARRIVED.value,
                "resource_type": ResourceType.RACK.value,
                "resource_code": rack_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "workline_id": resolved_workline_id,
                "workline_code": workline_code,
                "position_code": position_code,
                "logic_location_code": getattr(position, "logic_location_code", None),
                "external_location_code": resolved_external_location,
                "payload_json": {
                    "rack_code": rack_code,
                    "rack_kind": rack_kind.value,
                    "workline_code": workline_code,
                    "position_code": position_code,
                    "source_task_id": source_task_id,
                    "external_location_code": resolved_external_location,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        active_by_rack = await self.rack_placement_repo.get_active_by_rack_code(db, rack_code)
        if active_by_rack is not None and (
            active_by_rack.workline_code != workline_code or active_by_rack.position_code != position_code
        ):
            runtime_hold = await self._create_placement_conflict_hold(
                db,
                reason_code="RACK_PLACEMENT_CONFLICT",
                rack_code=rack_code,
                active_placement=active_by_rack,
                incoming={"workline_code": workline_code, "position_code": position_code},
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=resolved_workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                projection=active_by_rack,
                runtime_hold=runtime_hold,
                reason_code="RACK_PLACEMENT_CONFLICT",
                message="货架已有不同 active 工作线停靠位，已追加事实但不覆盖当前投影",
            )

        active_by_position = await self.rack_placement_repo.get_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        if active_by_position is not None and active_by_position.rack_code != rack_code:
            runtime_hold = await self._create_placement_conflict_hold(
                db,
                reason_code="WORKLINE_POSITION_OCCUPIED",
                rack_code=rack_code,
                active_placement=active_by_position,
                incoming={"workline_code": workline_code, "position_code": position_code},
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=resolved_workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                projection=active_by_position,
                runtime_hold=runtime_hold,
                reason_code="WORKLINE_POSITION_OCCUPIED",
                message="工作线停靠位已有其他 active 货架，已追加事实但不覆盖当前投影",
            )

        if active_by_rack is not None:
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.PROJECTED,
                event=event,
                projection=active_by_rack,
            )

        projection = await self.rack_placement_repo.create(
            db,
            {
                "rack_code": rack_code,
                "rack_kind": rack_kind.value,
                "location_code": getattr(position, "logic_location_code", None) or position_code,
                "workline_id": resolved_workline_id,
                "workline_code": workline_code,
                "position_code": position_code,
                "position_role": _enum_value(getattr(position, "position_role", "")),
                "logic_location_code": getattr(position, "logic_location_code", None),
                "external_location_code": resolved_external_location,
                "placement_status": RackPlacementStatus.ARRIVED.value,
                "source_system": source_system,
                "source_task_id": source_task_id,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "started_at": occurred_at_for_db,
                "ended_at": None,
            },
        )
        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event, projection=projection)

    async def record_bin_mounted_to_rack(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        bin_mounts: Sequence[Mapping[str, Any]],
        source_system: ResourceSourceSystem,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        source_version: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_id: int | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录货架槽位挂载料箱事实，并创建 active RackBinMount。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        normalized_mounts = [
            {"rack_slot_code": str(item["rack_slot_code"]), "bin_code": str(item["bin_code"])} for item in bin_mounts
        ]
        occurred_at_for_db = _db_time(occurred_at)
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.BIN_MOUNTED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=rack_code,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.BIN_MOUNTED.value,
                "resource_type": ResourceType.RACK.value,
                "resource_code": rack_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "payload_json": {"rack_code": rack_code, "bin_mounts": normalized_mounts},
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        for item in normalized_mounts:
            rack_slot_code = item["rack_slot_code"]
            bin_code = item["bin_code"]
            active_slot = await self.rack_bin_mount_repo.get_active_by_rack_slot(
                db,
                rack_code=rack_code,
                rack_slot_code=rack_slot_code,
            )
            active_bin = await self.rack_bin_mount_repo.get_active_by_bin_code(db, bin_code)
            if active_slot is not None or active_bin is not None:
                runtime_hold = await self._create_rack_bin_mount_conflict_hold(
                    db,
                    rack_code=rack_code,
                    rack_slot_code=rack_slot_code,
                    bin_code=bin_code,
                    active_slot=active_slot,
                    active_bin=active_bin,
                    source_event_id=source_event_id,
                    trace_id=trace_id,
                    session_id=workline_session_id,
                    workline_id=workline_id,
                    plugin_key=plugin_key,
                    contract_version=contract_version,
                )
                return ResourceProjectionResult(
                    status=ResourceProjectionStatus.RECONCILING,
                    event=event,
                    runtime_hold=runtime_hold,
                    reason_code="RACK_BIN_MOUNT_CONFLICT",
                    message="货架槽位或料箱已有 active 挂载",
                )
        for item in normalized_mounts:
            rack_slot_code = item["rack_slot_code"]
            bin_code = item["bin_code"]
            _ = await self.rack_bin_mount_repo.create(
                db,
                {
                    "rack_code": rack_code,
                    "rack_slot_code": rack_slot_code,
                    "bin_code": bin_code,
                    "mount_status": RackBinMountStatus.MOUNTED.value,
                    "source_system": source_system.value,
                    "source_event_id": source_event_id,
                    "source_version": source_version,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "started_at": occurred_at_for_db,
                    "ended_at": None,
                },
            )
        _ = await self.snapshot_service.record_empty_bin_snapshots_from_arrived_rack(
            db,
            rack_code=rack_code,
            bin_mounts=normalized_mounts,
            source_session_id=workline_session_id,
            source_event_id=source_event_id,
            captured_at=occurred_at_for_db,
        )
        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_material_mounted_to_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_code: str | None,
        bin_cell_index: str,
        material_identity_key: str,
        pkg_code: str | None,
        material_code: str | None,
        lot_code: str | None,
        date_code: str | None,
        wms_inventory_id: str | None,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        source_version: str | None = None,
        qty_snapshot: float | None = None,
        reel_diameter: str | None = None,
        reel_thickness: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_id: int | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录 OUTPUT_ARM 成功后的物料实际占格事实。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        occurred_at_for_db = _db_time(occurred_at)
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.MATERIAL_MOUNTED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=pkg_code or material_identity_key,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.MATERIAL_MOUNTED.value,
                "resource_type": ResourceType.MATERIAL.value,
                "resource_code": pkg_code or material_identity_key,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "payload_json": {
                    "bin_code": bin_code,
                    "bin_cell_code": bin_cell_code,
                    "bin_cell_index": bin_cell_index,
                    "material_identity_key": material_identity_key,
                    "pkg_code": pkg_code,
                    "material_code": material_code,
                    "lot_code": lot_code,
                    "date_code": date_code,
                    "wms_inventory_id": wms_inventory_id,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        conflict = await self._first_material_mount_conflict(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
            material_identity_key=material_identity_key,
            pkg_code=pkg_code,
            wms_inventory_id=wms_inventory_id,
        )
        if conflict is not None:
            runtime_hold = await self._create_material_mount_conflict_hold(
                db,
                conflict=conflict,
                bin_code=bin_code,
                bin_cell_index=bin_cell_index,
                pkg_code=pkg_code,
                wms_inventory_id=wms_inventory_id,
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=workline_id,
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

        _ = await self.bin_material_mount_repo.create(
            db,
            {
                "bin_code": bin_code,
                "bin_cell_code": bin_cell_code,
                "bin_cell_index": bin_cell_index,
                "material_identity_key": material_identity_key,
                "pkg_code": pkg_code,
                "material_code": material_code,
                "lot_code": lot_code,
                "date_code": date_code,
                "qty_snapshot": qty_snapshot,
                "reel_diameter": reel_diameter,
                "reel_thickness": reel_thickness,
                "wms_inventory_id": wms_inventory_id,
                "mount_status": BinMaterialMountStatus.OCCUPIED.value,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "started_at": occurred_at_for_db,
                "ended_at": None,
            },
        )
        _ = await self.snapshot_service.record_material_mounted_snapshot(
            db,
            bin_code=bin_code,
            bin_cell_code=bin_cell_code,
            bin_cell_index=bin_cell_index,
            pkg_code=pkg_code,
            material_code=material_code,
            lot_code=lot_code,
            date_code=date_code,
            qty_snapshot=qty_snapshot,
            wms_inventory_id=wms_inventory_id,
            source_session_id=workline_session_id,
            source_event_id=source_event_id,
            captured_at=occurred_at_for_db,
        )
        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_resource_fact(
        self,
        *,
        db: AsyncSession,
        session: Any,
        workline: Any,
        fact_type: str,
        payload_json: dict[str, Any],
        idempotency_key: str | None,
        trace_id: str | None = None,
    ) -> ResourceProjectionResult:
        """RuntimeIntent 入口：按 fact_type 路由资源事实。"""

        occurred_at = payload_json.get("occurred_at") or timezone.now_for_db()
        source_system = _as_source_system(payload_json.get("source_system") or ResourceSourceSystem.WES_RUNTIME.value)
        source_event_id = str(payload_json.get("source_event_id") or idempotency_key or fact_type)
        session_id = str(getattr(session, "id", "")) if getattr(session, "id", None) is not None else None
        workline_id = getattr(workline, "id", None)
        workline_code = str(payload_json.get("workline_code") or getattr(workline, "line_code", ""))

        if fact_type == ResourceStateEventType.RACK_ARRIVED.value:
            return await self.record_rack_arrived_at_workline_position(
                db,
                rack_code=str(payload_json["rack_code"]),
                rack_kind=_as_rack_kind(payload_json["rack_kind"]),
                workline_code=workline_code,
                position_code=str(payload_json["position_code"]),
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key
                or f"RACK_ARRIVED:{source_event_id}:{payload_json['rack_code']}:{workline_code}:{payload_json['position_code']}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
                source_task_id=payload_json.get("source_task_id"),
                external_location_code=payload_json.get("external_location_code"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        if fact_type == ResourceStateEventType.BIN_MOUNTED.value:
            return await self.record_bin_mounted_to_rack(
                db,
                rack_code=str(payload_json["rack_code"]),
                bin_mounts=payload_json.get("bin_mounts") or [],
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key or f"BIN_MOUNTED:{source_event_id}:{payload_json['rack_code']}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        if fact_type == ResourceStateEventType.MATERIAL_MOUNTED.value:
            return await self.record_material_mounted_to_bin_cell(
                db,
                bin_code=str(payload_json["bin_code"]),
                bin_cell_code=payload_json.get("bin_cell_code"),
                bin_cell_index=str(payload_json["bin_cell_index"]),
                material_identity_key=str(payload_json["material_identity_key"]),
                pkg_code=payload_json.get("pkg_code"),
                material_code=payload_json.get("material_code"),
                lot_code=payload_json.get("lot_code"),
                date_code=payload_json.get("date_code"),
                wms_inventory_id=payload_json.get("wms_inventory_id"),
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key
                or f"MATERIAL_MOUNTED:{source_event_id}:{payload_json.get('pkg_code')}:{payload_json['bin_code']}:{payload_json['bin_cell_index']}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
                qty_snapshot=payload_json.get("qty_snapshot"),
                reel_diameter=payload_json.get("reel_diameter"),
                reel_thickness=payload_json.get("reel_thickness"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        raise ValueError(f"unsupported resource fact type: {fact_type}")

    async def _first_material_mount_conflict(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_index: str,
        material_identity_key: str,
        pkg_code: str | None,
        wms_inventory_id: str | None,
    ) -> dict[str, Any] | None:
        active_cell = await self.bin_material_mount_repo.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        if active_cell is not None:
            return {
                "reason_code": "BIN_CELL_MATERIAL_MOUNT_CONFLICT",
                "message": "料箱格位已有 active 物料占用",
                "active": active_cell,
            }
        active_identity = await self.bin_material_mount_repo.get_active_by_material_identity(db, material_identity_key)
        if active_identity:
            return {
                "reason_code": "MATERIAL_IDENTITY_MOUNT_CONFLICT",
                "message": "物料身份已有 active 占用",
                "active": active_identity[0],
            }
        if pkg_code:
            active_pkg = await self.bin_material_mount_repo.get_active_by_pkg_code(db, pkg_code)
            if active_pkg is not None:
                return {
                    "reason_code": "PKG_MATERIAL_MOUNT_CONFLICT",
                    "message": "PKG 已有 active 物料占用",
                    "active": active_pkg,
                }
        if wms_inventory_id:
            active_wms = await self.bin_material_mount_repo.get_active_by_wms_inventory_id(db, wms_inventory_id)
            if active_wms is not None:
                return {
                    "reason_code": "WMS_INVENTORY_MATERIAL_MOUNT_CONFLICT",
                    "message": "WMS 库存记录已有 active 物料占用",
                    "active": active_wms,
                }
        return None

    async def _create_placement_conflict_hold(
        self,
        db: AsyncSession,
        *,
        reason_code: str,
        rack_code: str,
        active_placement: RackPlacement,
        incoming: dict[str, Any],
        source_event_id: str,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        if workline_id is None:
            return None
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=reason_code,
            source_event_id=source_event_id,
            evidence={
                "resource_type": ResourceType.RACK.value,
                "rack_code": rack_code,
                "active_workline_code": active_placement.workline_code,
                "active_position_code": active_placement.position_code,
                "incoming_workline_code": incoming.get("workline_code"),
                "incoming_position_code": incoming.get("position_code"),
            },
        )

    async def _create_material_mount_conflict_hold(
        self,
        db: AsyncSession,
        *,
        conflict: dict[str, Any],
        bin_code: str,
        bin_cell_index: str,
        pkg_code: str | None,
        wms_inventory_id: str | None,
        source_event_id: str,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        if workline_id is None:
            return None
        active = conflict["active"]
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=conflict["reason_code"],
            source_event_id=source_event_id,
            evidence={
                "resource_type": ResourceType.MATERIAL.value,
                "bin_code": bin_code,
                "bin_cell_index": bin_cell_index,
                "active_pkg_code": getattr(active, "pkg_code", None),
                "active_wms_inventory_id": getattr(active, "wms_inventory_id", None),
                "incoming_pkg_code": pkg_code,
                "incoming_wms_inventory_id": wms_inventory_id,
            },
        )

    async def _create_rack_bin_mount_conflict_hold(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        rack_slot_code: str,
        bin_code: str,
        active_slot: Any | None,
        active_bin: Any | None,
        source_event_id: str,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
    ) -> Any | None:
        if workline_id is None:
            return None
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason="RACK_BIN_MOUNT_CONFLICT",
            source_event_id=source_event_id,
            evidence={
                "resource_type": ResourceType.BIN.value,
                "rack_code": rack_code,
                "rack_slot_code": rack_slot_code,
                "bin_code": bin_code,
                "active_slot_bin_code": getattr(active_slot, "bin_code", None),
                "active_bin_rack_code": getattr(active_bin, "rack_code", None),
                "active_bin_slot_code": getattr(active_bin, "rack_slot_code", None),
            },
        )


resource_projection_service = ResourceProjectionService()


__all__ = ["ResourceProjectionService", "resource_projection_service"]
