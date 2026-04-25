"""SSE 事件发布服务。"""

import json
from typing import Any, cast

from src.core.logger import logger
from src.database.redis_client import get_redis
from src.utils.timezone import timezone

SSE_EVENT_CHANNEL = "events:stream"
DEFERRED_SSE_EVENTS_KEY = "_deferred_sse_events_after_commit"

DEVICE_STATUS_CHANGED_EVENT = "device.status.changed"


class EventStreamService:
    """向 SSE 事件通道发布轻量通知。"""

    async def publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        """发布一条 SSE 事件；Redis 不可用时静默降级。"""

        redis_client = get_redis()
        if redis_client is None:
            logger.debug(f"SSE 事件跳过发布（Redis 不可用）: {event_type}")
            return False

        event = {
            "type": event_type,
            "payload": payload,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
        }
        try:
            await cast("Any", redis_client).publish(SSE_EVENT_CHANNEL, json.dumps(event, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.warning(f"SSE 事件发布失败: {event_type}, error={exc}")
            return False


event_stream_service = EventStreamService()


def _get_session_info(db: Any) -> dict[str, Any] | None:
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        return info

    try:
        db.info = {}
    except Exception:
        return None

    created_info = getattr(db, "info", None)
    return created_info if isinstance(created_info, dict) else None


def defer_sse_event(db: Any, event_type: str, payload: dict[str, Any]) -> None:
    """登记事务提交后再发布的 SSE 事件。"""

    info = _get_session_info(db)
    if info is None:
        logger.debug(f"SSE 事件无法登记（session 不支持 info）: {event_type}")
        return

    events = info.setdefault(DEFERRED_SSE_EVENTS_KEY, [])
    if isinstance(events, list):
        events.append((event_type, payload))


async def publish_deferred_sse_events(db: Any) -> None:
    """发布并清空当前 session 中已登记的提交后 SSE 事件。"""

    info = _get_session_info(db)
    if info is None:
        return

    raw_events = info.pop(DEFERRED_SSE_EVENTS_KEY, [])
    if not isinstance(raw_events, list):
        return

    for event_type, payload in raw_events:
        if isinstance(event_type, str) and isinstance(payload, dict):
            _ = await event_stream_service.publish(event_type, payload)
