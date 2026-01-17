"""
请求限流中间件

保护系统不被过高并发压垮，当并发请求数超过阈值时返回 503 错误
"""

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.exceptions import ServiceUnavailableException
from src.core.logger import logger


class RateLimitMiddleware(BaseHTTPMiddleware):
    """并发请求限流中间件"""

    def __init__(self, app, max_concurrent: int = 200):
        """
        初始化限流中间件

        :param app: FastAPI 应用实例
        :param max_concurrent: 最大并发请求数，默认 200
        """
        super().__init__(app)
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._current_requests = 0
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        处理请求，限制并发数

        :param request: 请求对象
        :param call_next: 下一个中间件或路由处理器
        :return: 响应对象
        """
        # 尝试获取信号量
        if not self._semaphore.locked():
            async with self._semaphore:
                async with self._lock:
                    self._current_requests += 1
                    current = self._current_requests

                try:
                    # 记录高并发情况
                    if current > self.max_concurrent * 0.8:
                        logger.warning(f"高并发警告: 当前并发 {current}/{self.max_concurrent}")

                    return await call_next(request)
                finally:
                    async with self._lock:
                        self._current_requests -= 1
        else:
            # 超过并发限制，返回 503
            logger.error(
                f"并发限制: 拒绝请求 {request.method} {request.url.path} "
                f"(当前并发: {self._current_requests}/{self.max_concurrent})"
            )
            raise ServiceUnavailableException("服务器繁忙，请稍后重试")
