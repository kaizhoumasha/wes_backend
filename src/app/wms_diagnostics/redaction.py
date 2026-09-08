"""展示前脱敏，遍历和输出均受硬上限约束。"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Literal

_SENSITIVE = frozenset(
    {"authorization", "cookie", "setcookie", "password", "passwd", "secret", "token", "apikey", "credential"}
)


def sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "").replace("-", "")
    return any(part in normalized for part in _SENSITIVE)


def protocol_headers(headers: tuple[tuple[str, str], ...]) -> tuple[list[tuple[str, str]], bool]:
    """只保存应用层协议字段，保留重复值以供排查，禁止凭据进入展示。"""
    result = []
    incomplete = len(headers) > 128
    for name, value in headers[:128]:
        if name.lower() not in {"content-type", "content-length", "content-encoding", "retry-after"}:
            continue
        if len(result) == 16:
            incomplete = True
            break
        encoded = value[:256].encode("utf-8", errors="replace")
        incomplete |= len(value) > 256 or len(encoded) > 256
        result.append((name.lower(), encoded[:256].decode("utf-8", errors="ignore")))
    return result, incomplete


def safe_value(value: object, *, deadline: float | None = None) -> tuple[Any, bool]:
    """返回有界 JSON 展示值；不调用未知对象的字符串转换。"""
    nodes = 0
    remaining_bytes = 8192
    incomplete = False
    active: set[int] = set()

    def visit(item: object, depth: int) -> Any:
        nonlocal nodes, incomplete, remaining_bytes
        nodes += 1
        if (
            nodes > 2048
            or depth > 32
            or remaining_bytes <= 0
            or (deadline is not None and time.monotonic() >= deadline)
        ):
            incomplete = True
            return "[TRUNCATED]"
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            encoded = item[:2048].encode("utf-8", errors="replace")
            budget = min(len(encoded), max(0, remaining_bytes))
            if len(item) > 2048 or budget < len(encoded):
                incomplete = True
            remaining_bytes -= budget
            return encoded[:budget].decode("utf-8", errors="ignore")
        if isinstance(item, int) and item.bit_length() <= 256:
            return item
        if isinstance(item, float) and math.isfinite(item):
            return item
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active:
                incomplete = True
                return "[TRUNCATED]"
            active.add(identity)
            result: Any = {} if isinstance(item, dict) else []
            for key, child in item.items() if isinstance(item, dict) else enumerate(item):
                if nodes >= 2048 or remaining_bytes <= 0 or (deadline is not None and time.monotonic() >= deadline):
                    incomplete = True
                    break
                if isinstance(item, dict):
                    nodes += 1
                    if not isinstance(key, str) or len(key) > 256:
                        incomplete = True
                        continue
                    remaining_bytes -= len(key.encode("utf-8", errors="replace")) + 32
                    result[key] = "[REDACTED]" if sensitive_key(key) else visit(child, depth + 1)
                else:
                    remaining_bytes -= 8
                    result.append(visit(child, depth + 1))
            active.remove(identity)
            return result
        incomplete = True
        return "[UNAVAILABLE]"

    result = visit(value, 0)
    return result, incomplete


def preview_json(
    raw: bytes, *, max_bytes: int = 8192, deadline: float | None = None
) -> tuple[str | None, Literal["EMPTY", "TRUNCATED", "UNSAFE_JSON", "CAPTURED"]]:
    """无法安全解析的正文不保存；UTF-8 截断不产生非法编码。"""
    if not raw:
        return None, "EMPTY"
    if len(raw) > 256 * 1024:
        return None, "TRUNCATED"
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None, "UNSAFE_JSON"
    safe, incomplete = safe_value(value, deadline=deadline)
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), "TRUNCATED" if incomplete or len(
        encoded
    ) > max_bytes else "CAPTURED"
