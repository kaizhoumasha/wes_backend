"""Orchestrator 内部事件分发合同测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.workline_runtime.orchestrator import OrchestratorService
from src.workline_runtime.runtime_intent import RuntimeIntent


def test_resolve_inbox_type_maps_internal_event_to_plugin_event_route() -> None:
    orchestrator = OrchestratorService()
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "data": {"handoff_source_item_id": 22},
        },
    )

    assert orchestrator._resolve_inbox_type(inbox) == "DEVICE_EVENT"


@pytest.mark.asyncio
async def test_internal_event_calls_plugin_device_event_handler_explicitly() -> None:
    calls: list[str] = []
    returned_intents = [RuntimeIntent.update_context({"source_pick_requested": True})]

    class Plugin:
        async def on_device_event(self, ctx, inbox):
            _ = ctx, inbox
            calls.append("device")
            return returned_intents

        async def on_command_result(self, ctx, inbox):
            _ = ctx, inbox
            calls.append("command")
            return []

        async def on_external_http(self, ctx, inbox):
            _ = ctx, inbox
            calls.append("external")
            return []

        async def on_manual_operation(self, ctx, inbox):
            _ = ctx, inbox
            calls.append("manual")
            return []

    orchestrator = OrchestratorService()
    inbox = SimpleNamespace(
        kind=SimpleNamespace(value="INTERNAL_EVENT"),
        payload_json={
            "message_type": "INTERNAL_EVENT",
            "event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
            "data": {"handoff_source_item_id": 22},
        },
    )

    result = await orchestrator._call_plugin(Plugin(), SimpleNamespace(), inbox)

    assert result == returned_intents
    assert calls == ["device"]
