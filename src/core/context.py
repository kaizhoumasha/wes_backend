"""
请求上下文管理模块

基于 starlette-context 实现请求级别的上下文存储
"""

from typing import Any, cast

from starlette_context import context
from starlette_context.middleware import RawContextMiddleware


class RequestContext:
    """请求上下文管理器"""

    @staticmethod
    def set_request_id(request_id: str) -> None:
        """设置当前请求的 ID"""
        context["request_id"] = request_id

    @staticmethod
    def get_request_id() -> str:
        """获取当前请求的 ID"""
        if context.exists():
            request_id = cast("str | None", cast("Any", context).get("request_id"))
            return request_id or "SYSTEM"
        return "SYSTEM"


__all__ = ["RawContextMiddleware", "RequestContext"]
