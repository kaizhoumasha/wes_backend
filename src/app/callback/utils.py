"""Callback 入站边界使用的本地轻量工具。

本模块只承载 ``JsonDict`` 与 payload 字段规范化函数，不依赖 runtime
orchestration，也不承担业务执行语义。
"""

from __future__ import annotations

from typing import Any, cast

JsonDict = dict[str, Any]


def non_empty_str(value: Any) -> str | None:
    """返回非空字符串,否则 None。"""
    return value if isinstance(value, str) and value else None


def payload_dict(value: Any) -> JsonDict:
    """安全转换为 dict,无效值返回空 dict。"""
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def ensure_dict(value: Any) -> JsonDict:
    """将值转换为 JsonDict,非字典返回空字典。"""
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str:
    """从 payload 中按别名优先级提取第一个有效字符串。"""
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["JsonDict", "ensure_dict", "non_empty_str", "payload_dict", "resolve_first_str"]
