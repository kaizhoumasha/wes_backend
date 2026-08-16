"""Transport callback 的 token-preserving JSON 规范化。"""

from __future__ import annotations

import json


def canonical_callback_json(value: object) -> str:
    chunks: list[str] = []
    stack: list[tuple[str, object]] = [("value", value)]
    while stack:
        kind, item = stack.pop()
        if kind == "text":
            chunks.append(str(item))
            continue

        lexeme = getattr(item, "lexeme", None)
        if isinstance(item, float) and isinstance(lexeme, str):
            chunks.append(lexeme)
        elif isinstance(item, dict):
            events: list[tuple[str, object]] = [("text", "{")]
            for index, (key, child) in enumerate(sorted(item.items())):
                if index:
                    events.append(("text", ","))
                events.extend(
                    (
                        ("text", json.dumps(key, ensure_ascii=True)),
                        ("text", ":"),
                        ("value", child),
                    )
                )
            events.append(("text", "}"))
            stack.extend(reversed(events))
        elif isinstance(item, list):
            events = [("text", "[")]
            for index, child in enumerate(item):
                if index:
                    events.append(("text", ","))
                events.append(("value", child))
            events.append(("text", "]"))
            stack.extend(reversed(events))
        else:
            chunks.append(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
    return "".join(chunks)


__all__ = ["canonical_callback_json"]
