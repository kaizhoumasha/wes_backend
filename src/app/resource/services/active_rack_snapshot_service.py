"""SMT 当前货架快照恢复服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from src.app.resource.repositories import (
    BinCellOccupancyRepository,
    BinMaterialMountRepository,
    RackBinMountRepository,
    RackPlacementRepository,
    bin_cell_occupancy_repository,
    bin_material_mount_repository,
    rack_bin_mount_repository,
    rack_placement_repository,
)
from src.app.workline.repositories import (
    WorklineBinCellReservationRepository,
    workline_bin_cell_reservation_repository,
)
from src.app.workline.repositories.session_repository import WorklineSessionRepository

DEFAULT_SMT_RACK_POSITION_CODE = "SINGLE_LAYER_A"
DERIVED_CELL_OCCUPANCY_FIELDS = (
    "DateCode",
    "LotCode",
    "PkgID",
    "HHPN",
    "MfrPN",
    "Qty",
    "material_code",
    "vendor_code",
    "date_code",
    "lot_code",
    "pkg_code",
    "qty_snapshot",
    "reel_count",
    "used_depth_mm",
    "stack_depth_mm",
    "remaining_depth_mm",
    "remaining_depth",
    "available_depth_mm",
    "material_identity_key",
    "reels",
    "reel_diameter",
    "reel_thickness",
    "wms_inventory_id",
    "bin_cell_occupancy_id",
    "occupancy_id",
    "occupancy_status",
)
UNAVAILABLE_EMPTY_CELL_STATUSES = frozenset({"LOCKED", "DISABLED", "EXCEPTION"})


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text or None


def _cleared_cell_status(value: Any) -> str:
    status = (_text_or_none(value) or "").upper()
    if status in UNAVAILABLE_EMPTY_CELL_STATUSES:
        return status
    return "EMPTY"


def _cell_index(cell: Mapping[str, Any]) -> str | None:
    value = _text_or_none(cell.get("bin_cell_index"))
    if value is not None:
        return value

    location = _text_or_none(cell.get("bin_cell_location"))
    if location is None:
        return None
    return location.rsplit("-", maxsplit=1)[-1].rsplit("_", maxsplit=1)[-1] or None


def _bin_code(cell: Mapping[str, Any]) -> str | None:
    return _text_or_none(cell.get("bin_code")) or _text_or_none(cell.get("bin_id"))


def _slot_code(cell: Mapping[str, Any]) -> str | None:
    return _text_or_none(cell.get("rack_slot_code")) or _text_or_none(cell.get("slot_code"))


def _positive_int(value: Any) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _mount_stack_position(mount: Any) -> int:
    return _positive_int(getattr(mount, "cell_stack_position", None))


@dataclass(frozen=True, slots=True)
class SmtActiveRackSnapshotProvider:
    """绑定当前 DB 会话和工作线的运行时快照 provider。"""

    service: SmtActiveRackSnapshotService
    db: Any
    workline: Any

    async def active_bin_rack(self, *, context: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
        """恢复当前 SMT active_bin_rack。"""

        return await self.service.get_active_bin_rack(self.db, workline=self.workline, context=context)


class SmtActiveRackSnapshotService:
    """从资源 active 投影和最近会话结构恢复 SMT 当前货架快照。"""

    def __init__(
        self,
        *,
        rack_placement_repo: RackPlacementRepository = rack_placement_repository,
        rack_bin_mount_repo: RackBinMountRepository = rack_bin_mount_repository,
        bin_cell_occupancy_repo: BinCellOccupancyRepository = bin_cell_occupancy_repository,
        bin_material_mount_repo: BinMaterialMountRepository = bin_material_mount_repository,
        bin_cell_reservation_repo: WorklineBinCellReservationRepository = workline_bin_cell_reservation_repository,
        session_repo: WorklineSessionRepository | None = None,
    ) -> None:
        self.rack_placement_repo = rack_placement_repo
        self.rack_bin_mount_repo = rack_bin_mount_repo
        self.bin_cell_occupancy_repo = bin_cell_occupancy_repo
        self.bin_material_mount_repo = bin_material_mount_repo
        self.bin_cell_reservation_repo = bin_cell_reservation_repo
        self.session_repo = session_repo or WorklineSessionRepository()

    def bind(self, *, db: Any, workline: Any) -> SmtActiveRackSnapshotProvider:
        """绑定 DB 与工作线，供运行时服务容器注入。"""

        return SmtActiveRackSnapshotProvider(service=self, db=db, workline=workline)

    async def get_active_bin_rack(
        self,
        db: Any,
        *,
        workline: Any,
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """恢复当前工作线 active_bin_rack。

        资源投影只保存 active rack/bin/material 关系，不保存每个料箱的完整格位模板；
        因此这里使用最近一次会话中 WMS 回传的 active_bin_rack 作为结构模板，
        再用当前 active 投影覆盖实际占用状态。
        """

        runtime_context = dict(context or {})
        workline_code = self._workline_code(workline, runtime_context)
        if workline_code is None:
            return None

        position_code = self._position_code(runtime_context)
        placements = await self.rack_placement_repo.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        if len(placements) != 1:
            return None
        placement = placements[0]
        rack_code = _text_or_none(getattr(placement, "rack_code", None))
        if rack_code is None:
            return None

        rack_bin_mounts = await self.rack_bin_mount_repo.list_active_by_rack_code(db, rack_code)
        if not rack_bin_mounts:
            return None

        base_snapshot = self._active_rack_from_context(runtime_context, rack_code)
        if base_snapshot is None:
            base_snapshot = await self._latest_session_active_rack(db, workline=workline, rack_code=rack_code)
        if base_snapshot is None:
            return None

        snapshot = dict(cast("Mapping[str, Any]", deepcopy(base_snapshot)))
        snapshot["rack_code"] = rack_code
        snapshot.setdefault("rack_id", rack_code)
        cells = self._cells(snapshot)
        if not cells or not self._rack_bin_mounts_match_cells(rack_bin_mounts, cells):
            return None

        bin_codes = [_text_or_none(getattr(mount, "bin_code", None)) for mount in rack_bin_mounts]
        active_occupancies = await self.bin_cell_occupancy_repo.list_active_by_bin_codes(
            db,
            [bin_code for bin_code in bin_codes if bin_code is not None],
        )
        active_material_mounts = await self.bin_material_mount_repo.list_active_by_bin_codes(
            db,
            [bin_code for bin_code in bin_codes if bin_code is not None],
        )
        active_reservations = await self.bin_cell_reservation_repo.list_active_by_bin_codes(
            db,
            [bin_code for bin_code in bin_codes if bin_code is not None],
        )
        self._overlay_occupancies(cells, active_occupancies, active_material_mounts)
        self._overlay_active_reservations(cells, active_reservations)
        self._remove_derived_bin_snapshots(snapshot)
        return snapshot

    def _workline_code(self, workline: Any, context: Mapping[str, Any]) -> str | None:
        return (
            _text_or_none(context.get("workline_code"))
            or _text_or_none(context.get("line_code"))
            or _text_or_none(getattr(workline, "line_code", None))
            or _text_or_none(getattr(workline, "workline_code", None))
        )

    def _position_code(self, context: Mapping[str, Any]) -> str:
        rack_operation = context.get("rack_operation")
        if isinstance(rack_operation, Mapping):
            value = (
                _text_or_none(rack_operation.get("work_position_code"))
                or _text_or_none(rack_operation.get("target_position_code"))
                or _text_or_none(rack_operation.get("position_code"))
            )
            if value is not None:
                return value

        return (
            _text_or_none(context.get("work_position_code"))
            or _text_or_none(context.get("target_position_code"))
            or _text_or_none(context.get("position_code"))
            or DEFAULT_SMT_RACK_POSITION_CODE
        )

    def _active_rack_from_context(
        self,
        context: Mapping[str, Any],
        rack_code: str,
    ) -> Mapping[str, Any] | None:
        active_rack = context.get("active_bin_rack")
        return self._matching_active_rack(active_rack, rack_code)

    async def _latest_session_active_rack(
        self,
        db: Any,
        *,
        workline: Any,
        rack_code: str,
    ) -> Mapping[str, Any] | None:
        workline_id = getattr(workline, "id", None)
        if workline_id is None:
            return None

        session = await self.session_repo.get_latest_active_rack_template_session(
            db,
            workline_id=int(workline_id),
            rack_code=rack_code,
        )
        session_context = getattr(session, "context_json", None)
        if not isinstance(session_context, Mapping):
            return None
        return self._matching_active_rack(session_context.get("active_bin_rack"), rack_code)

    def _matching_active_rack(self, active_rack: Any, rack_code: str) -> Mapping[str, Any] | None:
        if not isinstance(active_rack, Mapping):
            return None
        active_rack_map = cast("Mapping[str, Any]", active_rack)
        active_rack_code = _text_or_none(active_rack_map.get("rack_code")) or _text_or_none(
            active_rack_map.get("rack_id")
        )
        if active_rack_code != rack_code:
            return None
        return active_rack_map if self._cells(active_rack_map) else None

    def _cells(self, active_rack: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_cells = active_rack.get("cells") or active_rack.get("bin_cells") or active_rack.get("cell_snapshots")
        if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
            return []
        return [cast("dict[str, Any]", cell) for cell in raw_cells if isinstance(cell, dict)]

    def _rack_bin_mounts_match_cells(self, rack_bin_mounts: Sequence[Any], cells: Sequence[Mapping[str, Any]]) -> bool:
        mounted_bins_by_slot = {
            _text_or_none(getattr(mount, "rack_slot_code", None)): _text_or_none(getattr(mount, "bin_code", None))
            for mount in rack_bin_mounts
        }
        if None in mounted_bins_by_slot or not mounted_bins_by_slot:
            return False

        cells_by_slot: dict[str, list[Mapping[str, Any]]] = {}
        for cell in cells:
            slot = _slot_code(cell)
            if slot is not None:
                cells_by_slot.setdefault(slot, []).append(cell)
        if set(cells_by_slot) != set(mounted_bins_by_slot):
            return False

        for slot, bin_code in mounted_bins_by_slot.items():
            slot_cells = cells_by_slot.get(cast("str", slot))
            if not slot_cells:
                return False
            if any(_bin_code(cell) != bin_code for cell in slot_cells):
                return False
        return True

    def _remove_derived_bin_snapshots(self, snapshot: dict[str, Any]) -> None:
        # cells 覆盖后才是当前投影状态；顶层料箱快照是派生值，
        # 保留模板旧值会让调度器优先读取 stale usage。
        snapshot.pop("bins", None)
        snapshot.pop("bin_snapshots", None)

    def _overlay_occupancies(
        self,
        cells: Sequence[dict[str, Any]],
        occupancies: Sequence[Any],
        material_mounts: Sequence[Any],
    ) -> None:
        occupancies_by_cell: dict[tuple[str, str], Any] = {}
        occupancy_ids_by_cell: dict[tuple[str, str], Any] = {}
        for occupancy in occupancies:
            bin_code = _text_or_none(getattr(occupancy, "bin_code", None))
            cell_index = _text_or_none(getattr(occupancy, "bin_cell_index", None))
            if bin_code is not None and cell_index is not None:
                key = (bin_code, cell_index)
                occupancies_by_cell[key] = occupancy
                occupancy_ids_by_cell[key] = getattr(occupancy, "id", None)

        mounts_by_occupancy_id: dict[Any, list[Any]] = {}
        mounts_by_cell: dict[tuple[str, str], list[Any]] = {}
        for mount in material_mounts:
            occupancy_id = getattr(mount, "bin_cell_occupancy_id", None)
            if occupancy_id is not None:
                mounts_by_occupancy_id.setdefault(occupancy_id, []).append(mount)
            bin_code = _text_or_none(getattr(mount, "bin_code", None))
            cell_index = _text_or_none(getattr(mount, "bin_cell_index", None))
            if bin_code is not None and cell_index is not None:
                mounts_by_cell.setdefault((bin_code, cell_index), []).append(mount)

        for cell in cells:
            bin_code = _bin_code(cell)
            cell_index = _cell_index(cell)
            if bin_code is None or cell_index is None:
                continue
            key = (bin_code, cell_index)
            occupancy = occupancies_by_cell.get(key)
            if occupancy is None:
                self._clear_cell_occupancy(cell, bin_code=bin_code, cell_index=cell_index)
                continue
            occupancy_id = occupancy_ids_by_cell.get(key)
            mounts = mounts_by_occupancy_id.get(occupancy_id) or mounts_by_cell.get(key) or []
            lifo_mounts = sorted(
                mounts,
                key=lambda mount: (_mount_stack_position(mount), _positive_int(getattr(mount, "id", None))),
                reverse=True,
            )
            latest_mount = lifo_mounts[0] if lifo_mounts else None

            cell["status"] = _text_or_none(getattr(occupancy, "occupancy_status", None)) or "OCCUPIED"
            cell["bin_code"] = bin_code
            cell["bin_id"] = bin_code
            cell["bin_cell_index"] = cell_index
            cell["DateCode"] = getattr(occupancy, "date_code", None)
            cell["LotCode"] = getattr(occupancy, "lot_code", None)
            cell["PkgID"] = getattr(latest_mount, "pkg_code", None) if latest_mount is not None else None
            cell["HHPN"] = getattr(occupancy, "material_code", None)
            cell["Qty"] = getattr(latest_mount, "qty_snapshot", None) if latest_mount is not None else None
            cell["reel_count"] = getattr(occupancy, "reel_count", None)
            cell["used_depth_mm"] = getattr(occupancy, "used_depth_mm", None)
            cell["capacity_depth_mm"] = getattr(occupancy, "capacity_depth_mm", None)
            cell["remaining_depth_mm"] = getattr(occupancy, "remaining_depth_mm", None)
            cell["material_identity_key"] = getattr(occupancy, "material_identity_key", None)
            cell["reels"] = [
                {
                    "pkg_code": getattr(mount, "pkg_code", None),
                    "cell_stack_position": getattr(mount, "cell_stack_position", None),
                    "reel_diameter": getattr(mount, "reel_diameter", None),
                    "reel_thickness": getattr(mount, "reel_thickness", None),
                    "qty_snapshot": getattr(mount, "qty_snapshot", None),
                    "wms_inventory_id": getattr(mount, "wms_inventory_id", None),
                }
                for mount in lifo_mounts
            ]
            if latest_mount is not None:
                cell["reel_diameter"] = getattr(latest_mount, "reel_diameter", None)
                cell["reel_thickness"] = getattr(latest_mount, "reel_thickness", None)
                cell["wms_inventory_id"] = getattr(latest_mount, "wms_inventory_id", None)

    def _clear_cell_occupancy(self, cell: dict[str, Any], *, bin_code: str, cell_index: str) -> None:
        # 结构模板可能来自旧 session；没有 active occupancy 时必须去掉旧物料派生状态。
        # 但 LOCKED/DISABLED/EXCEPTION 表示格位不可调度，不能因没有物料占用而清成空格。
        status = _cleared_cell_status(cell.get("status"))
        for field in DERIVED_CELL_OCCUPANCY_FIELDS:
            cell.pop(field, None)
        cell["status"] = status
        cell["bin_code"] = bin_code
        cell["bin_id"] = bin_code
        cell["bin_cell_index"] = cell_index

    def _overlay_active_reservations(self, cells: Sequence[dict[str, Any]], reservations: Sequence[Any]) -> None:
        reservations_by_cell: dict[tuple[str, str], Any] = {}
        for reservation in reservations:
            bin_code = _text_or_none(getattr(reservation, "bin_code", None))
            cell_index = _text_or_none(getattr(reservation, "bin_cell_index", None))
            if bin_code is not None and cell_index is not None:
                reservations_by_cell[(bin_code, cell_index)] = reservation

        for cell in cells:
            bin_code = _bin_code(cell)
            cell_index = _cell_index(cell)
            if bin_code is None or cell_index is None:
                continue
            reservation = reservations_by_cell.get((bin_code, cell_index))
            if reservation is None:
                continue
            metadata = getattr(reservation, "metadata_json", None)
            material_identity_key = metadata.get("material_identity_key") if isinstance(metadata, Mapping) else None
            cell["status"] = "LOCKED"
            cell["locked"] = True
            cell["bin_code"] = bin_code
            cell["bin_id"] = bin_code
            cell["bin_cell_index"] = cell_index
            cell["reservation_status"] = "PLANNED"
            cell["reservation_session_id"] = getattr(reservation, "session_id", None)
            cell["reserved_pkg_code"] = getattr(reservation, "pkg_code", None)
            if material_identity_key is not None:
                cell["reserved_material_identity_key"] = material_identity_key


smt_active_rack_snapshot_service = SmtActiveRackSnapshotService()


__all__ = [
    "SmtActiveRackSnapshotProvider",
    "SmtActiveRackSnapshotService",
    "smt_active_rack_snapshot_service",
]
