"""
FastAPI 中间件模块
"""
import time
import uuid
from typing import Callable, List
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette_context import request_cycle_context

from .logger import logger
from .conf import settings


# 需要跳过日志记录的路径前缀
SKIP_LOG_PATHS: List[str] = [
    "/static",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/health",
    "/metrics",
]


def should_skip_log(path: str) -> bool:
    """判断是否应该跳过日志记录"""
    return any(path.startswith(prefix) for prefix in SKIP_LOG_PATHS)


async def request_middleware(request: Request, call_next: Callable) -> Response:
    """
    请求日志中间件

    记录每个HTTP请求的详细信息，并将 request_id 存入上下文
    实现自动链路追踪
    """
    method = request.method
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    # 跳过静态资源日志
    if should_skip_log(path):
        return await call_next(request)

    # 生成请求ID
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    # 使用 request_cycle_context 手动管理上下文
    with request_cycle_context({'request_id': request_id}):
        try:
            response = await call_next(request)

            # 计算处理时间
            process_time = (time.time() - start_time) * 1000
            time_str = f"{process_time:.0f}ms" if process_time < 1000 else f"{process_time/1000:.2f}s"

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
            logger.error(
                f"{method} {path} - {str(e)} ({time_str})",
                exc_info=settings.APP_DEBUG,
            )

            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error", "request_id": request_id},
            )


__all__ = ["request_middleware"]
