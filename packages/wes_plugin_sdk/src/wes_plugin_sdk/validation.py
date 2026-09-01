"""核心与工作线插件共享的稳定值合同校验。"""

from typing import TypeGuard


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
    "is_opaque_face",
    "is_persistable_text",
    "validate_opaque_face",
    "validate_persistable_text",
    "validate_required_refs",
    "validate_required_text",
)
