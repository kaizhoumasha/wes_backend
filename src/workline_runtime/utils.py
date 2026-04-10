"""工作线运行时通用工具函数。"""

from typing import Any, cast

# 类型别名：JSON 字典
JsonDict = dict[str, Any]


def ensure_dict(value: Any) -> JsonDict:
    """
    将值转换为 JsonDict，非字典返回空字典。

    Args:
        value: 任意值

    Returns:
        JsonDict：如果输入是字典则返回，否则返回空字典
    """
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str:
    """
    从 payload 中按别名优先级提取第一个有效字符串。

    Args:
        payload: JSON payload 字典
        aliases: 字段别名元组，按优先级排序

    Returns:
        str: 第一个非空字符串值，如果都为空则返回空字符串
    """
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = [
    "JsonDict",
    "ensure_dict",
    "resolve_first_str",
]
