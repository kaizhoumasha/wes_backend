from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from inspect import signature
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

# 与现有 BaseService 保持兼容，避免已写入缓存的空值标记失效
CACHE_NULL_MARKER = "__BASE_SERVICE_NULL__"
LEGACY_CACHE_NULL_MARKERS = {
    CACHE_NULL_MARKER,
    "__NULL_CACHE_MARKER__",
}


def is_null_cache_value(value: Any) -> bool:
    """判断缓存值是否为约定的空值标记。

    仅对字符串值执行空标记判断，避免对列表等结构化缓存值执行集合成员测试。
    """
    return isinstance(value, str) and value in LEGACY_CACHE_NULL_MARKERS


def parse_set_from_cached(value: Any) -> set[Any]:
    """解析缓存中的集合数据（支持 JSON 字符串和可迭代对象）。

    Args:
        value: 缓存值，可以是 JSON 字符串或可迭代对象

    Returns:
        解析后的集合

    Raises:
        TypeError: 不支持的缓存载荷类型
    """
    if isinstance(value, str):
        return set(json.loads(value))
    if isinstance(value, list):
        # 优化：直接转换列表（常见场景）
        return set(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        return {str(item) for item in value}
    raise TypeError("unsupported cached set payload")


def serialize_for_cache(value: Any) -> Any:
    """将运行时对象转换为适合写入缓存的结构化数据。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


async def get_cached_value(
    cache: Any,
    key: str,
    *,
    parser: Callable[[Any], T] | None = None,
    delete_if_invalid: bool = True,
    on_invalid: Callable[[str], None] | None = None,
) -> tuple[bool, T | None]:
    """读取缓存值，统一处理空值标记和坏缓存清理。"""
    cached = await cache.get(key)
    if cached is None:
        return False, None
    if is_null_cache_value(cached):
        return True, None

    if parser is None:
        return True, cached

    try:
        return True, parser(cached)
    except (TypeError, ValueError):
        if delete_if_invalid:
            await cache.delete(key)
        if on_invalid is not None:
            on_invalid(key)
        return False, None


async def set_cached_value(
    cache: Any,
    key: str,
    value: Any,
    *,
    expire: int | None = None,
    null_expire: int | None = None,
    serializer: Callable[[Any], Any] | None = None,
    is_hot: bool = False,
) -> bool:
    """写入缓存值，统一处理空值缓存和结构化序列化。"""
    if value is None:
        if null_expire is None:
            return False
        return await _cache_set(cache, key, CACHE_NULL_MARKER, expire=null_expire, is_hot=is_hot)

    serialized = serializer(value) if serializer is not None else serialize_for_cache(value)
    return await _cache_set(cache, key, serialized, expire=expire, is_hot=is_hot)


async def _cache_set(cache: Any, key: str, value: Any, *, expire: int | None, is_hot: bool) -> bool:
    """兼容不同 cache.set 签名。"""
    if "is_hot" in signature(cache.set).parameters:
        return await cache.set(key, value, expire=expire, is_hot=is_hot)
    return await cache.set(key, value, expire=expire)


__all__ = [
    "CACHE_NULL_MARKER",
    "LEGACY_CACHE_NULL_MARKERS",
    "get_cached_value",
    "is_null_cache_value",
    "parse_set_from_cached",
    "serialize_for_cache",
    "set_cached_value",
]
