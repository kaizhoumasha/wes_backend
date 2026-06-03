"""资源事实与 active 投影服务。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import isfinite
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.exc import IntegrityError

from src.app.resource.models import (
    BinCellOccupancyStatus,
    BinMaterialMountStatus,
    BinPlacementStatus,
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
    BinCellOccupancyRepository,
    BinMaterialMountRepository,
    BinPlacementRepository,
    RackBinMountRepository,
    RackPlacementRepository,
    ResourceStateEventRepository,
    bin_cell_occupancy_repository,
    bin_material_mount_repository,
    bin_placement_repository,
    rack_bin_mount_repository,
    rack_placement_repository,
    resource_state_event_repository,
)
from src.app.resource.services.material_identity import material_identity_keys_match, material_identity_lookup_keys
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
from src.utils.value_normalization import enum_str

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


def _as_source_system(value: Any) -> ResourceSourceSystem:
    if isinstance(value, ResourceSourceSystem):
        return value
    return ResourceSourceSystem(str(value))


def _as_rack_kind(value: Any) -> RackKind:
    if isinstance(value, RackKind):
        return value
    return RackKind(str(value))


def _db_time(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not isfinite(value):
            return timezone.now_for_db()
        try:
            timestamp = value / 1000 if abs(value) >= 10_000_000_000 else value
            return timezone.to_db_datetime(timestamp) or timezone.now_for_db()
        except (OSError, OverflowError, ValueError):
            return timezone.now_for_db()
    return timezone.to_db_datetime(value) or timezone.now_for_db()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bin_placement_position_mismatch(active_placement: Any, *, position_type: Any, position_code: Any) -> bool:
    expected_type = _optional_text(position_type)
    expected_code = _optional_text(position_code)
    if expected_type is not None and _optional_text(getattr(active_placement, "position_type", None)) != expected_type:
        return True
    return (
        expected_code is not None and _optional_text(getattr(active_placement, "position_code", None)) != expected_code
    )


def _source_version_is_older(incoming_version: Any, active_version: Any) -> bool:
    incoming_text = _optional_text(incoming_version)
    active_text = _optional_text(active_version)
    if incoming_text is None or active_text is None:
        return False
    return _source_version_sort_key(incoming_text) < _source_version_sort_key(active_text)


def _source_version_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _normalized_text_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str | bytes):
        candidates = [values]
    else:
        try:
            candidates = list(values)
        except TypeError:
            candidates = [values]

    result: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _non_negative_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _json_depth_text(value: Any) -> str | None:
    parsed = _non_negative_decimal(value)
    return str(parsed) if parsed is not None else None


def _occupancy_status_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").upper()


class ResourceProjectionService:
    """统一处理资源事实写入、active 投影和冲突 RuntimeHold。"""

    def __init__(
        self,
        *,
        state_event_repo: ResourceStateEventRepository = resource_state_event_repository,
        rack_placement_repo: RackPlacementRepository = rack_placement_repository,
        rack_bin_mount_repo: RackBinMountRepository = rack_bin_mount_repository,
        bin_placement_repo: BinPlacementRepository = bin_placement_repository,
        bin_material_mount_repo: BinMaterialMountRepository = bin_material_mount_repository,
        bin_cell_occupancy_repo: BinCellOccupancyRepository = bin_cell_occupancy_repository,
        rack_position_service: WorklineRackPositionService = workline_rack_position_service,
        runtime_hold_creator: Any = default_runtime_hold_creation_service,
        snapshot_service: ResourceSnapshotService = resource_snapshot_service,
    ) -> None:
        self.state_event_repo = state_event_repo
        self.rack_placement_repo = rack_placement_repo
        self.rack_bin_mount_repo = rack_bin_mount_repo
        self.bin_placement_repo = bin_placement_repo
        self.bin_material_mount_repo = bin_material_mount_repo
        self.bin_cell_occupancy_repo = bin_cell_occupancy_repo
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
        released_rack_codes: Sequence[str] | None = None,
    ) -> ResourceProjectionResult:
        """记录货架到达工作线停靠位事实，并更新 active placement。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        occurred_at_for_db = _db_time(occurred_at)
        normalized_released_rack_codes = [
            released_rack_code
            for released_rack_code in _normalized_text_list(released_rack_codes)
            if released_rack_code != rack_code
        ]
        try:
            position, capacity = await self.rack_position_service.require_position_capacity_for_update(
                db,
                workline_code=workline_code,
                position_code=position_code,
                rack_kind=rack_kind,
            )
        except ValueError as exc:
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
                    "workline_id": workline_id,
                    "workline_code": workline_code,
                    "position_code": position_code,
                    "logic_location_code": None,
                    "external_location_code": external_location_code,
                    "payload_json": {
                        "rack_code": rack_code,
                        "rack_kind": rack_kind.value,
                        "workline_code": workline_code,
                        "position_code": position_code,
                        "source_task_id": source_task_id,
                        "external_location_code": external_location_code,
                        "released_rack_codes": normalized_released_rack_codes,
                        "validation_error": str(exc),
                    },
                    "occurred_at": occurred_at_for_db,
                    "received_at": timezone.now_for_db(),
                },
            )
            reason_code = "WORKLINE_RACK_POSITION_UNAVAILABLE"
            runtime_hold = await self._create_placement_reconciliation_hold(
                db,
                reason_code=reason_code,
                rack_code=rack_code,
                incoming={"workline_code": workline_code, "position_code": position_code},
                active_placements=[],
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
                evidence={"validation_error": str(exc)},
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                runtime_hold=runtime_hold,
                reason_code=reason_code,
                message=f"工作线停靠位不可用，已追加事实但不创建当前投影: {exc}",
            )

        resolved_workline_id = workline_id if workline_id is not None else getattr(position, "workline_id", None)
        resolved_external_location = external_location_code or getattr(position, "external_location_code", None)
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
                    "released_rack_codes": normalized_released_rack_codes,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        active_by_rack = await self.rack_placement_repo.get_active_by_rack_code(db, rack_code)
        if active_by_rack is not None and (
            active_by_rack.workline_code == workline_code and active_by_rack.position_code == position_code
        ):
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.PROJECTED,
                event=event,
                projection=active_by_rack,
            )

        active_placements = await self.rack_placement_repo.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        if normalized_released_rack_codes:
            released_rack_code_set = set(normalized_released_rack_codes)
            remaining_active_placements: list[RackPlacement] = []
            for active_placement in active_placements:
                active_rack_code = str(getattr(active_placement, "rack_code", "")).strip()
                if active_rack_code in released_rack_code_set:
                    active_placement_id = getattr(active_placement, "id", None)
                    if active_placement_id is None:
                        raise ValueError(f"active rack placement missing id: {active_rack_code}")
                    await self.rack_placement_repo.update(
                        db,
                        active_placement_id,
                        {
                            "placement_status": RackPlacementStatus.DEPARTED.value,
                            "ended_at": occurred_at_for_db,
                        },
                    )
                    continue
                remaining_active_placements.append(active_placement)
            active_placements = remaining_active_placements

        active_count = len(active_placements)
        if active_count >= capacity:
            reason_code = "WORKLINE_POSITION_CAPACITY_EXHAUSTED"
            runtime_hold = await self._create_placement_reconciliation_hold(
                db,
                reason_code=reason_code,
                rack_code=rack_code,
                incoming={"workline_code": workline_code, "position_code": position_code},
                active_placements=active_placements,
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=resolved_workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
                evidence={"capacity": capacity, "active_count": active_count},
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                projection=active_placements[0] if active_placements else None,
                runtime_hold=runtime_hold,
                reason_code=reason_code,
                message="工作线停靠位 active 货架数已达到容量，已追加事实但不创建当前投影",
            )

        if active_by_rack is not None:
            active_rack_id = getattr(active_by_rack, "id", None)
            if active_rack_id is None:
                raise ValueError(f"active rack placement missing id: {rack_code}")
            await self.rack_placement_repo.update(
                db,
                active_rack_id,
                {
                    "placement_status": RackPlacementStatus.DEPARTED.value,
                    "ended_at": occurred_at_for_db,
                },
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
                "position_role": enum_str(getattr(position, "position_role", "")),
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
        cell_capacity_depth_mm: Any | None = None,
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
                "workline_id": workline_id,
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
                    "reel_diameter": reel_diameter,
                    "reel_thickness": reel_thickness,
                    "cell_capacity_depth_mm": _json_depth_text(cell_capacity_depth_mm),
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        snapshot_kwargs = {
            "bin_code": bin_code,
            "bin_cell_code": bin_cell_code,
            "bin_cell_index": bin_cell_index,
            "pkg_code": pkg_code,
            "material_code": material_code,
            "lot_code": lot_code,
            "date_code": date_code,
            "qty_snapshot": qty_snapshot,
            "wms_inventory_id": wms_inventory_id,
            "reel_diameter": reel_diameter,
            "reel_thickness": reel_thickness,
            "source_session_id": workline_session_id,
            "source_event_id": source_event_id,
            "captured_at": occurred_at_for_db,
        }
        active_cell = await self.bin_cell_occupancy_repo.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )

        conflict = await self._first_material_mount_conflict(
            db,
            material_identity_key=material_identity_key,
            pkg_code=pkg_code,
            wms_inventory_id=wms_inventory_id,
            active_cell=active_cell,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
            reel_thickness=reel_thickness,
            cell_capacity_depth_mm=cell_capacity_depth_mm,
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

        occupancy = await self._upsert_bin_cell_occupancy(
            db,
            active_cell=active_cell,
            bin_code=bin_code,
            bin_cell_code=bin_cell_code,
            bin_cell_index=bin_cell_index,
            material_identity_key=material_identity_key,
            material_code=material_code,
            lot_code=lot_code,
            date_code=date_code,
            reel_thickness=reel_thickness,
            cell_capacity_depth_mm=cell_capacity_depth_mm,
            source_system=source_system,
            source_event_id=source_event_id,
            source_version=source_version,
            trace_id=trace_id,
            session_id=session_id,
            occurred_at_for_db=occurred_at_for_db,
        )
        occupancy_id = getattr(occupancy, "id", None)
        cell_stack_position = self._current_cell_stack_position(occupancy)

        _ = await self.bin_material_mount_repo.create(
            db,
            {
                "bin_cell_occupancy_id": occupancy_id,
                "cell_stack_position": cell_stack_position,
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
        _ = await self.snapshot_service.record_material_mounted_snapshot(db, **snapshot_kwargs)
        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_material_unmounted_from_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_code: str | None = None,
        bin_cell_index: str,
        material_identity_key: str,
        pkg_code: str | None = None,
        wms_inventory_id: str | None = None,
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
        reel_thickness: str | None = None,
    ) -> ResourceProjectionResult:
        """记录物料从源料格出账事实，并关闭 top active mount。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)

        occurred_at_for_db = _db_time(occurred_at)
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.MATERIAL_UNMOUNTED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=pkg_code or material_identity_key,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.MATERIAL_UNMOUNTED.value,
                "resource_type": ResourceType.MATERIAL.value,
                "resource_code": pkg_code or material_identity_key,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "workline_id": workline_id,
                "payload_json": {
                    "bin_code": bin_code,
                    "bin_cell_code": bin_cell_code,
                    "bin_cell_index": bin_cell_index,
                    "material_identity_key": material_identity_key,
                    "pkg_code": pkg_code,
                    "wms_inventory_id": wms_inventory_id,
                    "reel_thickness": reel_thickness,
                    "source_session_id": workline_session_id,
                    "source_command_id": source_event_id,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        active_mounts = await self.bin_material_mount_repo.list_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        active_mount = active_mounts[0] if active_mounts else None
        active_cell = await self.bin_cell_occupancy_repo.get_active_by_bin_cell(
            db,
            bin_code=bin_code,
            bin_cell_index=bin_cell_index,
        )
        conflict = self._material_unmount_conflict(
            active_mount=active_mount,
            active_cell=active_cell,
            material_identity_key=material_identity_key,
            pkg_code=pkg_code,
            wms_inventory_id=wms_inventory_id,
            source_version=source_version,
            reel_thickness=reel_thickness,
        )
        if conflict is not None:
            runtime_hold = await self._create_material_unmount_reconciliation_hold(
                db,
                reason_code=conflict["reason_code"],
                message=conflict["message"],
                bin_code=bin_code,
                bin_cell_index=bin_cell_index,
                material_identity_key=material_identity_key,
                pkg_code=pkg_code,
                wms_inventory_id=wms_inventory_id,
                active_mount=active_mount,
                active_cell=active_cell,
                source_event_id=source_event_id,
                source_version=source_version,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
                evidence=conflict.get("evidence"),
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                runtime_hold=runtime_hold,
                reason_code=conflict["reason_code"],
                message=conflict["message"],
            )

        active_mount = cast("Any", active_mount)
        active_cell = cast("Any", active_cell)
        active_mount.ended_at = occurred_at_for_db
        active_mount.mount_status = BinMaterialMountStatus.REMOVED
        active_mount.source_system = source_system
        active_mount.source_event_id = source_event_id
        active_mount.source_version = source_version
        active_mount.trace_id = trace_id
        active_mount.session_id = session_id
        _ = await self.bin_material_mount_repo.save(db, active_mount)

        outgoing_depth = _non_negative_decimal(reel_thickness) or _non_negative_decimal(
            getattr(active_mount, "reel_thickness", None)
        )
        current_used_depth = _non_negative_decimal(getattr(active_cell, "used_depth_mm", None)) or Decimal("0")
        active_cell.reel_count = max(int(getattr(active_cell, "reel_count", 0) or 0) - 1, 0)
        if outgoing_depth is not None:
            active_cell.used_depth_mm = max(current_used_depth - outgoing_depth, Decimal("0"))
        else:
            active_cell.used_depth_mm = current_used_depth
        active_cell.remaining_depth_mm = self._remaining_depth(
            capacity_depth=_non_negative_decimal(getattr(active_cell, "capacity_depth_mm", None)),
            used_depth=active_cell.used_depth_mm,
        )
        active_cell.occupancy_status = (
            BinCellOccupancyStatus.REMOVED.value
            if active_cell.reel_count == 0
            else self._occupancy_status(active_cell.remaining_depth_mm)
        )
        active_cell.ended_at = occurred_at_for_db if active_cell.reel_count == 0 else None
        active_cell.source_system = source_system
        active_cell.source_event_id = source_event_id
        active_cell.source_version = source_version
        active_cell.trace_id = trace_id
        active_cell.session_id = session_id
        _ = await self.bin_cell_occupancy_repo.save(db, active_cell)

        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_bin_arrived_at_position(
        self,
        db: AsyncSession,
        *,
        position_type: str,
        position_code: str,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        workline_id: int | None = None,
        workline_code: str | None = None,
        source_version: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录料箱到达非货架位置事实，并创建 active BinPlacement。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)
        if bin_code is None and placeholder_key is None:
            raise ValueError("BIN_ARRIVED requires bin_code or placeholder_key")

        occurred_at_for_db = _db_time(occurred_at)
        resource_code = bin_code or placeholder_key or "UNKNOWN"
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.BIN_ARRIVED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=resource_code,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.BIN_ARRIVED.value,
                "resource_type": ResourceType.BIN.value,
                "resource_code": resource_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "workline_id": workline_id,
                "workline_code": workline_code,
                "position_code": position_code,
                "payload_json": {
                    "bin_code": bin_code,
                    "placeholder_key": placeholder_key,
                    "position_type": position_type,
                    "position_code": position_code,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        if (
            bin_code is not None
            and (active_by_bin := await self.bin_placement_repo.get_active_by_bin_code(db, bin_code, for_update=True))
            is not None
        ):
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_ACTIVE_PLACEMENT_CONFLICT",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=active_by_bin,
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
                reason_code="BIN_ACTIVE_PLACEMENT_CONFLICT",
                message="料箱已有 active 位置投影",
            )
        active_by_placeholder = None
        if placeholder_key is not None:
            active_by_placeholder = await self.bin_placement_repo.get_active_by_placeholder_key(
                db,
                placeholder_key,
                for_update=True,
            )
        if active_by_placeholder is not None:
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_ACTIVE_PLACEMENT_CONFLICT",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=active_by_placeholder,
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
                reason_code="BIN_ACTIVE_PLACEMENT_CONFLICT",
                message="占位键已有 active 位置投影",
            )

        placement_data = {
            "bin_code": bin_code,
            "placeholder_key": placeholder_key,
            "position_type": position_type,
            "position_code": position_code,
            "workline_id": workline_id,
            "workline_code": workline_code,
            "placement_status": BinPlacementStatus.ARRIVED.value,
            "source_system": source_system,
            "source_event_id": source_event_id,
            "source_version": source_version,
            "trace_id": trace_id,
            "session_id": session_id,
            "started_at": occurred_at_for_db,
            "ended_at": None,
            "metadata_json": {},
        }
        try:
            _ = await self._create_bin_placement_with_integrity_guard(db, placement_data)
        except IntegrityError as exc:
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_ACTIVE_PLACEMENT_CONCURRENT_CONFLICT",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=None,
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
                evidence={"integrity_error": str(getattr(exc, "orig", exc))},
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                runtime_hold=runtime_hold,
                reason_code="BIN_ACTIVE_PLACEMENT_CONCURRENT_CONFLICT",
                message="料箱 active 位置投影发生并发唯一冲突",
            )
        return ResourceProjectionResult(status=ResourceProjectionStatus.PROJECTED, event=event)

    async def record_bin_departed_from_position(
        self,
        db: AsyncSession,
        *,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        position_type: str | None = None,
        position_code: str | None = None,
        source_version: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        workline_id: int | None = None,
        workline_session_id: int | None = None,
        plugin_key: str | None = None,
        contract_version: str | None = None,
    ) -> ResourceProjectionResult:
        """记录料箱离开非货架位置事实，并关闭 active BinPlacement。"""

        existing_event = await self._get_duplicate_event(db, idempotency_key=idempotency_key)
        if existing_event is not None:
            return ResourceProjectionResult(status=ResourceProjectionStatus.DUPLICATE, event=existing_event)
        if bin_code is None and placeholder_key is None:
            raise ValueError("BIN_DEPARTED requires bin_code or placeholder_key")

        occurred_at_for_db = _db_time(occurred_at)
        resource_code = bin_code or placeholder_key or "UNKNOWN"
        event = await self.state_event_repo.create(
            db,
            {
                "event_code": self._event_code(
                    event_type=ResourceStateEventType.BIN_DEPARTED,
                    source_system=source_system,
                    source_event_id=source_event_id,
                    resource_code=resource_code,
                ),
                "idempotency_key": idempotency_key,
                "event_type": ResourceStateEventType.BIN_DEPARTED.value,
                "resource_type": ResourceType.BIN.value,
                "resource_code": resource_code,
                "source_system": source_system,
                "source_event_id": source_event_id,
                "source_version": source_version,
                "trace_id": trace_id,
                "session_id": session_id,
                "payload_json": {
                    "bin_code": bin_code,
                    "placeholder_key": placeholder_key,
                    "position_type": position_type,
                    "position_code": position_code,
                },
                "occurred_at": occurred_at_for_db,
                "received_at": timezone.now_for_db(),
            },
        )

        active_placement = None
        if bin_code is not None:
            active_placement = await self.bin_placement_repo.get_active_by_bin_code(db, bin_code, for_update=True)
        if active_placement is None and placeholder_key is not None:
            active_placement = await self.bin_placement_repo.get_active_by_placeholder_key(
                db,
                placeholder_key,
                for_update=True,
            )
        if active_placement is None:
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_ACTIVE_PLACEMENT_MISSING",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=None,
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
                reason_code="BIN_ACTIVE_PLACEMENT_MISSING",
                message="料箱离开事件没有找到 active 位置投影",
            )

        if _bin_placement_position_mismatch(active_placement, position_type=position_type, position_code=position_code):
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_PLACEMENT_POSITION_MISMATCH",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=active_placement,
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
                reason_code="BIN_PLACEMENT_POSITION_MISMATCH",
                message="料箱离开事件位置与 active 投影不一致",
            )
        if _source_version_is_older(source_version, getattr(active_placement, "source_version", None)):
            runtime_hold = await self._create_bin_placement_reconciliation_hold(
                db,
                reason_code="BIN_PLACEMENT_SOURCE_VERSION_STALE",
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                incoming={"position_type": position_type, "position_code": position_code},
                active_placement=active_placement,
                source_event_id=source_event_id,
                trace_id=trace_id,
                session_id=workline_session_id,
                workline_id=workline_id,
                plugin_key=plugin_key,
                contract_version=contract_version,
                evidence={"incoming_source_version": source_version},
            )
            return ResourceProjectionResult(
                status=ResourceProjectionStatus.RECONCILING,
                event=event,
                runtime_hold=runtime_hold,
                reason_code="BIN_PLACEMENT_SOURCE_VERSION_STALE",
                message="料箱离开事件来源版本早于 active 投影版本",
            )

        if bin_code is not None and getattr(active_placement, "bin_code", None) == bin_code:
            _ = await self.bin_placement_repo.close_active_by_bin_code(
                db,
                bin_code,
                ended_at=occurred_at_for_db,
                source_event_id=source_event_id,
            )
        elif placeholder_key is not None:
            _ = await self.bin_placement_repo.close_active_by_placeholder_key(
                db,
                placeholder_key,
                ended_at=occurred_at_for_db,
                source_event_id=source_event_id,
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
                released_rack_codes=payload_json.get("released_rack_codes"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        if fact_type == ResourceStateEventType.BIN_ARRIVED.value:
            return await self.record_bin_arrived_at_position(
                db,
                bin_code=payload_json.get("bin_code"),
                placeholder_key=payload_json.get("placeholder_key"),
                position_type=str(payload_json["position_type"]),
                position_code=str(payload_json["position_code"]),
                workline_id=workline_id,
                workline_code=workline_code,
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key
                or f"BIN_ARRIVED:{source_event_id}:{payload_json.get('bin_code') or payload_json.get('placeholder_key')}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
                trace_id=trace_id,
                session_id=session_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        if fact_type == ResourceStateEventType.BIN_DEPARTED.value:
            return await self.record_bin_departed_from_position(
                db,
                bin_code=payload_json.get("bin_code"),
                placeholder_key=payload_json.get("placeholder_key"),
                position_type=payload_json.get("position_type"),
                position_code=payload_json.get("position_code"),
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key
                or f"BIN_DEPARTED:{source_event_id}:{payload_json.get('bin_code') or payload_json.get('placeholder_key')}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
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
                cell_capacity_depth_mm=payload_json.get("cell_capacity_depth_mm")
                or payload_json.get("capacity_depth_mm"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        if fact_type == ResourceStateEventType.MATERIAL_UNMOUNTED.value:
            return await self.record_material_unmounted_from_bin_cell(
                db,
                bin_code=str(payload_json["bin_code"]),
                bin_cell_code=payload_json.get("bin_cell_code"),
                bin_cell_index=str(payload_json["bin_cell_index"]),
                material_identity_key=str(payload_json["material_identity_key"]),
                pkg_code=payload_json.get("pkg_code"),
                wms_inventory_id=payload_json.get("wms_inventory_id"),
                source_system=source_system,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key
                or f"MATERIAL_UNMOUNTED:{source_event_id}:{payload_json.get('pkg_code')}:{payload_json['bin_code']}:{payload_json['bin_cell_index']}",
                occurred_at=occurred_at,
                source_version=payload_json.get("source_version"),
                reel_thickness=payload_json.get("reel_thickness"),
                trace_id=trace_id,
                session_id=session_id,
                workline_id=workline_id,
                workline_session_id=getattr(session, "id", None),
                plugin_key=getattr(workline, "plugin_key", None),
                contract_version=getattr(workline, "contract_version", None),
            )

        raise ValueError(f"unsupported resource fact type: {fact_type}")

    def _material_unmount_conflict(
        self,
        *,
        active_mount: Any | None,
        active_cell: Any | None,
        material_identity_key: str,
        pkg_code: str | None,
        wms_inventory_id: str | None,
        source_version: str | None,
        reel_thickness: str | None,
    ) -> dict[str, Any] | None:
        conflict: dict[str, Any] | None = None
        if active_mount is None:
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_ACTIVE_MOUNT_MISSING",
                "message": "源料格没有可出账的 active top mount",
            }
        elif active_cell is None:
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT",
                "message": "源料格缺少 active 聚合占用",
            }
        elif not material_identity_keys_match(
            getattr(active_mount, "material_identity_key", None), material_identity_key
        ):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_IDENTITY_MISMATCH",
                "message": "源料格 top mount 物料身份与出账事实不一致",
            }
        elif pkg_code is not None and (
            (active_pkg_code := _optional_text(getattr(active_mount, "pkg_code", None))) is None
            or active_pkg_code != pkg_code
        ):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_IDENTITY_MISMATCH",
                "message": "源料格 top mount PKG 与出账事实不一致",
            }
        elif wms_inventory_id is not None and (
            (active_wms_inventory_id := _optional_text(getattr(active_mount, "wms_inventory_id", None))) is None
            or active_wms_inventory_id != wms_inventory_id
        ):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_IDENTITY_MISMATCH",
                "message": "源料格 top mount WMS 库存与出账事实不一致",
            }
        elif _source_version_is_older(source_version, getattr(active_mount, "source_version", None)):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_SOURCE_VERSION_STALE",
                "message": "物料出账来源版本早于 active top mount 版本",
                "evidence": {"active_source_version": getattr(active_mount, "source_version", None)},
            }
        elif (
            (mount_occupancy_id := getattr(active_mount, "bin_cell_occupancy_id", None)) is not None
            and (active_occupancy_id := getattr(active_cell, "id", None)) is not None
            and mount_occupancy_id != active_occupancy_id
        ):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT",
                "message": "源料格 top mount 与 active 聚合占用不一致",
                "evidence": {
                    "active_occupancy_id": active_occupancy_id,
                    "mount_occupancy_id": mount_occupancy_id,
                },
            }
        elif int(getattr(active_cell, "reel_count", 0) or 0) <= 0:
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT",
                "message": "源料格 active 聚合占用数量异常",
                "evidence": {"active_reel_count": getattr(active_cell, "reel_count", None)},
            }
        elif (
            outgoing_depth := _non_negative_decimal(reel_thickness)
            or _non_negative_decimal(getattr(active_mount, "reel_thickness", None))
        ) is not None and outgoing_depth > (
            _non_negative_decimal(getattr(active_cell, "used_depth_mm", None)) or Decimal("0")
        ):
            conflict = {
                "reason_code": "MATERIAL_UNMOUNTED_OCCUPANCY_INCONSISTENT",
                "message": "源料格 active 已用深度小于出账料盘厚度",
                "evidence": {
                    "active_used_depth_mm": _json_depth_text(getattr(active_cell, "used_depth_mm", None)),
                    "outgoing_reel_thickness": _json_depth_text(outgoing_depth),
                },
            }
        return conflict

    async def _first_material_mount_conflict(
        self,
        db: AsyncSession,
        *,
        material_identity_key: str,
        pkg_code: str | None,
        wms_inventory_id: str | None,
        active_cell: Any | None,
        bin_code: str,
        bin_cell_index: str,
        reel_thickness: str | None,
        cell_capacity_depth_mm: Any | None,
    ) -> dict[str, Any] | None:
        if active_cell is not None:
            active_identity_key = getattr(active_cell, "material_identity_key", None)
            if not material_identity_keys_match(active_identity_key, material_identity_key):
                return {
                    "reason_code": "BIN_CELL_MATERIAL_MOUNT_CONFLICT",
                    "message": "料箱格位已有其他物料 active 聚合占用",
                    "active": active_cell,
                }
            if (
                _occupancy_status_value(getattr(active_cell, "occupancy_status", None))
                == BinCellOccupancyStatus.FULL.value
            ):
                return {
                    "reason_code": "BIN_CELL_FULL",
                    "message": "料箱格位已满，不能继续追加料盘",
                    "active": active_cell,
                }
            capacity_conflict = self._active_cell_capacity_conflict(
                active_cell,
                reel_thickness=reel_thickness,
                cell_capacity_depth_mm=cell_capacity_depth_mm,
            )
            if capacity_conflict is not None:
                return {
                    "reason_code": "BIN_CELL_CAPACITY_EXCEEDED",
                    "message": "料箱格位剩余深度不足，应重新分配新的料格",
                    "active": active_cell,
                    "evidence": capacity_conflict,
                }

        active_identity = await self._list_active_by_material_identity_variants(db, material_identity_key)
        reusable_other_cell = [
            occupancy
            for occupancy in active_identity
            if (
                getattr(occupancy, "bin_code", None),
                str(getattr(occupancy, "bin_cell_index", "")),
            )
            != (bin_code, bin_cell_index)
            and self._active_cell_can_accept_reel(occupancy, reel_thickness=reel_thickness)
        ]
        if reusable_other_cell:
            return {
                "reason_code": "MATERIAL_IDENTITY_MOUNT_CONFLICT",
                "message": "相同物料身份已有未满 active 格位，应继续写入该格位",
                "active": reusable_other_cell[0],
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

    def _active_cell_capacity_conflict(
        self,
        active_cell: Any,
        *,
        reel_thickness: str | None,
        cell_capacity_depth_mm: Any | None,
    ) -> dict[str, Any] | None:
        incoming_depth = _non_negative_decimal(reel_thickness)
        if incoming_depth is None or incoming_depth == 0:
            return None

        active_capacity = _non_negative_decimal(getattr(active_cell, "capacity_depth_mm", None))
        incoming_capacity = _non_negative_decimal(cell_capacity_depth_mm)
        capacity_depth = active_capacity if active_capacity is not None else incoming_capacity
        used_depth = _non_negative_decimal(getattr(active_cell, "used_depth_mm", None))
        remaining_depth = _non_negative_decimal(getattr(active_cell, "remaining_depth_mm", None))

        if remaining_depth is None:
            if capacity_depth is None or used_depth is None:
                return None
            remaining_depth = self._remaining_depth(capacity_depth=capacity_depth, used_depth=used_depth)

        if remaining_depth is None or incoming_depth <= remaining_depth:
            return None

        return {
            "incoming_reel_thickness": _json_depth_text(incoming_depth),
            "remaining_depth_mm": _json_depth_text(remaining_depth),
            "capacity_depth_mm": _json_depth_text(capacity_depth),
            "used_depth_mm": _json_depth_text(used_depth),
            "requires_reallocation": True,
        }

    def _active_cell_can_accept_reel(self, active_cell: Any, *, reel_thickness: str | None) -> bool:
        if _occupancy_status_value(getattr(active_cell, "occupancy_status", None)) == BinCellOccupancyStatus.FULL.value:
            return False
        # 同物料旧格位剩余深度不足时，调度应允许切换到新格位，而不是强制回写旧格位触发 HOLD。
        return (
            self._active_cell_capacity_conflict(
                active_cell,
                reel_thickness=reel_thickness,
                cell_capacity_depth_mm=None,
            )
            is None
        )

    async def _upsert_bin_cell_occupancy(
        self,
        db: AsyncSession,
        *,
        active_cell: Any | None,
        bin_code: str,
        bin_cell_code: str | None,
        bin_cell_index: str,
        material_identity_key: str,
        material_code: str | None,
        lot_code: str | None,
        date_code: str | None,
        reel_thickness: str | None,
        cell_capacity_depth_mm: Any | None,
        source_system: ResourceSourceSystem,
        source_event_id: str,
        source_version: str | None,
        trace_id: str | None,
        session_id: str | None,
        occurred_at_for_db: datetime,
    ) -> Any:
        incoming_depth = _non_negative_decimal(reel_thickness) or Decimal("0")
        incoming_capacity = _non_negative_decimal(cell_capacity_depth_mm)

        if active_cell is None:
            used_depth = incoming_depth
            capacity_depth = incoming_capacity
            remaining_depth = self._remaining_depth(capacity_depth=capacity_depth, used_depth=used_depth)
            return await self.bin_cell_occupancy_repo.create(
                db,
                {
                    "bin_code": bin_code,
                    "bin_cell_code": bin_cell_code,
                    "bin_cell_index": bin_cell_index,
                    "material_identity_key": material_identity_key,
                    "material_code": material_code,
                    "lot_code": lot_code,
                    "date_code": date_code,
                    "reel_count": 1,
                    "used_depth_mm": used_depth,
                    "capacity_depth_mm": capacity_depth,
                    "remaining_depth_mm": remaining_depth,
                    "occupancy_status": self._occupancy_status(remaining_depth),
                    "source_system": source_system,
                    "source_event_id": source_event_id,
                    "source_version": source_version,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "started_at": occurred_at_for_db,
                    "ended_at": None,
                    "metadata_json": {},
                },
            )

        active_cell.reel_count = int(getattr(active_cell, "reel_count", 0) or 0) + 1
        active_cell.material_identity_key = material_identity_key
        active_cell.used_depth_mm = (
            _non_negative_decimal(getattr(active_cell, "used_depth_mm", None)) or Decimal("0")
        ) + incoming_depth
        if incoming_capacity is not None:
            active_cell.capacity_depth_mm = incoming_capacity
        active_cell.remaining_depth_mm = self._remaining_depth(
            capacity_depth=_non_negative_decimal(getattr(active_cell, "capacity_depth_mm", None)),
            used_depth=active_cell.used_depth_mm,
        )
        active_cell.occupancy_status = self._occupancy_status(active_cell.remaining_depth_mm)
        active_cell.source_system = source_system
        active_cell.source_event_id = source_event_id
        active_cell.source_version = source_version
        active_cell.trace_id = trace_id
        active_cell.session_id = session_id
        return await self.bin_cell_occupancy_repo.save(db, active_cell)

    async def _list_active_by_material_identity_variants(
        self,
        db: AsyncSession,
        material_identity_key: str,
    ) -> list[Any]:
        active_identity: list[Any] = []
        seen: set[tuple[Any, str | None, str]] = set()
        for lookup_key in material_identity_lookup_keys(material_identity_key):
            for occupancy in await self.bin_cell_occupancy_repo.list_active_by_material_identity(db, lookup_key):
                identity = (
                    getattr(occupancy, "id", None),
                    getattr(occupancy, "bin_code", None),
                    str(getattr(occupancy, "bin_cell_index", "")),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                active_identity.append(occupancy)
        return active_identity

    def _remaining_depth(self, *, capacity_depth: Decimal | None, used_depth: Decimal) -> Decimal | None:
        if capacity_depth is None:
            return None
        return max(capacity_depth - used_depth, Decimal("0"))

    def _occupancy_status(self, remaining_depth: Decimal | None) -> str:
        if remaining_depth is not None and remaining_depth <= 0:
            return BinCellOccupancyStatus.FULL.value
        return BinCellOccupancyStatus.OCCUPIED.value

    def _current_cell_stack_position(self, occupancy: Any) -> int:
        reel_count = int(getattr(occupancy, "reel_count", 0) or 0)
        return max(reel_count, 1)

    async def _create_bin_placement_with_integrity_guard(
        self,
        db: AsyncSession,
        placement_data: dict[str, Any],
    ) -> Any:
        begin_nested = getattr(db, "begin_nested", None)
        if callable(begin_nested):
            # 唯一索引竞争必须回滚到 savepoint，避免污染外层事实写入事务。
            async with cast("Any", begin_nested)():
                return await self.bin_placement_repo.create(db, placement_data)
        return await self.bin_placement_repo.create(db, placement_data)

    async def _create_bin_placement_reconciliation_hold(
        self,
        db: AsyncSession,
        *,
        reason_code: str,
        bin_code: str | None,
        placeholder_key: str | None,
        incoming: dict[str, Any],
        active_placement: Any | None,
        source_event_id: str,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
        evidence: dict[str, Any] | None = None,
    ) -> Any | None:
        if workline_id is None:
            return None

        hold_evidence = {
            "resource_type": ResourceType.BIN.value,
            "bin_code": bin_code,
            "placeholder_key": placeholder_key,
            "incoming_position_type": incoming.get("position_type"),
            "incoming_position_code": incoming.get("position_code"),
        }
        if active_placement is not None:
            hold_evidence.update(
                {
                    "active_bin_code": getattr(active_placement, "bin_code", None),
                    "active_placeholder_key": getattr(active_placement, "placeholder_key", None),
                    "active_position_type": getattr(active_placement, "position_type", None),
                    "active_position_code": getattr(active_placement, "position_code", None),
                    "active_source_event_id": getattr(active_placement, "source_event_id", None),
                    "active_source_version": getattr(active_placement, "source_version", None),
                }
            )
        hold_evidence.update(evidence or {})
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=reason_code,
            source_event_id=source_event_id,
            evidence=hold_evidence,
        )

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

    async def _create_placement_reconciliation_hold(
        self,
        db: AsyncSession,
        *,
        reason_code: str,
        rack_code: str,
        incoming: dict[str, Any],
        active_placements: Sequence[RackPlacement],
        source_event_id: str,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
        evidence: dict[str, Any] | None = None,
    ) -> Any | None:
        if workline_id is None:
            return None
        active_rack_codes = [placement.rack_code for placement in active_placements]
        hold_evidence = {
            "resource_type": ResourceType.RACK.value,
            "rack_code": rack_code,
            "active_rack_codes": active_rack_codes,
            "incoming_workline_code": incoming.get("workline_code"),
            "incoming_position_code": incoming.get("position_code"),
        }
        hold_evidence.update(evidence or {})
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=reason_code,
            source_event_id=source_event_id,
            evidence=hold_evidence,
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
        evidence = {
            "resource_type": ResourceType.MATERIAL.value,
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "active_bin_code": getattr(active, "bin_code", None),
            "active_bin_cell_index": getattr(active, "bin_cell_index", None),
            "active_material_identity_key": getattr(active, "material_identity_key", None),
            "active_pkg_code": getattr(active, "pkg_code", None),
            "active_wms_inventory_id": getattr(active, "wms_inventory_id", None),
            "incoming_pkg_code": pkg_code,
            "incoming_wms_inventory_id": wms_inventory_id,
        }
        evidence.update(conflict.get("evidence") or {})
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=conflict["reason_code"],
            source_event_id=source_event_id,
            evidence=evidence,
        )

    async def _create_material_unmount_reconciliation_hold(
        self,
        db: AsyncSession,
        *,
        reason_code: str,
        message: str,
        bin_code: str,
        bin_cell_index: str,
        material_identity_key: str,
        pkg_code: str | None,
        wms_inventory_id: str | None,
        active_mount: Any | None,
        active_cell: Any | None,
        source_event_id: str,
        source_version: str | None,
        trace_id: str | None,
        session_id: int | None,
        workline_id: int | None,
        plugin_key: str | None,
        contract_version: str | None,
        evidence: dict[str, Any] | None = None,
    ) -> Any | None:
        if workline_id is None:
            return None
        hold_evidence = {
            "resource_type": ResourceType.MATERIAL.value,
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "source_session_id": session_id,
            "source_event_id": source_event_id,
            "source_command_id": source_event_id,
            "source_version": source_version,
            "expected_material_identity_key": material_identity_key,
            "expected_pkg_code": pkg_code,
            "expected_wms_inventory_id": wms_inventory_id,
            "message": message,
        }
        if active_mount is not None:
            hold_evidence.update(
                {
                    "active_mount_id": getattr(active_mount, "id", None),
                    "active_material_identity_key": getattr(active_mount, "material_identity_key", None),
                    "active_pkg_code": getattr(active_mount, "pkg_code", None),
                    "active_wms_inventory_id": getattr(active_mount, "wms_inventory_id", None),
                    "active_cell_stack_position": getattr(active_mount, "cell_stack_position", None),
                    "active_source_event_id": getattr(active_mount, "source_event_id", None),
                    "active_source_version": getattr(active_mount, "source_version", None),
                }
            )
        if active_cell is not None:
            hold_evidence.update(
                {
                    "active_occupancy_id": getattr(active_cell, "id", None),
                    "active_reel_count": getattr(active_cell, "reel_count", None),
                    "active_used_depth_mm": _json_depth_text(getattr(active_cell, "used_depth_mm", None)),
                    "active_remaining_depth_mm": _json_depth_text(getattr(active_cell, "remaining_depth_mm", None)),
                }
            )
        hold_evidence.update(evidence or {})
        return await self.runtime_hold_creator.create_for_resource_reconciliation(
            db,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            source_reason=reason_code,
            source_event_id=source_event_id,
            evidence=hold_evidence,
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
