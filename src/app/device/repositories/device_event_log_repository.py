"""设备事件日志 Repository"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.event_log import DeviceEventLog
from src.database.base_repository import BaseRepository


class DeviceEventLogRepository(BaseRepository[DeviceEventLog]):
    """设备事件日志 Repository"""

    def __init__(self):
        super().__init__(DeviceEventLog)

    async def update_event_log(
        self,
        db: AsyncSession,
        event_log: DeviceEventLog,
        processed: bool,
        processing_result: dict[str, object] | None = None,
        error_message: str | None = None,
    ) -> DeviceEventLog:
        """更新事件日志处理状态

        Args:
            db: 数据库会话
            event_log: 事件日志实例
            processed: 是否已处理
            processing_result: 处理结果（JSON 格式）
            error_message: 错误消息

        Returns:
            更新后的事件日志实例

        设计原则:
            - SRP: 数据访问逻辑应在 Repository 层，不应在 Service 层
            - 性能: 使用 SQLAlchemy update 直接更新，避免完整的 ORM 开销
            - 一致性: 保持与原有实现相同的更新逻辑
        """
        # 更新实例属性
        event_log.processed = processed
        if processing_result is not None:
            event_log.processing_result = processing_result
        if error_message is not None:
            event_log.error_message = error_message

        # 使用 SQLAlchemy update 直接更新（性能优化）
        stmt = (
            update(DeviceEventLog)
            .where(DeviceEventLog.id == event_log.id)  # type: ignore[arg-type]
            .values(
                processed=processed,
                processing_result=processing_result,
                error_message=error_message,
            )
        )
        await db.execute(stmt)

        # 刷新实例以获取更新后的值
        await db.refresh(event_log)

        return event_log


device_event_log_repository = DeviceEventLogRepository()

__all__ = ["DeviceEventLogRepository", "device_event_log_repository"]
