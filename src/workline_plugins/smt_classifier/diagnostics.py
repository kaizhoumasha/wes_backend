"""SMT 插件级诊断入口。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.workline_runtime.plugin_sdk import normalize_inbox_input
from src.workline_runtime.utils import non_empty_str

from .context import SmtClassifierContext, parse_smt_context


class SmtPluginDiagnosticResult(BaseModel):
    """单条 payload 的插件级诊断结果。"""

    normalized_input: dict[str, Any]
    parsed_context: SmtClassifierContext
    selected_handler: str | None = None
    runtime_intents: Any

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _make_inbox(payload_json: dict[str, Any], *, kind: str, trace_id: str | None = None) -> Any:
    return SimpleNamespace(
        id=None,
        kind=SimpleNamespace(value=kind),
        payload_json=payload_json,
        trace_id=trace_id,
    )


def _select_event_handler(plugin: Any, normalized_input: Any, payload_json: dict[str, Any]) -> str | None:
    event_type = non_empty_str(getattr(normalized_input, "canonical_event_type", None)) or non_empty_str(
        payload_json.get("event_type")
    )
    handler = getattr(plugin, "_event_handlers", {}).get(event_type)
    return getattr(handler, "__name__", None)


def _command_route_keys(normalized_input: Any, payload_json: dict[str, Any]) -> list[tuple[str, str | None]]:
    command_type = (
        non_empty_str(getattr(normalized_input, "command_type", None))
        or non_empty_str(payload_json.get("command_type"))
        or non_empty_str(payload_json.get("task_type"))
    )
    if not command_type:
        return []

    keys: list[tuple[str, str | None]] = []

    def _append(result_value: str | None) -> None:
        key = (command_type, result_value)
        if key not in keys:
            keys.append(key)

    source_result = non_empty_str(getattr(normalized_input, "source_result", None)) or non_empty_str(
        payload_json.get("result")
    )
    normalized_result = non_empty_str(getattr(normalized_input, "normalized_result", None))
    _append(source_result)
    _append(normalized_result)
    if normalized_result in {"TERMINAL_FAILURE", "RETRYABLE_FAILURE"}:
        _append("FAILED")
    _append(None)
    return keys


def _select_command_handler(plugin: Any, normalized_input: Any, payload_json: dict[str, Any]) -> str | None:
    handlers = getattr(plugin, "_command_handlers", {})
    for key in _command_route_keys(normalized_input, payload_json):
        handler = handlers.get(key)
        if handler is not None:
            return getattr(handler, "__name__", None)
    return None


async def diagnose_smt_payload(
    plugin: Any,
    ctx: Any,
    payload_json: dict[str, Any],
    *,
    kind: str = "DEVICE_EVENT",
) -> SmtPluginDiagnosticResult:
    """诊断单条 SMT payload 会如何被插件解释。

    该入口只覆盖 handler / context / RuntimeIntent 解释，不替代 WORKLINE 级 sandbox 调试。
    """

    inbox = _make_inbox(payload_json, kind=kind, trace_id=getattr(ctx, "trace_id", None))
    normalized_input = normalize_inbox_input(
        inbox,
        trace_id=getattr(ctx, "trace_id", None) or "",
        plugin_key=getattr(plugin, "plugin_key", None),
    )
    ctx.normalized_input = normalized_input
    parsed_context = parse_smt_context(ctx)

    selected_handler: str | None
    if kind == "COMMAND_RESULT":
        selected_handler = _select_command_handler(plugin, normalized_input, payload_json)
        runtime_intents = await plugin.on_command_result(ctx, inbox)
    else:
        selected_handler = _select_event_handler(plugin, normalized_input, payload_json)
        runtime_intents = await plugin.on_device_event(ctx, inbox)

    return SmtPluginDiagnosticResult(
        normalized_input=normalized_input.model_dump(),
        parsed_context=parsed_context,
        selected_handler=selected_handler,
        runtime_intents=runtime_intents,
    )


__all__ = ["SmtPluginDiagnosticResult", "diagnose_smt_payload"]
