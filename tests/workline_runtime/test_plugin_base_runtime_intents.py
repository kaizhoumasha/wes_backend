"""插件基类 RuntimeIntent 返回边界测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.workline_runtime.plugin_base import (
    WorklinePlugin,
    build_payload_invalid_block,
    on_event,
)
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent, RuntimeIntentKind


def _ctx():
    return SimpleNamespace(
        next=PluginNext(),
        logger=MagicMock(),
        session=SimpleNamespace(context_json={}),
    )


def _inbox(event_type: str = "SCAN_COMPLETED", payload: dict | None = None):
    payload_json = {"event_type": event_type}
    if payload:
        payload_json.update(payload)
    return SimpleNamespace(id=1, payload_json=payload_json)


class ListIntentPlugin(WorklinePlugin):
    plugin_key = "list-intent"
    contract_version = "1.0"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, inbox):
        return [
            ctx.next.update_context({"barcode": inbox.payload_json["barcode"]}),
            ctx.next.command(
                device_role="ARM",
                action="PICK",
                payload={"barcode": inbox.payload_json["barcode"]},
                destination_role="ARM",
            ),
        ]


class SingleIntentPlugin(WorklinePlugin):
    plugin_key = "single-intent"
    contract_version = "1.0"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, inbox):
        return ctx.next.command(device_role="ARM", action="PICK", payload={})


class NoneIntentPlugin(WorklinePlugin):
    plugin_key = "none-intent"
    contract_version = "1.0"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, inbox):
        return None


class InvalidReturnPlugin(WorklinePlugin):
    plugin_key = "invalid-return"
    contract_version = "1.0"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, inbox):
        return {"kind": "COMMAND"}


class InvalidSequenceReturnPlugin(WorklinePlugin):
    plugin_key = "invalid-sequence-return"
    contract_version = "1.0"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, inbox):
        return inbox.payload_json["return_value"]


@pytest.mark.asyncio
async def test_device_event_handler_can_return_ordered_runtime_intent_list():
    result = await ListIntentPlugin().on_device_event(_ctx(), _inbox(payload={"barcode": "BC-001"}))

    assert isinstance(result, list)
    assert [intent.kind for intent in result] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert result[0].context_patch == {"barcode": "BC-001"}
    assert result[1].action == "PICK"


@pytest.mark.asyncio
async def test_handler_returning_single_runtime_intent_is_normalized_to_list():
    result = await SingleIntentPlugin().on_device_event(_ctx(), _inbox())

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].kind == RuntimeIntentKind.COMMAND


@pytest.mark.asyncio
async def test_handler_returning_none_is_normalized_to_empty_list():
    result = await NoneIntentPlugin().on_device_event(_ctx(), _inbox())

    assert result == []


@pytest.mark.asyncio
async def test_handler_returning_invalid_type_raises_type_error():
    with pytest.raises(
        TypeError,
        match="Plugin handler must return RuntimeIntent, list\\[RuntimeIntent\\], or None",
    ):
        await InvalidReturnPlugin().on_device_event(_ctx(), _inbox())


@pytest.mark.asyncio
@pytest.mark.parametrize("return_value", ["", b"", bytearray()])
async def test_handler_returning_empty_string_or_bytes_sequence_raises_type_error(return_value):
    with pytest.raises(
        TypeError,
        match="Plugin handler must return RuntimeIntent, list\\[RuntimeIntent\\], or None",
    ):
        await InvalidSequenceReturnPlugin().on_device_event(_ctx(), _inbox(payload={"return_value": return_value}))


def test_build_payload_invalid_block_returns_material_block_intent():
    intent = build_payload_invalid_block("缺少 barcode")

    assert isinstance(intent, RuntimeIntent)
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.block_scope == BlockScope.MATERIAL
    assert intent.reason_code == "PAYLOAD_INVALID"
    assert intent.message == "缺少 barcode"
    assert intent.suggested_action == "检查设备回调 payload"
