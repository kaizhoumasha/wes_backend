"""读取正式 JSON Schema 与已经产生的校验错误，仅生成展示项。"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from src.app.wms_diagnostics.contracts import FieldComparison
from src.app.wms_diagnostics.redaction import safe_value, sensitive_key


def compare_fields(
    schema: dict[str, Any],
    actual: object,
    *,
    side: Literal["request", "response"],
    source: str,
    errors: tuple[dict[str, Any], ...] = (),
    validated: bool = False,
    deadline: float | None = None,
) -> list[FieldComparison]:
    """不校验输入、不选择响应分支；调用者提供当次实际使用的 schema。"""
    result: list[FieldComparison] = []
    error_paths = {tuple(error.get("loc", ())) for error in errors[:256]}
    visited: set[tuple] = set()
    nodes = 0

    def visit(rule: dict[str, Any], value: object, location: tuple, present: bool, required: bool, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 2048 or len(result) >= 256 or (deadline is not None and time.monotonic() >= deadline):
            return
        reference = rule.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            rule = schema.get("$defs", {}).get(reference[8:], {})
        # 仅展开原校验已成功采用的显式 discriminator；不试验或重验候选分支。
        discriminator = rule.get("discriminator", {})
        if validated and isinstance(value, dict) and discriminator:
            tag = value.get(discriminator.get("propertyName"))
            reference = discriminator.get("mapping", {}).get(tag) if isinstance(tag, str) else None
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                rule = schema.get("$defs", {}).get(reference[8:], {})
        constraints = {
            key: rule[key]
            for key in (
                "type",
                "enum",
                "const",
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
                "pattern",
                "anyOf",
                "oneOf",
                "allOf",
                "if",
                "then",
                "else",
                "additionalProperties",
            )
            if key in rule
        }
        if required:
            constraints["required"] = True
        visited.add(location)
        matching_errors = [error for error in errors[:256] if tuple(error.get("loc", ())) == location]
        if matching_errors:
            constraints["validation_errors"] = [
                {"code": error.get("type"), "message": error.get("msg")} for error in matching_errors
            ]
        safe_rule, _ = safe_value(constraints, deadline=deadline)
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in location)
        hidden = any(isinstance(part, str) and sensitive_key(part) for part in location)
        safe_actual, incomplete = safe_value("[REDACTED]" if hidden else value, deadline=deadline)
        expected = matching_errors[0].get("expected_value") if matching_errors else None
        safe_expected, _ = safe_value("[REDACTED]" if hidden and expected is not None else expected, deadline=deadline)
        result.append(
            FieldComparison(
                side=side,
                path=path[:1024],
                expected_rule=json.dumps(safe_rule, ensure_ascii=False)[:2048],
                expected_value=safe_expected,
                actual_present=present,
                actual_value=safe_actual if present else None,
                verdict="ERROR"
                if location in error_paths
                else "PASS"
                if validated and not incomplete
                else "NOT_VALIDATED",
                source=source[:256],
            )
        )
        if hidden:
            return
        properties = rule.get("properties", {})
        if isinstance(properties, dict):
            for name, child in properties.items():
                if len(result) >= 256:
                    break
                exists = isinstance(value, dict) and name in value
                visit(
                    child,
                    value[name] if isinstance(value, dict) and exists else None,
                    (*location, name),
                    exists,
                    name in rule.get("required", ()),
                    depth + 1,
                )
        if isinstance(value, dict):
            for name in value:
                if len(result) >= 256:
                    break
                if name not in properties:
                    visit({}, value[name], (*location, name), True, False, depth + 1)
        if isinstance(value, list) and isinstance(rule.get("items"), dict):
            for index, child in enumerate(value):
                if len(result) >= 256:
                    break
                visit(rule["items"], child, (*location, index), True, False, depth + 1)

    visit(schema, actual, (), True, False, 0)
    # 联合 DTO 的错误路径可能包含模型名，不把它猜成 WIRE 字段路径。
    # 保留原校验器定位，以根正文作为实际值，缺失字段与分支名仍可区分。
    for error in errors[:256]:
        if tuple(error.get("loc", ())) in visited or len(result) >= 256:
            continue
        if deadline is not None and time.monotonic() >= deadline:
            break
        rule, _ = safe_value(
            {"validation_error": error.get("type"), "validator_location": list(error.get("loc", ()))}, deadline=deadline
        )
        value, _ = safe_value(actual, deadline=deadline)
        result.append(
            FieldComparison(
                side=side,
                path="$",
                expected_rule=json.dumps(rule, ensure_ascii=False)[:2048],
                actual_present=True,
                actual_value=value,
                verdict="ERROR",
                source=source[:256],
            )
        )
    return result
