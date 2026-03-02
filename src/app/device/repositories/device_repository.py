"""
设备 Repository 层
"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.device import Device, DeviceCommand, DeviceEvent
from src.database.base_repository import BaseRepository


class DeviceRepository(BaseRepository[Device]):
    """设备 Repository"""

    async def get_by_device_id(self, db: AsyncSession, device_id: str) -> Device | None:
        """根据设备 ID 获取设备"""
        result = await db.execute(
            select(self.model).where(self.model.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        db: AsyncSession,
        device_id: str,
        status: str,
        current_command_id: str | None = None,
    ) -> None:
        """更新设备状态"""
        values: dict[str, Any] = {"status": status}
        if current_command_id is not None:
            values["current_command_id"] = current_command_id

        await db.execute(
            update(self.model)
            .where(self.model.device_id == device_id)
            .values(**values)
        )
        await db.commit()

    async def set_online(self, db: AsyncSession, device_id: str, is_online: bool = True) -> None:
        """设置设备在线状态"""
        from src.utils.timezone import timezone

        await db.execute(
            update(self.model)
            .where(self.model.device_id == device_id)
            .values(
                is_online=is_online,
                last_heartbeat=timezone.now_for_db() if is_online else None,
                status="IDLE" if is_online else "OFFLINE",
            )
        )
        await db.commit()

    async def get_online_devices(self, db: AsyncSession) -> list[Device]:
        """获取所有在线设备"""
        result = await db.execute(
            select(self.model).where(self.model.is_online == True)
        )
        return list(result.scalars().all())


class DeviceCommandRepository(BaseRepository[DeviceCommand]):
    """设备指令 Repository"""

    async def get_by_command_id(self, db: AsyncSession, command_id: str) -> DeviceCommand | None:
        """根据指令 ID 获取指令"""
        result = await db.execute(
            select(self.model).where(self.model.command_id == command_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        db: AsyncSession,
        command_id: str,
        status: str,
        **kwargs,
    ) -> None:
        """更新指令状态"""
        from src.utils.timezone import timezone

        values: dict[str, Any] = {"status": status, **kwargs}

        # 根据状态自动设置时间戳
        if status == "SENT":
            values["sent_at"] = timezone.now_for_db()
        elif status == "ACKED":
            values["acked_at"] = timezone.now_for_db()
        elif status in ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"):
            values["completed_at"] = timezone.now_for_db()

        await db.execute(
            update(self.model)
            .where(self.model.command_id == command_id)
            .values(**values)
        )
        await db.commit()

    async def get_pending_commands(
        self,
        db: AsyncSession,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[DeviceCommand]:
        """获取待处理指令"""
        stmt = select(self.model).where(self.model.status == "PENDING")

        if device_id:
            stmt = stmt.where(self.model.device_id == device_id)

        stmt = stmt.order_by(self.model.priority.desc(), self.model.created_at.asc()).limit(limit)

        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def increment_retry(self, db: AsyncSession, command_id: str) -> int:
        """增加重试次数并返回新值"""
        command = await self.get_by_command_id(db, command_id)
        if command:
            command.retry_count += 1
            await db.commit()
            await db.refresh(command)
            return command.retry_count
        return 0


class DeviceEventRepository(BaseRepository[DeviceEvent]):
    """设备事件 Repository"""

    async def get_unprocessed_events(
        self,
        db: AsyncSession,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[DeviceEvent]:
        """获取未处理事件"""
        stmt = select(self.model).where(self.model.is_processed == False)

        if device_id:
            stmt = stmt.where(self.model.device_id == device_id)

        stmt = stmt.order_by(self.model.created_at.asc()).limit(limit)

        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def mark_as_processed(self, db: AsyncSession, event_id: int) -> None:
        """标记事件为已处理"""
        from src.utils.timezone import timezone

        await db.execute(
            update(self.model)
            .where(self.model.id == event_id)
            .values(
                is_processed=True,
                processed_at=timezone.now_for_db(),
            )
        )
        await db.commit()


# 创建单例
device_repository = DeviceRepository(Device)
device_command_repository = DeviceCommandRepository(DeviceCommand)
device_event_repository = DeviceEventRepository(DeviceEvent)
