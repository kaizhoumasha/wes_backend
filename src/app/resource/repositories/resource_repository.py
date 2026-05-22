"""Resource Repository 层。"""

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.resource.models import (
    Bin,
    BinCellOccupancy,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinMaterialMount,
    BinPlacement,
    BinSlotTemplate,
    BinType,
    Rack,
    RackBinMount,
    RackPlacement,
    RackSlotTemplate,
    RackType,
    ResourceSourceSystem,
    ResourceStateEvent,
)
from src.database.base_repository import BaseRepository


class RackTypeRepository(BaseRepository[RackType]):
    """货架类型 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackType)


class RackSlotTemplateRepository(BaseRepository[RackSlotTemplate]):
    """货架槽位模板 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackSlotTemplate)

    async def get_by_type_and_slot(
        self,
        db: AsyncSession,
        *,
        rack_type_code: str,
        slot_code: str,
    ) -> RackSlotTemplate | None:
        """按货架类型和槽位编码查询模板。"""

        columns = cast("Any", RackSlotTemplate).__table__.c
        result = await db.execute(
            select(RackSlotTemplate).where(
                columns.rack_type_code == rack_type_code,
                columns.slot_code == slot_code,
            )
        )
        return result.scalar_one_or_none()


class RackRepository(BaseRepository[Rack]):
    """货架实例 Repository。"""

    def __init__(self) -> None:
        super().__init__(Rack)

    async def get_by_rack_code(self, db: AsyncSession, rack_code: str) -> Rack | None:
        """按 rack_code 查询货架。"""

        columns = cast("Any", Rack).__table__.c
        result = await db.execute(
            select(Rack).where(
                columns.rack_code == rack_code,
            )
        )
        return result.scalar_one_or_none()


class BinTypeRepository(BaseRepository[BinType]):
    """料箱类型 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinType)


class BinSlotTemplateRepository(BaseRepository[BinSlotTemplate]):
    """料箱槽位模板 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinSlotTemplate)


class BinRepository(BaseRepository[Bin]):
    """料箱实例 Repository。"""

    def __init__(self) -> None:
        super().__init__(Bin)

    async def get_by_bin_code(self, db: AsyncSession, bin_code: str) -> Bin | None:
        """按 bin_code 查询料箱。"""

        columns = cast("Any", Bin).__table__.c
        result = await db.execute(
            select(Bin).where(
                columns.bin_code == bin_code,
            )
        )
        return result.scalar_one_or_none()


