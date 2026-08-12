"""项目统一的 UUIDv7 生成与校验。"""

from __future__ import annotations

import secrets
import time
from uuid import RFC_4122, UUID

_MAX_TIMESTAMP_MS = (1 << 48) - 1
_MAX_RANDOM_BITS = (1 << 74) - 1


def new_uuid7(*, timestamp_ms: int | None = None, random_bits: int | None = None) -> str:
    """生成符合 RFC 9562 位布局的 UUIDv7 字符串。"""

    timestamp_ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    random_bits = secrets.randbits(74) if random_bits is None else random_bits
    if (
        not isinstance(timestamp_ms, int)
        or isinstance(timestamp_ms, bool)
        or not 0 <= timestamp_ms <= _MAX_TIMESTAMP_MS
    ):
        raise ValueError("timestamp_ms must be a 48-bit non-negative integer")
    if not isinstance(random_bits, int) or isinstance(random_bits, bool) or not 0 <= random_bits <= _MAX_RANDOM_BITS:
        raise ValueError("random_bits must be a 74-bit non-negative integer")

    value = (
        (timestamp_ms << 80)
        | (0b0111 << 76)
        | ((random_bits >> 62) << 64)
        | (0b10 << 62)
        | (random_bits & ((1 << 62) - 1))
    )
    return str(UUID(int=value))


def is_uuid7(value: object) -> bool:
    """判断输入是否为 RFC 4122 variant 的 UUIDv7 文本。"""

    if not isinstance(value, str):
        return False
    try:
        candidate = UUID(value)
    except (ValueError, AttributeError):
        return False
    return candidate.version == 7 and candidate.variant == RFC_4122 and str(candidate) == value.lower()


__all__ = ["is_uuid7", "new_uuid7"]
