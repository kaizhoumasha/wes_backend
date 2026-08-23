"""SSE 事件发布服务。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

from src.core.logger import logger
from src.database.redis_client import get_redis
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SSE_EVENT_CHANNEL = "events:stream"
DEVICE_EVIDENCE_STREAM_CHANNEL = "device:evidence:stream"
DEFERRED_SSE_EVENTS_KEY = "_deferred_sse_events_after_commit"
SSE_PUBLISH_TIMEOUT_SECONDS = 1.0
SSE_SUBSCRIBE_TIMEOUT_SECONDS = 5.0
SSE_CLEANUP_TIMEOUT_SECONDS = 1.0

DEVICE_STATUS_CHANGED_EVENT = "device.status.changed"
WORKLINE_RUNTIME_CHANGED_EVENT = "workline.runtime.changed"
COMMAND_STATUS_CHANGED_EVENT = "command.status.changed"


class EventStreamService:
    """向 SSE 事件通道发布轻量通知。"""

    async def publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        """发布一条 SSE 事件；Redis 不可用时静默降级。"""

        return await self.publish_to(SSE_EVENT_CHANNEL, event_type, payload)

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, Any]) -> bool:
        """向指定频道发布事件；用于复用同一套基础能力。"""

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
            async with asyncio.timeout(SSE_PUBLISH_TIMEOUT_SECONDS):
                await cast("Any", redis_client).publish(channel, json.dumps(event, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.warning(f"SSE 事件发布失败: {event_type}, error={exc}")
            return False

    async def subscribe(
        self,
        channel: str,
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any] | None]:
        """订阅 live-only 频道；超时以 ``None`` 通知 route 发送 heartbeat。"""

        redis_client = get_redis()
        if redis_client is None:
            return

        pubsub = cast("Any", redis_client).pubsub()
        try:
            pending_message = None
            async with asyncio.timeout(SSE_SUBSCRIBE_TIMEOUT_SECONDS):
                await pubsub.subscribe(channel)
                while True:
                    startup_message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)
                    if startup_message is None:
                        continue
                    if startup_message.get("type") == "subscribe":
                        break
                    if startup_message.get("type") == "message" and pending_message is None:
                        pending_message = startup_message
            # readiness：只有 Redis 已完成订阅后，路由才能向客户端声明 SSE 已连接。
            yield None
            while True:
                try:
                    message = pending_message
                    pending_message = None
                    if message is None or message.get("type") != "message":
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=timeout_seconds,
                        )
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.warning(f"SSE 订阅异常: channel={channel}, error={exc}")
                    yield None
                    continue
                if not message or message.get("type") != "message":
                    yield None
                    continue
                try:
                    yield _decode_stream_event(message.get("data"))
                except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    logger.warning(f"SSE 跳过非法消息: channel={channel}, error={exc}")
        finally:
            try:
                async with asyncio.timeout(SSE_CLEANUP_TIMEOUT_SECONDS):
                    await pubsub.unsubscribe(channel)
            except Exception as exc:
                logger.debug(f"SSE Pub/Sub unsubscribe 失败（可忽略）: {exc}")
            try:
                async with asyncio.timeout(SSE_CLEANUP_TIMEOUT_SECONDS):
                    close = getattr(pubsub, "aclose", None) or pubsub.close
                    close_result = close()
                    if asyncio.iscoroutine(close_result):
                        await close_result
            except Exception as exc:
                logger.debug(f"SSE Pub/Sub close 失败（可忽略）: {exc}")


event_stream_service = EventStreamService()


def _decode_stream_event(raw: object) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise TypeError("SSE 消息必须是文本")
    event = json.loads(raw)
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("type"), str)
        or not isinstance(event.get("payload"), dict)
        or not isinstance(event.get("timestamp"), int)
    ):
        raise TypeError("SSE event envelope 无效")
    return event


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


def discard_deferred_sse_events(db: Any) -> None:
    """丢弃当前事务暂存的 SSE；数据库回滚不会自动清理 ``session.info``。"""

    info = _get_session_info(db)
    if info is not None:
        info.pop(DEFERRED_SSE_EVENTS_KEY, None)


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


def defer_command_status_changed_event(
    db: Any,
    *,
    command: Any,
    action: str,
    workline_id: int | None,
    device_id: int | None,
    session_id: int | None = None,
) -> None:
    """登记 command 状态变更 SSE 事件，作为唯一的 command SSE 入口。"""

    command_id = getattr(command, "id", None)
    command_code = getattr(command, "command_code", None)
    keys: dict[str, Any] = {
        "workline_id": workline_id,
        "device_id": device_id,
        "command_id": command_id,
        "command_code": command_code,
    }
    if session_id is not None:
        keys["session_id"] = session_id

    payload: dict[str, Any] = {
        "domain": "workline_runtime",
        "entity": "command",
        "action": action,
        "keys": keys,
        "command_id": command_id,
        "command_code": command_code,
        "workline_id": workline_id,
        "device_id": device_id,
        "session_id": session_id,
        "status": getattr(getattr(command, "status", None), "value", getattr(command, "status", None)),
        "timestamp": timezone.now_utc().isoformat(),
    }
    defer_sse_event(db, COMMAND_STATUS_CHANGED_EVENT, payload)
