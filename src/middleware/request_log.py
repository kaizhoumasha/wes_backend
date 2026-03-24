"""
FastAPI 中间件模块
"""

import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

import starlette_context
from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from src.core.conf import settings
from src.core.logger import logger
from src.utils.request_parse import parse_ip_info, parse_user_agent_info

# 需要跳过日志记录的路径前缀
SKIP_LOG_PATHS: list[str] = [
    "/static",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/health",
    "/metrics",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
]


def should_skip_log(path: str) -> bool:
    """判断是否应该跳过日志记录"""
    return any(path.startswith(prefix) for prefix in SKIP_LOG_PATHS)


async def attend_state_info(request: StarletteRequest) -> None:
    """附加请求信息"""
    ip_info = await parse_ip_info(request)
    ua_info = parse_user_agent_info(request)

    # 设置附加请求信息
    request.state.ip = ip_info.ip
    request.state.country = ip_info.country
    request.state.region = ip_info.region
    request.state.city = ip_info.city
    request.state.user_agent = ua_info.user_agent
    request.state.os = ua_info.os
    request.state.browser = ua_info.browser
    request.state.device = ua_info.device


@contextmanager
def request_cycle_context(initial_data: dict[str, Any]) -> Iterator[None]:
    with cast("Any", starlette_context.request_cycle_context)(initial_data):
        yield


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    请求日志中间件

    记录每个HTTP请求的详细信息，并将 request_id 存入上下文
    实现自动链路追踪
    """

    async def dispatch(
        self,
        request: StarletteRequest,
        call_next: Callable[[StarletteRequest], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        method = request.method
        path = request.url.path

        # 跳过静态资源日志
        if should_skip_log(path):
            return await call_next(request)

        # 生成请求ID
        request_id = str(uuid.uuid4())[:8]

        # 使用 request_cycle_context 管理整个请求周期的上下文
        # 这会创建一个新上下文，确保后续所有代码都能访问到 request_id 和 request 对象
        with request_cycle_context({"request_id": request_id, "request": request}):
            start_time = time.time()

            try:
                await attend_state_info(request)
                response = cast("StarletteResponse", await call_next(request))

                # 计算处理时间
                process_time = (time.time() - start_time) * 1000
                time_str = f"{process_time:.0f}ms" if process_time < 1000 else f"{process_time / 1000:.2f}s"

                # 根据状态码决定日志级别
                status = response.status_code
                if status >= 500:
                    logger.error(f"{method} {path} - {status} ({time_str})")
                elif status >= 400:
                    logger.warning(f"{method} {path} - {status} ({time_str})")
                else:
                    logger.info(f"{method} {path} - {status} ({time_str})")

                # 添加请求ID到响应头
                response.headers["X-Request-ID"] = request_id

                return response

            except Exception as e:
                process_time = (time.time() - start_time) * 1000
                time_str = f"{process_time:.0f}ms"

                # 使用 logger.exception() 自动记录异常堆栈
                if settings.APP_DEBUG:
                    logger.exception(f"{method} {path} - Exception occurred ({time_str})")
                else:
                    logger.error(f"{method} {path} - {type(e).__name__}: {e!s} ({time_str})")

                # 返回符合统一错误格式的响应
                from src.utils.timezone import timezone

                return ORJSONResponse(
                    status_code=500,
                    content={
                        "code": "INTERNAL_ERROR",
                        "message": "服务器内部错误",
                        "detail": {"request_id": request_id},
                        "timestamp": timezone.now_utc().isoformat().replace("+00:00", "Z"),
                    },
                )


__all__ = ["RequestLogMiddleware"]
