"""
SSE 实时事件推送 API

提供 Server-Sent Events (SSE) 实时事件推送功能：
- GET /api/v1/sys/events/stream - SSE 事件流
"""

import asyncio
import json

from fastapi import APIRouter, status
from starlette.responses import StreamingResponse

from src.core.logger import logger
from src.database.redis_client import get_redis

router = APIRouter(prefix="/events", tags=["系统事件"])


class SSEEventType:
    """SSE 事件类型常量"""

    SYSTEM_NOTIFICATION = "system_notification"  # 系统通知
    BUSINESS_STATUS = "business_status"  # 业务状态更新
    HEARTBEAT = "heartbeat"  # 心跳（保持连接）


@router.get(
    "/stream",
    summary="SSE 实时事件流",
    description="订阅 SSE 事件流，接收系统通知和业务状态更新",
    status_code=status.HTTP_200_OK,
)
async def event_stream():
    """
    SSE 实时事件推送端点

    返回 Server-Sent Events 流，持续推送：
    - 系统通知（SYSTEM_NOTIFICATION）：系统公告、警报等
    - 业务状态更新（BUSINESS_STATUS）：入库单状态、库存变化等
    - 心跳（HEARTBEAT）：保持连接活跃

    **事件格式**：
    ```
    event: system_notification
    data: {"title": "测试", "message": "这是一条通知", "level": "info"}

    event: business_status
    data: {"business_type": "inbound", "business_id": 123, "status": "confirmed"}
    ```

    **使用场景**：
    - 前端实时接收系统通知
    - 前端实时监听业务状态变化
    - 多客户端同步状态

    **特性**：
    - Redis 不可用时自动降级（返回空流）
    - 异常时自动发送心跳保持连接
    - 支持多客户端并发连接
    """

    redis_client = get_redis()
    if not redis_client:
        # Redis 不可用时返回空事件流（降级模式）
        async def empty_generator():
            while True:
                await asyncio.sleep(1)
                yield ": keep-alive\n\n"

        return StreamingResponse(empty_generator(), media_type="text/event-stream")

    async def event_generator():
        """事件生成器"""
        while True:
            try:
                # 从 Redis 队列获取事件（阻塞 1 秒）
                event = await redis_client.brpop("events:stream", timeout=1)
                if event:
                    _, data = event
                    event_dict = json.loads(data)
                    event_type = event_dict.get("type", "message")
                    payload = json.dumps(event_dict.get("payload", {}), ensure_ascii=False)
                    timestamp = str(event_dict.get("timestamp", 0))
                    # SSE 格式: event: xxx\ndata: xxx\nid: xxx\n\n
                    yield f"event: {event_type}\n"
                    yield f"data: {payload}\n"
                    yield f"id: {timestamp}\n\n"
                else:
                    # 没有事件时发送心跳
                    yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                # 客户端断开连接
                logger.debug("SSE 客户端断开连接")
                break
            except Exception as e:
                # 出错时发送心跳
                logger.warning(f"SSE 事件流异常: {e}，发送心跳保持连接")
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
