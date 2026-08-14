"""WMS 线协议共享的严格 JSON 解码。"""

from __future__ import annotations

import json
import math
from typing import Any


class StrictJsonError(ValueError):
    """JSON 语法或对象成员不满足严格合同。"""

    def __init__(self, message: str, *, operation_id: object = None) -> None:
        super().__init__(message)
        self.operation_id = operation_id


class _ObjectPairs(list[tuple[str, Any]]):
    pass


def loads_strict_json(text: str) -> object:
    """拒绝重复 key、非标准常量和溢出浮点数，并保留可唯一提取的消息身份。"""

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_ObjectPairs,
            parse_float=_parse_finite_float,
            parse_constant=_reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise StrictJsonError("invalid JSON") from error

    operation_id = _unique_top_level_value(parsed, "operation_id")
    try:
        return _materialize(parsed)
    except StrictJsonError as error:
        raise StrictJsonError(str(error), operation_id=operation_id) from error
    except RecursionError as error:
        raise StrictJsonError("invalid JSON", operation_id=operation_id) from error


def is_json_utf8_media_type(value: str) -> bool:
    """只接受 application/json，以及可选的完整 UTF-8 charset 参数。"""

    parts = [part.strip() for part in value.split(";")]
    if not parts or parts[0].casefold() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    name, separator, charset = parts[1].partition("=")
    return separator == "=" and name.casefold() == "charset" and charset.casefold() in {"utf-8", '"utf-8"'}


def _unique_top_level_value(value: object, key: str) -> object:
    if not isinstance(value, _ObjectPairs):
        return None
    values = [item for name, item in value if name == key]
    return values[0] if len(values) == 1 else None


def _materialize(value: object) -> object:
    if isinstance(value, _ObjectPairs):
        result: dict[str, object] = {}
        for key, item in value:
            if key in result:
                raise StrictJsonError(f"duplicate JSON key: {key}")
            result[key] = _materialize(item)
        return result
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number must be finite")
    return parsed


__all__ = ["StrictJsonError", "is_json_utf8_media_type", "loads_strict_json"]
