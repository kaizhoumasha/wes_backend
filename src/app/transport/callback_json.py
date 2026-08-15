"""Transport callback 的 token-preserving JSON 规范化。"""

from __future__ import annotations

import json


def canonical_callback_json(value: object) -> str:
    lexeme = getattr(value, "lexeme", None)
    if isinstance(value, float) and isinstance(lexeme, str):
        return lexeme
    if isinstance(value, dict):
        items = sorted(value.items())
        encoded_items = (f"{json.dumps(key, ensure_ascii=True)}:{canonical_callback_json(item)}" for key, item in items)
        return "{" + ",".join(encoded_items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical_callback_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


__all__ = ["canonical_callback_json"]
