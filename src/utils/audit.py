"""
审计日志工具模块

提供审计日志记录的辅助函数，用于从请求上下文中提取信息
"""

from typing import Any

from starlette_context import context
from starlette_context.errors import ContextDoesNotExistError

from src.core.logger import logger


def get_request_id() -> str | None:
    """
    从上下文中获取请求 ID

    Returns:
        请求 ID，如果不存在则返回 None
    """
    try:
        return context.get("request_id")
    except (ContextDoesNotExistError, RuntimeError):
        return None


def get_current_user_id() -> int | None:
    """
    从请求上下文中获取当前用户 ID

    优先从 request.state 获取（由认证依赖设置），
    如果不存在则从 starlette_context 获取。

    Returns:
        用户 ID，如果不存在则返回 None
    """
    try:
        # 优先从 starlette_context 中获取 request 对象
        request = context.get("request")
        if request and hasattr(request, "state") and hasattr(request.state, "user_id"):
            return request.state.user_id
    except (ContextDoesNotExistError, RuntimeError, AttributeError):
        pass

    # 备用：从 context 直接获取
    try:
        return context.get("user_id")
    except (ContextDoesNotExistError, RuntimeError):
        return None


def get_current_username() -> str | None:
    """
    从请求上下文中获取当前用户名

    Returns:
        用户名，如果不存在则返回 None
    """
    try:
        # 优先从 starlette_context 中获取 request 对象
        request = context.get("request")
        if request and hasattr(request, "state") and hasattr(request.state, "username"):
            return request.state.username
    except (ContextDoesNotExistError, RuntimeError, AttributeError):
        pass

    # 备用：从 context 直接获取
    try:
        return context.get("username")
    except (ContextDoesNotExistError, RuntimeError):
        return None


def get_request_info() -> dict[str, Any]:
    """
    从上下文中获取请求信息

    Returns:
        包含请求信息的字典，包括：
        - request_id: 请求 ID
        - ip: IP 地址
        - country: 国家
        - region: 地区
        - city: 城市
        - user_agent: 用户代理
        - os: 操作系统
        - browser: 浏览器
        - device: 设备类型
    """
    info = {
        "request_id": get_request_id(),
        "ip": None,
        "country": None,
        "region": None,
        "city": None,
        "user_agent": None,
        "os": None,
        "browser": None,
        "device": None,
    }

    try:
        # 尝试从上下文中获取 request 对象
        request = context.get("request")
        if request and hasattr(request, "state"):
            state = request.state
            info.update(
                {
                    "ip": getattr(state, "ip", None),
                    "country": getattr(state, "country", None),
                    "region": getattr(state, "region", None),
                    "city": getattr(state, "city", None),
                    "user_agent": getattr(state, "user_agent", None),
                    "os": getattr(state, "os", None),
                    "browser": getattr(state, "browser", None),
                    "device": getattr(state, "device", None),
                }
            )
    except (ContextDoesNotExistError, RuntimeError, AttributeError) as e:
        logger.debug(f"无法获取请求信息: {e}")

    return info


__all__ = [
    "get_current_user_id",
    "get_current_username",
    "get_request_id",
    "get_request_info",
]
