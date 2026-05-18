"""Resource Repository 层。"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.resource.models import (
    Bin,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinSlotTemplate,
    BinType,
    ExecutionLocation,
    ExecutionZone,
    FullBoxExchangeTask,
    Rack,
    RackBinMount,
    RackMaterialMount,
    RackPlacement,
    RackRelease,
    RackReleaseBinSnapshot,
    RackReleaseStatus,
    RackSlotTemplate,
    RackType,
    ResourceSourceSystem,
    ResourceStateEvent,
    WmsWritebackEvidence,
)
from src.database.base_repository import BaseRepository


class ExecutionZoneRepository(BaseRepository[ExecutionZone]):
    """执行区域 Repository。"""

    def __init__(self) -> None:
        super().__init__(ExecutionZone)


class ExecutionLocationRepository(BaseRepository[ExecutionLocation]):
    """执行地码 Repository。"""

    def __init__(self) -> None:
        super().__init__(ExecutionLocation)


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


class RackMaterialMountRepository(BaseRepository[RackMaterialMount]):
    """物料卡槽投影 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackMaterialMount)

    async def get_active_by_rack_slot(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        rack_slot_code: str,
    ) -> RackMaterialMount | None:
        """查询货架槽位当前 active material mount。"""

        columns = cast("Any", RackMaterialMount).__table__.c
        result = await db.execute(
            select(RackMaterialMount).where(
                columns.rack_code == rack_code,
                columns.rack_slot_code == rack_slot_code,
                columns.ended_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


class WmsWritebackEvidenceRepository(BaseRepository[WmsWritebackEvidence]):
    """WMS 回写证据 Repository。"""

    def __init__(self) -> None:
        super().__init__(WmsWritebackEvidence)

    async def get_by_idempotency_key(self, db: AsyncSession, idempotency_key: str) -> WmsWritebackEvidence | None:
        """按 WMS 回写幂等键查询证据。"""

        columns = cast("Any", WmsWritebackEvidence).__table__.c
        result = await db.execute(
            select(WmsWritebackEvidence).where(
                columns.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()


class RackReleaseRepository(BaseRepository[RackRelease]):
    """释放周期 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackRelease)

    async def get_by_release_id(self, db: AsyncSession, rack_release_id: str) -> RackRelease | None:
        """按释放周期业务 ID 查询释放记录。"""

        columns = cast("Any", RackRelease).__table__.c
        result = await db.execute(
            select(RackRelease).where(
                columns.rack_release_id == rack_release_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_full_box_exchange_candidates(
        self,
        db: AsyncSession,
        *,
        limit: int = 50,
    ) -> list[RackRelease]:
        """查询可派生满箱交换入口 Inbox 的释放周期。"""

        columns = cast("Any", RackRelease).__table__.c
        result = await db.execute(
            select(RackRelease)
            .where(
                columns.release_status == RackReleaseStatus.CANDIDATE.value,
                columns.moved_out_at.is_not(None),
                columns.inbox_id.is_(None),
            )
            .order_by(columns.released_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


class RackReleaseBinSnapshotRepository(BaseRepository[RackReleaseBinSnapshot]):
    """释放槽位快照 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackReleaseBinSnapshot)

    async def list_by_release_id(
        self,
        db: AsyncSession,
        rack_release_id: str,
    ) -> list[RackReleaseBinSnapshot]:
        """按释放周期业务 ID 查询槽位快照。"""

        columns = cast("Any", RackReleaseBinSnapshot).__table__.c
        result = await db.execute(
            select(RackReleaseBinSnapshot)
            .where(
                columns.rack_release_id == rack_release_id,
            )
            .order_by(columns.slot_code.asc())
        )
        return list(result.scalars().all())


class BinContentSnapshotRepository(BaseRepository[BinContentSnapshot]):
    """料箱内容快照头 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinContentSnapshot)


class BinContentSnapshotItemRepository(BaseRepository[BinContentSnapshotItem]):
    """料箱内容快照明细 Repository。"""

    def __init__(self) -> None:
        super().__init__(BinContentSnapshotItem)


class FullBoxExchangeTaskRepository(BaseRepository[FullBoxExchangeTask]):
    """满箱交换任务 Repository。"""

    def __init__(self) -> None:
        super().__init__(FullBoxExchangeTask)

    async def get_by_exchange_request_code(
        self,
        db: AsyncSession,
        exchange_request_code: str,
    ) -> FullBoxExchangeTask | None:
        """按满箱交换请求编码查询任务镜像。"""

        columns = cast("Any", FullBoxExchangeTask).__table__.c
        result = await db.execute(
            select(FullBoxExchangeTask).where(
                columns.exchange_request_code == exchange_request_code,
            )
        )
        return result.scalar_one_or_none()


execution_zone_repository = ExecutionZoneRepository()
execution_location_repository = ExecutionLocationRepository()
rack_type_repository = RackTypeRepository()
rack_slot_template_repository = RackSlotTemplateRepository()
rack_repository = RackRepository()
bin_type_repository = BinTypeRepository()
bin_slot_template_repository = BinSlotTemplateRepository()
bin_repository = BinRepository()
resource_state_event_repository = ResourceStateEventRepository()
rack_placement_repository = RackPlacementRepository()
rack_bin_mount_repository = RackBinMountRepository()
rack_material_mount_repository = RackMaterialMountRepository()
wms_writeback_evidence_repository = WmsWritebackEvidenceRepository()
rack_release_repository = RackReleaseRepository()
rack_release_bin_snapshot_repository = RackReleaseBinSnapshotRepository()
bin_content_snapshot_repository = BinContentSnapshotRepository()
bin_content_snapshot_item_repository = BinContentSnapshotItemRepository()
full_box_exchange_task_repository = FullBoxExchangeTaskRepository()

__all__ = [
    "BinContentSnapshotItemRepository",
    "BinContentSnapshotRepository",
    "BinRepository",
    "BinSlotTemplateRepository",
    "BinTypeRepository",
    "ExecutionLocationRepository",
    "ExecutionZoneRepository",
    "FullBoxExchangeTaskRepository",
    "RackBinMountRepository",
    "RackMaterialMountRepository",
    "RackPlacementRepository",
    "RackReleaseBinSnapshotRepository",
    "RackReleaseRepository",
    "RackRepository",
    "RackSlotTemplateRepository",
    "RackTypeRepository",
    "ResourceStateEventRepository",
    "WmsWritebackEvidenceRepository",
    "bin_content_snapshot_item_repository",
    "bin_content_snapshot_repository",
    "bin_repository",
    "bin_slot_template_repository",
    "bin_type_repository",
    "execution_location_repository",
    "execution_zone_repository",
    "full_box_exchange_task_repository",
    "rack_bin_mount_repository",
    "rack_material_mount_repository",
    "rack_placement_repository",
    "rack_release_bin_snapshot_repository",
    "rack_release_repository",
    "rack_repository",
    "rack_slot_template_repository",
    "rack_type_repository",
    "resource_state_event_repository",
    "wms_writeback_evidence_repository",
]
