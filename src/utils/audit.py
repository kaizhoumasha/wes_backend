"""
审计日志工具模块

提供审计日志记录的辅助函数，用于从请求上下文中提取信息
"""

from typing import Any, TypedDict, cast

from fastapi import Request
from starlette_context import context
from starlette_context.errors import ContextDoesNotExistError

from src.core.logger import logger


class RequestInfo(TypedDict):
    request_id: str | None
    ip: str | None
    country: str | None
    region: str | None
    city: str | None
    user_agent: str | None
    os: str | None
    browser: str | None
    device: str | None


def _get_context_value(key: str) -> Any | None:
    try:
        context_store = cast("Any", context)
        return cast("Any | None", context_store.get(key))
    except (ContextDoesNotExistError, RuntimeError):
        return None


def _get_request() -> Request | None:
    request = _get_context_value("request")
    return request if isinstance(request, Request) else None


def get_request_id() -> str | None:
    """
    从上下文中获取请求 ID

    Returns:
        请求 ID，如果不存在则返回 None
    """
    return cast("str | None", _get_context_value("request_id"))


def get_current_user_id() -> int | None:
    """
    从请求上下文中获取当前用户 ID

    优先从 request.state 获取（由认证依赖设置），
    如果不存在则从 starlette_context 获取。

    Returns:
        用户 ID，如果不存在则返回 None
    """
    request = _get_request()
    if request is not None:
        return cast("int | None", getattr(request.state, "user_id", None))

    # 备用：从 context 直接获取
    return cast("int | None", _get_context_value("user_id"))


def get_current_username() -> str | None:
    """
    从请求上下文中获取当前用户名

    Returns:
        用户名，如果不存在则返回 None
    """
    request = _get_request()
    if request is not None:
        return cast("str | None", getattr(request.state, "username", None))

    # 备用：从 context 直接获取
    return cast("str | None", _get_context_value("username"))


def get_request_method() -> str | None:
    """
    从请求上下文中获取 HTTP 方法

    Returns:
        HTTP 方法（GET/POST/PUT/DELETE等），如果不存在则返回 None
    """
    request = _get_request()
    return request.method if request is not None else None


def get_request_info() -> RequestInfo:
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
    info: RequestInfo = {
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
        request = _get_request()
        if request is not None:
            state = request.state
            info.update(
                {
                    "ip": cast("str | None", getattr(state, "ip", None)),
                    "country": cast("str | None", getattr(state, "country", None)),
                    "region": cast("str | None", getattr(state, "region", None)),
                    "city": cast("str | None", getattr(state, "city", None)),
                    "user_agent": cast("str | None", getattr(state, "user_agent", None)),
                    "os": cast("str | None", getattr(state, "os", None)),
                    "browser": cast("str | None", getattr(state, "browser", None)),
                    "device": cast("str | None", getattr(state, "device", None)),
                }
            )
    except AttributeError as e:
        logger.debug(f"无法获取请求信息: {e}")

    return info


__all__ = [
    "get_current_user_id",
    "get_current_username",
    "get_request_id",
    "get_request_info",
    "get_request_method",
]
