"""WMS 线协议共享的严格 JSON 解码。"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class StrictJsonError(ValueError):
    """JSON 语法或对象成员不满足严格合同。"""

    def __init__(
        self,
        message: str,
        *,
        operation_id: object = None,
        operation: object = None,
        duplicate_key: bool = False,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.operation = operation
        self.duplicate_key = duplicate_key


class _ObjectPairs(list[tuple[str, Any]]):
    pass


class _PreservedJsonFloat(float):
    def __new__(cls, lexeme: str) -> _PreservedJsonFloat:
        value = super().__new__(cls, lexeme)
        value.lexeme = lexeme
        return value

    lexeme: str


def loads_strict_json(text: str) -> object:
    """拒绝重复 key、非标准常量和非有限浮点数，并保留可唯一提取的消息身份。"""

    return _loads_strict_json(text, parse_float=_parse_finite_float, materialize=_materialize_recursive)


def loads_transport_json(text: str) -> object:
    """按 Transport 合同保留 Decimal 可表示 Float 的原始 lexeme。"""

    return _loads_strict_json(text, parse_float=_parse_transport_float, materialize=_materialize)


def _loads_strict_json(
    text: str,
    *,
    parse_float: Callable[[str], float],
    materialize: Callable[[object], object],
) -> object:
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_ObjectPairs,
            parse_float=parse_float,
            parse_constant=_reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise StrictJsonError("invalid JSON") from error

    operation_id = _unique_top_level_value(parsed, "operation_id")
    operation = _unique_top_level_value(parsed, "operation")
    try:
        return materialize(parsed)
    except StrictJsonError as error:
        raise StrictJsonError(
            str(error),
            operation_id=operation_id,
            operation=operation,
            duplicate_key=error.duplicate_key,
        ) from error
    except RecursionError as error:
        raise StrictJsonError("invalid JSON", operation_id=operation_id, operation=operation) from error


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
    if not isinstance(value, (_ObjectPairs, list)):
        return value

    root: dict[str, object] | list[object] = {} if isinstance(value, _ObjectPairs) else []
    stack: list[tuple[_ObjectPairs | list[object], dict[str, object] | list[object]]] = [(value, root)]
    while stack:
        source, target = stack.pop()
        if isinstance(source, _ObjectPairs):
            if not isinstance(target, dict):
                raise TypeError("object pairs require a dictionary target")
            for key, item in source:
                if key in target:
                    raise StrictJsonError(f"duplicate JSON key: {key}", duplicate_key=True)
                if isinstance(item, _ObjectPairs):
                    child: dict[str, object] | list[object] = {}
                    target[key] = child
                    stack.append((item, child))
                elif isinstance(item, list):
                    child = []
                    target[key] = child
                    stack.append((item, child))
                else:
                    target[key] = item
            continue

        if not isinstance(target, list):
            raise TypeError("array values require a list target")
        target.extend([None] * len(source))
        for index, item in enumerate(source):
            if isinstance(item, _ObjectPairs):
                child = {}
                target[index] = child
                stack.append((item, child))
            elif isinstance(item, list):
                child = []
                target[index] = child
                stack.append((item, child))
            else:
                target[index] = item
    return root


def _materialize_recursive(value: object) -> object:
    if isinstance(value, _ObjectPairs):
        result: dict[str, object] = {}
        for key, item in value:
            if key in result:
                raise StrictJsonError(f"duplicate JSON key: {key}", duplicate_key=True)
            result[key] = _materialize_recursive(item)
        return result
    if isinstance(value, list):
        return [_materialize_recursive(item) for item in value]
    return value


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number must be finite")
    return parsed


def _parse_transport_float(value: str) -> _PreservedJsonFloat:
    _parse_finite_float(value)
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("JSON number must be a decimal") from error
    _, digits, exponent = decimal_value.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits)) if digits else 0
    if coefficient == 0:
        return _PreservedJsonFloat(value)
    if exponent > 0:
        coefficient *= 10**exponent
        exponent = 0
    while exponent < 0 and coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    if exponent < -28 or coefficient > 2**96 - 1:
        raise ValueError("JSON number must be exactly representable as System.Decimal")
    return _PreservedJsonFloat(value)


__all__ = ["StrictJsonError", "is_json_utf8_media_type", "loads_strict_json", "loads_transport_json"]
