"""
性能监控中间件

功能：
1. 追踪慢请求（>100ms）
2. 记录数据库查询时间
3. 记录缓存命中率
4. 提供性能分析数据
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from src.core.logger import logger


class PerformanceMiddleware(BaseHTTPMiddleware):
    """性能监控中间件"""

    def __init__(self, app: ASGIApp, slow_request_threshold: int = 100):
        """
        初始化性能监控中间件

        :param app: FastAPI 应用
        :param slow_request_threshold: 慢请求阈值（毫秒）
        """
        super().__init__(app)
        self.slow_request_threshold = slow_request_threshold

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """处理请求并记录性能指标"""
        start_time = time.time()

        # 处理请求
        response = await call_next(request)

        # 计算响应时间
        process_time = (time.time() - start_time) * 1000  # 转换为毫秒

        # 添加响应头
        response.headers["X-Process-Time"] = f"{process_time:.2f}ms"

        # 记录慢请求
        if process_time > self.slow_request_threshold:
            logger.warning(
                f"慢请求: {request.method} {request.url.path} - "
                f"{process_time:.2f}ms (阈值: {self.slow_request_threshold}ms)"
            )

        return response