class ResourceStateEventRepository(BaseRepository[ResourceStateEvent]):
    """资源事实 Repository。"""

    def __init__(self) -> None:
        super().__init__(ResourceStateEvent)

    async def get_by_source_event_id(
        self,
        db: AsyncSession,
        *,
        source_system: ResourceSourceSystem,
        source_event_id: str,
    ) -> ResourceStateEvent | None:
        """按来源事件幂等键查询资源事实。"""

        columns = cast("Any", ResourceStateEvent).__table__.c
        result = await db.execute(
            select(ResourceStateEvent).where(
                columns.source_system == source_system,
                columns.source_event_id == source_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, db: AsyncSession, idempotency_key: str) -> ResourceStateEvent | None:
        """按资源事实显式幂等键查询。"""

        columns = cast("Any", ResourceStateEvent).__table__.c
        result = await db.execute(
            select(ResourceStateEvent).where(
                columns.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()


class RackPlacementRepository(BaseRepository[RackPlacement]):
    """货架位置投影 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackPlacement)

    async def get_active_by_rack_code(self, db: AsyncSession, rack_code: str) -> RackPlacement | None:
        """查询货架当前 active placement。"""

        columns = cast("Any", RackPlacement).__table__.c
        result = await db.execute(
            select(RackPlacement).where(
                columns.rack_code == rack_code,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_first_active_by_workline_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> RackPlacement | None:
        """查询工作线停靠位第一条 active placement。

        仅用于 legacy/diagnostic 场景；容量判断必须使用 list/count 方法。
        """

        columns = cast("Any", RackPlacement).__table__.c
        result = await db.execute(
            select(RackPlacement)
            .where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
                columns.ended_at.is_(None),
            )
            .order_by(columns.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_active_by_workline_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> list[RackPlacement]:
        """查询工作线停靠位当前 active placements。"""

        columns = cast("Any", RackPlacement).__table__.c
        result = await db.execute(
            select(RackPlacement)
            .where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
                columns.ended_at.is_(None),
            )
            .order_by(columns.id.asc())
        )
        return list(result.scalars().all())

    async def count_active_by_workline_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> int:
        """统计工作线停靠位当前 active placement 数量。"""

        columns = cast("Any", RackPlacement).__table__.c
        result = await db.execute(
            select(func.count())
            .select_from(RackPlacement)
            .where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
                columns.ended_at.is_(None),
            )
        )
        return int(result.scalar_one())


class RackBinMountRepository(BaseRepository[RackBinMount]):
    """料箱挂载投影 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackBinMount)

    async def get_active_by_rack_slot(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        rack_slot_code: str,
    ) -> RackBinMount | None:
        """查询货架槽位当前 active bin mount。"""

        columns = cast("Any", RackBinMount).__table__.c
        result = await db.execute(
            select(RackBinMount).where(
                columns.rack_code == rack_code,
                columns.rack_slot_code == rack_slot_code,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_bin_code(self, db: AsyncSession, bin_code: str) -> RackBinMount | None:
        """查询料箱当前 active mount。"""

        columns = cast("Any", RackBinMount).__table__.c
        result = await db.execute(
            select(RackBinMount).where(
                columns.bin_code == bin_code,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_active_by_rack_code(self, db: AsyncSession, rack_code: str) -> list[RackBinMount]:
        """查询货架当前 active bin mounts。"""

        columns = cast("Any", RackBinMount).__table__.c
        result = await db.execute(
            select(RackBinMount)
            .where(
                columns.rack_code == rack_code,
                columns.ended_at.is_(None),
            )
            .order_by(columns.rack_slot_code.asc())
        )
        return list(result.scalars().all())


class BinPlacementRepository(BaseRepository[BinPlacement]):
    """料箱非货架位置投影 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinPlacement)

    async def get_active_by_bin_code(
        self,
        db: AsyncSession,
        bin_code: str,
        *,
        for_update: bool = False,
    ) -> BinPlacement | None:
        columns = cast("Any", BinPlacement).__table__.c
        statement = select(BinPlacement).where(
            columns.bin_code == bin_code,
            columns.ended_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_by_placeholder_key(
        self,
        db: AsyncSession,
        placeholder_key: str,
        *,
        for_update: bool = False,
    ) -> BinPlacement | None:
        columns = cast("Any", BinPlacement).__table__.c
        statement = select(BinPlacement).where(
            columns.placeholder_key == placeholder_key,
            columns.ended_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def close_active_by_bin_code(
        self,
        db: AsyncSession,
        bin_code: str,
        *,
        ended_at: Any,
        source_event_id: str,
    ) -> int:
        active = await self.get_active_by_bin_code(db, bin_code, for_update=True)
        if active is None:
            return 0
        active.ended_at = ended_at
        active.placement_status = "DEPARTED"
        active.metadata_json = {**(active.metadata_json or {}), "departed_source_event_id": source_event_id}
        db.add(active)
        await db.flush()
        return 1

    async def close_active_by_placeholder_key(
        self,
        db: AsyncSession,
        placeholder_key: str,
        *,
        ended_at: Any,
        source_event_id: str,
    ) -> int:
        active = await self.get_active_by_placeholder_key(db, placeholder_key, for_update=True)
        if active is None:
            return 0
        active.ended_at = ended_at
        active.placement_status = "DEPARTED"
        active.metadata_json = {**(active.metadata_json or {}), "departed_source_event_id": source_event_id}
        db.add(active)
        await db.flush()
        return 1


class BinMaterialMountRepository(BaseRepository[BinMaterialMount]):
    """料盘/PKG 料箱格位明细 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinMaterialMount)

    async def list_active_by_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> list[BinMaterialMount]:
        """查询料箱格位当前 active material mount 明细。"""

        columns = cast("Any", BinMaterialMount).__table__.c
        result = await db.execute(
            select(BinMaterialMount)
            .where(
                columns.bin_code == bin_code,
                columns.bin_cell_index == bin_cell_index,
                columns.ended_at.is_(None),
            )
            .order_by(columns.cell_stack_position.desc(), columns.id.desc())
        )
        return list(result.scalars().all())

    async def get_active_by_pkg_code(self, db: AsyncSession, pkg_code: str) -> BinMaterialMount | None:
        """查询 PKG 当前 active material mount。"""

        columns = cast("Any", BinMaterialMount).__table__.c
        result = await db.execute(
            select(BinMaterialMount).where(
                columns.pkg_code == pkg_code,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_wms_inventory_id(
        self,
        db: AsyncSession,
        wms_inventory_id: str,
    ) -> BinMaterialMount | None:
        """查询 WMS 库存记录当前 active material mount。"""

        columns = cast("Any", BinMaterialMount).__table__.c
        result = await db.execute(
            select(BinMaterialMount).where(
                columns.wms_inventory_id == wms_inventory_id,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_material_identity(
        self,
        db: AsyncSession,
        material_identity_key: str,
    ) -> list[BinMaterialMount]:
        """按物料属性身份查询 active material mounts。"""

        columns = cast("Any", BinMaterialMount).__table__.c
        result = await db.execute(
            select(BinMaterialMount).where(
                columns.material_identity_key == material_identity_key,
                columns.ended_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def list_active_by_bin_codes(self, db: AsyncSession, bin_codes: list[str]) -> list[BinMaterialMount]:
        """按料箱编码批量查询 active material mounts。"""

        unique_bin_codes = list(dict.fromkeys(bin_codes))
        if not unique_bin_codes:
            return []

        columns = cast("Any", BinMaterialMount).__table__.c
        result = await db.execute(
            select(BinMaterialMount)
            .where(
                columns.bin_code.in_(unique_bin_codes),
                columns.ended_at.is_(None),
            )
            .order_by(
                columns.bin_code.asc(),
                columns.bin_cell_index.asc(),
                columns.cell_stack_position.desc(),
                columns.id.desc(),
            )
        )
        return list(result.scalars().all())


class BinCellOccupancyRepository(BaseRepository[BinCellOccupancy]):
    """料箱格位聚合占用 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinCellOccupancy)

    async def get_active_by_bin_cell(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_index: str,
    ) -> BinCellOccupancy | None:
        """查询料箱格位当前 active 聚合占用。"""

        columns = cast("Any", BinCellOccupancy).__table__.c
        result = await db.execute(
            select(BinCellOccupancy).where(
                columns.bin_code == bin_code,
                columns.bin_cell_index == bin_cell_index,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_active_by_material_identity(
        self,
        db: AsyncSession,
        material_identity_key: str,
    ) -> list[BinCellOccupancy]:
        """按物料属性身份查询 active 料格聚合占用。"""

        columns = cast("Any", BinCellOccupancy).__table__.c
        result = await db.execute(
            select(BinCellOccupancy)
            .where(
                columns.material_identity_key == material_identity_key,
                columns.ended_at.is_(None),
            )
            .order_by(columns.bin_code.asc(), columns.bin_cell_index.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_active_by_bin_codes(self, db: AsyncSession, bin_codes: list[str]) -> list[BinCellOccupancy]:
        """按料箱编码批量查询 active 料格聚合占用。"""

        unique_bin_codes = list(dict.fromkeys(bin_codes))
        if not unique_bin_codes:
            return []

        columns = cast("Any", BinCellOccupancy).__table__.c
        result = await db.execute(
            select(BinCellOccupancy)
            .where(
                columns.bin_code.in_(unique_bin_codes),
                columns.ended_at.is_(None),
            )
            .order_by(columns.bin_code.asc(), columns.bin_cell_index.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def save(self, db: AsyncSession, occupancy: BinCellOccupancy) -> BinCellOccupancy:
        """保存已变更的料格聚合占用。"""

        db.add(occupancy)
        await db.flush()
        await db.refresh(occupancy)
        return occupancy


class BinContentSnapshotRepository(BaseRepository[BinContentSnapshot]):
    """料箱内容快照头 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinContentSnapshot)


class BinContentSnapshotItemRepository(BaseRepository[BinContentSnapshotItem]):
    """料箱内容快照明细 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinContentSnapshotItem)


rack_type_repository = RackTypeRepository()
rack_slot_template_repository = RackSlotTemplateRepository()
rack_repository = RackRepository()
bin_type_repository = BinTypeRepository()
bin_slot_template_repository = BinSlotTemplateRepository()
bin_repository = BinRepository()
resource_state_event_repository = ResourceStateEventRepository()
rack_placement_repository = RackPlacementRepository()
rack_bin_mount_repository = RackBinMountRepository()
bin_placement_repository = BinPlacementRepository()
bin_material_mount_repository = BinMaterialMountRepository()
bin_cell_occupancy_repository = BinCellOccupancyRepository()
bin_content_snapshot_repository = BinContentSnapshotRepository()
bin_content_snapshot_item_repository = BinContentSnapshotItemRepository()

__all__ = [
    "BinCellOccupancyRepository",
    "BinContentSnapshotItemRepository",
    "BinContentSnapshotRepository",
    "BinMaterialMountRepository",
    "BinPlacementRepository",
    "BinRepository",
    "BinSlotTemplateRepository",
    "BinTypeRepository",
    "RackBinMountRepository",
    "RackPlacementRepository",
    "RackRepository",
    "RackSlotTemplateRepository",
    "RackTypeRepository",
    "ResourceStateEventRepository",
    "bin_cell_occupancy_repository",
    "bin_content_snapshot_item_repository",
    "bin_content_snapshot_repository",
    "bin_material_mount_repository",
    "bin_placement_repository",
    "bin_repository",
    "bin_slot_template_repository",
    "bin_type_repository",
    "rack_bin_mount_repository",
    "rack_placement_repository",
    "rack_repository",
    "rack_slot_template_repository",
    "rack_type_repository",
    "resource_state_event_repository",
]
