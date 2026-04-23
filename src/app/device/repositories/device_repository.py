"""Device Repository 层"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import Device
from src.database.base_repository import BaseRepository
from src.utils.device_cache import workline_device_cache


class DeviceRepository(BaseRepository[Device]):
    """设备数据访问层"""

    def __init__(self) -> None:
        """初始化设备仓库"""
        super().__init__(Device)

    async def get_by_device_code(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> Device | None:
        """根据设备编码查询"""
        result = await db.execute(
            select(Device).where(
                Device.device_code == device_code,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def get_by_work_line_id(
        self,
        db: AsyncSession,
        work_line_id: int,
    ) -> list[Device]:
        """根据作业线 ID 查询所有设备。"""
        result = await db.execute(
            select(Device)
            .where(
                Device.work_line_id == work_line_id,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
            .order_by(
                Device.sort_order.asc(),  # type: ignore[arg-type]
                Device.role_index.asc(),  # type: ignore[arg-type]
                Device.id.asc(),  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())

    async def after_device_change(
        self,
        _db: AsyncSession,
        old_work_line_id: int | None,
        new_work_line_id: int | None,
    ) -> None:
        """设备变更后失效缓存

        当设备的 work_line_id 变化（绑定、解绑、更新）时调用，
        失效相关工作线的设备缓存。

        Args:
            _db: 数据库会话（为保持仓储 Hook 签名一致而保留）
            old_work_line_id: 变更前的工作线 ID（可 None）
            new_work_line_id: 变更后的工作线 ID（可 None）
        """
        # 失效旧工作线缓存
        if old_work_line_id is not None:
            workline_device_cache.invalidate(old_work_line_id)

        # 失效新工作线缓存
        if new_work_line_id is not None and new_work_line_id != old_work_line_id:
            workline_device_cache.invalidate(new_work_line_id)


# 创建单例
device_repository = DeviceRepository()
