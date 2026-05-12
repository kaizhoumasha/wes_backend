from types import SimpleNamespace

import pytest

from src.workline_runtime.null_plugin import NullPlugin


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(logger=SimpleNamespace(info=lambda *_: None, warning=lambda *_: None))


@pytest.mark.asyncio
async def test_null_plugin_handlers_return_empty_runtime_intents() -> None:
    plugin = NullPlugin()
    inbox = SimpleNamespace(
        id=1,
        payload_json={"event_type": "SCAN_COMPLETED", "command_type": "PICK_AND_PUT", "result": "SUCCESS"},
    )

    assert await plugin.on_device_event(_ctx(), inbox) == []
    assert await plugin.on_command_result(_ctx(), inbox) == []
    assert await plugin.on_external_http(_ctx(), inbox) == []
    assert await plugin.on_manual_operation(_ctx(), inbox) == []
