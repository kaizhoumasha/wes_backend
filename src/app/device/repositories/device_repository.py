"""Device Repository 层"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import Device, DeviceStatus
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

    async def get_by_device_code_for_update(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> Device | None:
        """根据设备编码查询并锁定设备行，用于真实设备派发前的串行化治理。"""

        result = await db.execute(
            select(Device)
            .where(
                Device.device_code == device_code,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
            .with_for_update()
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

    async def get_heartbeat_stale_devices(
        self,
        db: AsyncSession,
        *,
        cutoff: datetime,
        limit: int = 100,
    ) -> list[Device]:
        """查询心跳超时且可由 WES 判定为离线的设备。"""

        columns = cast("Any", Device).__table__.c
        result = await db.execute(
            select(Device)
            .where(
                columns.last_heartbeat_at.is_not(None),
                columns.last_heartbeat_at < cutoff,
                columns.device_status.in_([DeviceStatus.IDLE, DeviceStatus.RUNNING]),
                columns.maintenance_mode.is_(False),
                columns.is_deleted.is_(False),
            )
            .order_by(columns.last_heartbeat_at.asc(), columns.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_non_maintenance_by_workline_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> list[Device]:
        """锁定 WorkLine 下可被急停投影接管的设备。"""

        columns = cast("Any", Device).__table__.c
        result = await db.execute(
            select(Device)
            .where(
                columns.work_line_id == workline_id,
                columns.device_status.in_([DeviceStatus.IDLE, DeviceStatus.RUNNING]),
                columns.maintenance_mode.is_(False),
                columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return list(result.scalars().all())

    async def get_safety_error_by_workline_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> list[Device]:
        """锁定 WorkLine 下由急停派生的设备错误投影。"""

        columns = cast("Any", Device).__table__.c
        result = await db.execute(
            select(Device)
            .where(
                columns.work_line_id == workline_id,
                columns.device_status == DeviceStatus.ERROR,
                columns.error_code == "WORKLINE_ESTOPPED",
                columns.is_deleted.is_(False),
            )
            .with_for_update()
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
