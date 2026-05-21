from types import SimpleNamespace

import pytest

from src.workline_runtime.plugin_base import WorklinePlugin, build_payload_invalid_block, on_event
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind


class RuntimeNativePlugin(WorklinePlugin):
    plugin_key = "runtime_native"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return ctx.next.update_context({"pkg_id": event.payload_json["data"]["PkgID"]})


class InvalidReturnPlugin(WorklinePlugin):
    plugin_key = "invalid_return"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return "bad"


def _ctx() -> SimpleNamespace:
    from src.workline_runtime.plugin_next import PluginNext

    return SimpleNamespace(
        next=PluginNext(),
        logger=SimpleNamespace(warning=lambda *_: None, error=lambda *_: None, exception=lambda *_: None),
        normalized_input=None,
        trace_id="trace-test",
    )


def _inbox(event_type: str = "SCAN_COMPLETED") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        payload_json={
            "event_type": event_type,
            "data": {"PkgID": "L0001-1"},
        },
    )


@pytest.mark.asyncio
async def test_handler_returns_runtime_intent_list() -> None:
    intents = await RuntimeNativePlugin().on_device_event(_ctx(), _inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    assert intents[0].context_patch == {"pkg_id": "L0001-1"}


@pytest.mark.asyncio
async def test_missing_handler_returns_empty_intents() -> None:
    intents = await RuntimeNativePlugin().on_device_event(_ctx(), _inbox("UNKNOWN_EVENT"))

    assert intents == []


@pytest.mark.asyncio
async def test_invalid_handler_return_is_rejected() -> None:
    with pytest.raises(TypeError, match="Plugin handler must return RuntimeIntent"):
        await InvalidReturnPlugin().on_device_event(_ctx(), _inbox())


def test_build_payload_invalid_block_returns_block_intent() -> None:
    intent = build_payload_invalid_block("缺少 PkgID")

    assert isinstance(intent, RuntimeIntent)
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "PAYLOAD_INVALID"
