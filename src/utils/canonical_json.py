"""跨领域共享的稳定 JSON 字节与摘要。"""

from __future__ import annotations

import hashlib
import json


def canonical_json_bytes(value: object) -> bytes:
    """使用稳定键顺序、紧凑分隔符和 UTF-8 编码 JSON 值。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def canonical_json_digest(value: object) -> str:
    """返回 canonical JSON 字节的 SHA-256 十六进制摘要。"""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = ["canonical_json_bytes", "canonical_json_digest"]
