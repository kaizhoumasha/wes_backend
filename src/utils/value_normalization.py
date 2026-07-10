"""跨域值规范化工具。"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
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


# ── 跨域值提取与校验（消除 DRY 违规） ──


def require_text(value: Any, field_name: str) -> str:
    """校验 value 是非空字符串，否则抛出 ValueError。

    字段名仅用于错误信息与单值场景的语义标注，不参与取值。
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is required")  # noqa: TRY004
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def require_text_any(payload: Mapping[str, Any], *field_names: str) -> str:
    """从多个候选字段中取第一个非空字符串值，全部缺失抛出 ValueError。"""
    for field_name in field_names:
        value = coerce_string_value(payload.get(field_name))
        if value:
            return value
    raise ValueError(f"{'/'.join(field_names)} is required")


def string_list(payload: Mapping[str, Any], field_name: str) -> list[str]:
    """从 Mapping 中安全取字符串列表。"""
    raw = payload.get(field_name)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def mapping_copy(value: Any) -> dict[str, Any]:
    """安全浅拷贝 Mapping 为 dict，非 Mapping 返回 {}。

    区别于 as_dict()：as_dict 仅接受 dict 类型，本函数接受任何 Mapping。
    """
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def json_safe(value: Any) -> Any:
    """递归转换 Decimal/tuple 为 JSON 可序列化类型。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def positive_quantity(value: Any) -> float:
    """正数校验，非正数抛出 ValueError。"""
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be positive") from exc
    if qty <= 0:
        raise ValueError("quantity must be positive")
    return qty


def positive_timeout_seconds(value: Any) -> int:
    """正整数超时校验，None 默认 300。"""
    if value is None:
        return 300
    try:
        secs = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be positive") from exc
    if secs <= 0:
        raise ValueError("timeout_seconds must be positive")
    return secs


__all__ = [
    "as_dict",
    "canonical_event_type",
    "coerce_optional_int",
    "coerce_optional_str",
    "coerce_string_value",
    "dict_attr",
    "enum_str",
    "enum_value",
    "json_safe",
    "mapping_copy",
    "optional_enum_str",
    "optional_int",
    "optional_int_attr",
    "optional_str",
    "optional_str_attr",
    "positive_quantity",
    "positive_timeout_seconds",
    "require_text",
    "require_text_any",
    "required_int_attr",
    "resolve_entity_id",
    "resolve_required_pk",
    "string_list",
    "string_value",
]
