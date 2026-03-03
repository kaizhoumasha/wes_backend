"""
SSE 事件发布工具

提供便捷的事件发布函数，用于向 SSE 流推送实时事件
"""

import json
import time
from typing import Any

from src.core.logger import logger
from src.database.redis_client import get_redis


class SSEEventType:
    """SSE 事件类型常量"""

    SYSTEM_NOTIFICATION = "system_notification"  # 系统通知
    BUSINESS_STATUS = "business_status"  # 业务状态更新


async def publish_event(event_type: str, payload: dict[str, Any]) -> bool:
    """发布事件到 SSE 流

    Args:
        event_type: 事件类型（使用 SSEEventType 常量）
        payload: 事件负载（字典）

    Returns:
        bool: 是否发布成功

    Example:
        await publish_event(
            SSEEventType.SYSTEM_NOTIFICATION,
            {"title": "测试", "message": "这是一条通知", "level": "info"}
        )
    """
    redis_client = get_redis()
    if not redis_client:
        logger.warning("Redis 不可用，无法发布 SSE 事件")
        return False

    event_data = {
        "type": event_type,
        "payload": payload,
        "timestamp": int(time.time() * 1000),  # 毫秒时间戳
    }

    try:
        await redis_client.lpush("events:stream", json.dumps(event_data, ensure_ascii=False))
        # 保持队列长度，防止内存溢出（最多保留 1000 条）
        await redis_client.ltrim("events:stream", 0, 1000)
        logger.debug(f"SSE 事件已发布: {event_type}")
        return True
    except Exception as e:
        logger.error(f"发布 SSE 事件失败: {e}")
        return False


async def publish_system_notification(title: str, message: str, level: str = "info") -> bool:
    """发布系统通知

    Args:
        title: 通知标题
        message: 通知内容
        level: 通知级别（info/warning/error/success）

    Returns:
        bool: 是否发布成功

    Example:
        await publish_system_notification(
            title="系统维护",
            message="系统将于今晚 22:00 进行维护",
            level="warning"
        )
    """
    return await publish_event(
        SSEEventType.SYSTEM_NOTIFICATION,
        {
            "title": title,
            "message": message,
            "level": level,
        },
    )


async def publish_business_status(
    business_type: str,
    business_id: int,
    status: str,
    extra: dict[str, Any] | None = None,
) -> bool:
    """发布业务状态更新

    Args:
        business_type: 业务类型（inbound/outbound/inventory 等）
        business_id: 业务 ID
        status: 状态（draft/confirmed/completed 等）
        extra: 额外信息（可选）

    Returns:
        bool: 是否发布成功

    Example:
        await publish_business_status(
            business_type="inbound",
            business_id=123,
            status="confirmed",
            extra={"warehouse_id": 1, "quantity": 100}
        )
    """
    payload = {
        "business_type": business_type,
        "business_id": business_id,
        "status": status,
    }
    if extra:
        payload.update(extra)
    return await publish_event(SSEEventType.BUSINESS_STATUS, payload)


__all__ = [
    "SSEEventType",
    "publish_business_status",
    "publish_event",
    "publish_system_notification",
]
