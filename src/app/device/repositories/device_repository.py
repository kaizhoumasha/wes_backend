"""Device Repository 层"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import Device
from src.database.base_repository import BaseRepository


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
        """根据作业线 ID 查询所有设备"""
        result = await db.execute(
            select(Device).where(
                Device.work_line_id == work_line_id,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())


# 创建单例
device_repository = DeviceRepository()
