"""插件与系统能力定义共享的稳定 identity 基础函数。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def canonical_json(value: Any) -> str:
    """生成不受字段插入顺序影响的紧凑 JSON。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_digest(value: Any) -> str:
    """计算 canonical JSON 的 SHA-256 摘要。"""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_key_version(value: str, *, field_name: str) -> str:
    """校验 extension key 与 contract version 使用稳定、非空标识符。"""

    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-empty stable identifier")
    return value


def stable_sort[T](values: Iterable[T], *, key: Callable[[T], Any] | None = None) -> tuple[T, ...]:
    """将声明集合转成稳定排序的不可变 tuple。"""

    return tuple(sorted(values, key=key))
