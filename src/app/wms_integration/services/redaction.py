"""WMS evidence 脱敏和 canonical hash 工具。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

REDACTED_VALUE = "***REDACTED***"
MAX_SNAPSHOT_COLLECTION_ITEMS = 50
MAX_SNAPSHOT_DEPTH = 8
MAX_SNAPSHOT_JSON_BYTES = 32_000
MAX_SNAPSHOT_KEYS = 120
MAX_SNAPSHOT_STRING_LENGTH = 2_048
SNAPSHOT_TRUNCATED_MARKER = "__snapshot_truncated__"

_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "apikey",
    "cookie",
    "password",
    "secret",
    "signature",
    "token",
)


def _normalise_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: object) -> bool:
    normalised = _normalise_key(key)
    return any(marker in normalised for marker in _SENSITIVE_KEY_MARKERS)


def redact_sensitive(value: Any) -> Any:
    """递归脱敏 WMS 调用快照中的敏感字段。"""

    if isinstance(value, Mapping):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive(nested) for key, nested in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value


def bounded_redacted_snapshot(value: Any) -> Any:
    """生成可持久化的脱敏快照，并为单行 JSON 体积设置边界。"""

    redacted = redact_sensitive(value)
    bounded = _bound_snapshot_value(redacted)
    if _json_size_bytes(bounded) <= MAX_SNAPSHOT_JSON_BYTES:
        return bounded
    return _build_oversize_snapshot_summary(redacted)


def canonical_sha256(value: Any) -> str:
    """对 JSON 语义等价的值生成稳定 sha256。"""

    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bound_snapshot_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_SNAPSHOT_DEPTH:
        return {"__truncated_reason__": "max_depth"}

    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        items = list(value.items())
        for key, nested in items[:MAX_SNAPSHOT_KEYS]:
            bounded[str(key)] = _bound_snapshot_value(nested, depth=depth + 1)
        if len(items) > MAX_SNAPSHOT_KEYS:
            bounded["__truncated_keys__"] = len(items) - MAX_SNAPSHOT_KEYS
        return bounded

    if isinstance(value, list | tuple):
        bounded_items = [_bound_snapshot_value(item, depth=depth + 1) for item in value[:MAX_SNAPSHOT_COLLECTION_ITEMS]]
        if len(value) > MAX_SNAPSHOT_COLLECTION_ITEMS:
            bounded_items.append({"__truncated_items__": len(value) - MAX_SNAPSHOT_COLLECTION_ITEMS})
        return bounded_items

    if isinstance(value, str):
        return _truncate_string(value, MAX_SNAPSHOT_STRING_LENGTH)

    return value


def _build_oversize_snapshot_summary(redacted: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        SNAPSHOT_TRUNCATED_MARKER: True,
        "payload_kind": type(redacted).__name__,
        "original_json_bytes": _json_size_bytes(redacted),
    }
    if isinstance(redacted, Mapping):
        keys = sorted(str(key) for key in redacted)
        summary["payload_keys"] = [_truncate_string(key, 240) for key in keys[:MAX_SNAPSHOT_KEYS]]
        summary["payload_key_count"] = len(keys)
    elif isinstance(redacted, list | tuple):
        summary["payload_item_count"] = len(redacted)
    return summary


def _truncate_string(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    omitted = len(value) - max_length
    return f"{value[:max_length]}...<truncated chars={omitted}>"


def _json_size_bytes(value: Any) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    )


__all__ = [
    "MAX_SNAPSHOT_JSON_BYTES",
    "REDACTED_VALUE",
    "SNAPSHOT_TRUNCATED_MARKER",
    "bounded_redacted_snapshot",
    "canonical_sha256",
    "redact_sensitive",
]
