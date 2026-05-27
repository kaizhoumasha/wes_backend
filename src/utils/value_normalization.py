"""跨域值规范化工具。"""

from __future__ import annotations

from enum import Enum
from typing import Any, cast


def enum_value(value: Any) -> Any:
    """提取 Enum 或类 Enum 对象的原始值。"""
    if isinstance(value, Enum):
        return value.value
    return getattr(value, "value", value)


def enum_str(value: Any) -> str:
    """将 Enum 或类 Enum 对象规范化为字符串。"""
    return str(enum_value(value))


def optional_enum_str(value: Any) -> str | None:
    """将可空 Enum 或类 Enum 对象规范化为字符串。"""
    if value is None:
        return None
    return enum_str(value)


def optional_int(value: Any) -> int | None:
    """仅接受真实 int，bool 不视为 int。"""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def optional_int_attr(value: Any, name: str) -> int | None:
    """从对象属性中提取真实 int，bool 不视为 int。"""
    return optional_int(getattr(value, name, None))


def required_int_attr(value: Any, name: str) -> int:
    """从对象属性中提取必需 int，不存在时抛出 ValueError。"""
    result = optional_int_attr(value, name)
    if result is None:
        raise ValueError(f"{name} is required")
    return result


def coerce_optional_int(value: Any) -> int | None:
    """尽力把外部输入转为 int，失败时返回 None。"""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def optional_str(value: Any) -> str | None:
    """仅接受非空字符串。"""
    return value if isinstance(value, str) and value else None


def optional_str_attr(value: Any, name: str) -> str | None:
    """从对象属性中提取非空字符串。"""
    return optional_str(getattr(value, name, None))


def coerce_optional_str(value: Any) -> str | None:
    """尽力把外部输入转为去空白字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def string_value(value: Any, default: str = "") -> str:
    """仅接受字符串，否则返回默认值。"""
    return value if isinstance(value, str) else default


def coerce_string_value(value: Any, default: str = "") -> str:
    """将外部输入转为字符串，None 使用默认值。"""
    if value is None:
        return default
    return str(value)


def as_dict(value: Any) -> dict[str, Any]:
    """仅接受 dict，并返回浅拷贝。"""
    return dict(cast("dict[str, Any]", value)) if isinstance(value, dict) else {}


def dict_attr(value: Any, name: str) -> dict[str, Any]:
    """从对象属性中提取 dict，并返回浅拷贝。"""
    return as_dict(getattr(value, name, None))


def resolve_entity_id(entity: Any) -> int | None:
    """从实体上提取真实整型主键。"""
    return optional_int(getattr(entity, "id", None))


def resolve_required_pk(entity: Any, entity_name: str, *_field_names: str) -> int:
    """提取必需的整型主键，不存在时抛出 ValueError。"""
    pk = resolve_entity_id(entity)
    if pk is None:
        raise ValueError(f"{entity_name} missing primary key")
    return pk


def canonical_event_type(payload: dict[str, Any]) -> str | None:
    """提取 canonical_event_type，缺失时回退 event_type。"""
    return optional_str(payload.get("canonical_event_type")) or optional_str(payload.get("event_type"))


__all__ = [
    "as_dict",
    "canonical_event_type",
    "coerce_optional_int",
    "coerce_optional_str",
    "coerce_string_value",
    "dict_attr",
    "enum_str",
    "enum_value",
    "optional_enum_str",
    "optional_int",
    "optional_int_attr",
    "optional_str",
    "optional_str_attr",
    "required_int_attr",
    "resolve_entity_id",
    "resolve_required_pk",
    "string_value",
]
