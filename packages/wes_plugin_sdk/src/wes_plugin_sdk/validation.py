"""核心与工作线插件共享的稳定值合同校验。"""

import math
from typing import Never, SupportsIndex, TypeGuard, cast


class _FrozenDict(dict[str, object]):
    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("frozen JSON object cannot be mutated")

    def __setitem__(self, _key: str, _value: object) -> Never:
        self._reject_mutation()

    def __delitem__(self, _key: str) -> Never:
        self._reject_mutation()

    def clear(self) -> Never:
        self._reject_mutation()

    def pop(self, _key: str, _default: object = None) -> Never:
        self._reject_mutation()

    def popitem(self) -> Never:
        self._reject_mutation()

    def setdefault(self, _key: str, _default: object = None) -> Never:
        self._reject_mutation()

    def update(self, *_args: object, **_kwargs: object) -> Never:
        self._reject_mutation()

    def __ior__(self, _value: object) -> Never:
        self._reject_mutation()


class _FrozenList(list[object]):
    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("frozen JSON array cannot be mutated")

    def __setitem__(self, _key: SupportsIndex | slice, _value: object) -> Never:
        self._reject_mutation()

    def __delitem__(self, _key: SupportsIndex | slice) -> Never:
        self._reject_mutation()

    def __iadd__(self, _value: object) -> Never:
        self._reject_mutation()

    def __imul__(self, _value: object) -> Never:
        self._reject_mutation()

    def append(self, _value: object) -> Never:
        self._reject_mutation()

    def clear(self) -> Never:
        self._reject_mutation()

    def extend(self, _value: object) -> Never:
        self._reject_mutation()

    def insert(self, _index: SupportsIndex, _value: object) -> Never:
        self._reject_mutation()

    def pop(self, _index: SupportsIndex = -1) -> Never:
        self._reject_mutation()

    def remove(self, _value: object) -> Never:
        self._reject_mutation()

    def reverse(self) -> Never:
        self._reject_mutation()

    def sort(self, *, key: object = None, reverse: bool = False) -> Never:
        del key, reverse
        self._reject_mutation()


def freeze_json_object(value: object, field_name: str) -> dict[str, object]:
    """校验 JSON object 并递归冻结，阻止 Decision 构造后的别名修改。"""

    if type(value) is not dict:
        raise TypeError(f"{field_name} must be a dict")
    return cast("dict[str, object]", _freeze_json_value(value, field_name))


def _freeze_json_value(value: object, field_name: str) -> object:
    if type(value) is dict:
        frozen: dict[str, object] = {}
        for key, nested in value.items():
            if type(key) is not str:
                raise TypeError(f"{field_name} keys must be strings")
            frozen[key] = _freeze_json_value(nested, field_name)
        return _FrozenDict(frozen)
    if type(value) is list:
        return _FrozenList(_freeze_json_value(item, field_name) for item in value)
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{field_name} must not contain non-finite numbers")
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise TypeError(f"{field_name} must contain only JSON values")


def validate_required_text(
    value: object,
    field_name: str,
    *,
    error_type: type[ValueError] = ValueError,
) -> str:
    """校验调用边界要求的非空白字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field_name} must not be blank")
    return value


def validate_required_refs(values: tuple[str, ...], field_name: str) -> None:
    """校验不可变、非空且无重复的引用集合。"""
    if type(values) is not tuple:
        raise TypeError(f"{field_name} must be a tuple")
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    for value in values:
        validate_required_text(value, field_name)


def validate_persistable_text(
    value: object,
    field_name: str,
    *,
    max_length: int | None = None,
    error_type: type[ValueError] = ValueError,
) -> str:
    """校验可安全持久化和跨线传输的非空白 UTF-8 字符串。"""
    text = validate_required_text(value, field_name, error_type=error_type)
    _validate_nul_length_utf8(text, field_name, max_length=max_length, error_type=error_type)
    return text


def validate_opaque_face(
    value: object,
    field_name: str,
    *,
    error_type: type[ValueError] = ValueError,
) -> None:
    """校验不解释内容的 face 字符串，同时保留调用边界的异常类型。"""
    if type(value) is not str or value == "":
        raise error_type(f"{field_name} must be a non-empty string")
    _validate_nul_length_utf8(value, field_name, max_length=None, error_type=error_type)


def _validate_nul_length_utf8(
    value: str,
    field_name: str,
    *,
    max_length: int | None,
    error_type: type[ValueError],
) -> None:
    if "\x00" in value:
        raise error_type(f"{field_name} must not contain NUL")
    if max_length is not None and len(value) > max_length:
        raise error_type(f"{field_name} exceeds {max_length} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise error_type(f"{field_name} must be valid UTF-8") from error


def is_opaque_face(value: object) -> TypeGuard[str]:
    """返回值是否满足 opaque face 合同。"""
    try:
        validate_opaque_face(value, "face")
    except ValueError:
        return False
    return True


def is_persistable_text(value: object, max_length: int) -> TypeGuard[str]:
    """返回值是否满足有长度上限的可持久化文本合同。"""
    try:
        validate_persistable_text(value, "text", max_length=max_length)
    except ValueError:
        return False
    return True


__all__ = (
    "freeze_json_object",
    "is_opaque_face",
    "is_persistable_text",
    "validate_opaque_face",
    "validate_persistable_text",
    "validate_required_refs",
    "validate_required_text",
)
