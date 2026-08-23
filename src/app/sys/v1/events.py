"""
SSE 实时事件推送 API

提供 Server-Sent Events (SSE) 实时事件推送功能：
- GET /api/v1/sys/events/stream - SSE 事件流
"""

import json

from fastapi import APIRouter, Query, Request, status
from starlette.responses import StreamingResponse

from src.app.sys.services.event_stream_service import SSE_EVENT_CHANNEL, event_stream_service
from src.core.exceptions import AuthException
from src.core.security import _verify_token

router = APIRouter(tags=["系统事件"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 25.0


class SSEEventType:
    """SSE 事件类型常量"""

    SYSTEM_NOTIFICATION = "system_notification"  # 系统通知
    BUSINESS_STATUS = "business_status"  # 业务状态更新
    HEARTBEAT = "heartbeat"  # 心跳（保持连接）


@router.get(
    "/events/stream",
    summary="SSE 实时事件流",
    description="订阅 SSE 事件流，接收系统通知和业务状态更新",
    status_code=status.HTTP_200_OK,
)
async def event_stream(
    request: Request,
    token: str | None = Query(  # pyright: ignore[reportCallInDefaultInitializer]
        default=None,
        description="访问令牌（EventSource 无法设置 Authorization 头时使用）",
    ),
):
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

    # SSE 默认要求登录态（前端通过 query token 透传）
    bearer_token = token
    if not bearer_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:]

    if not bearer_token:
        raise AuthException("缺少访问令牌")

    _ = await _verify_token(bearer_token, request)

    async def event_generator():
        """事件生成器"""
        async for event in event_stream_service.subscribe(
            SSE_EVENT_CHANNEL,
            timeout_seconds=SSE_HEARTBEAT_INTERVAL_SECONDS,
        ):
            if event is None:
                yield ": heartbeat\n\n"
                continue
            payload = json.dumps(event["payload"], ensure_ascii=False)
            yield f"event: {event['type']}\n"
            yield f"data: {payload}\n"
            yield f"id: {event['timestamp']}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
