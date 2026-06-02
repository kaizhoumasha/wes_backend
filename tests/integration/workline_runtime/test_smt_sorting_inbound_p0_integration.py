"""SMT 分拣入库 P0 插件 intent smoke。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.workline_plugins.smt_sorting_inbound.constants import COMMAND_SOURCE_PICK
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.runtime_intent import RuntimeIntentKind

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


def _ctx(session_context: dict[str, Any]) -> PluginContext:
    return cast(
        "PluginContext",
        SimpleNamespace(
            trace_id="trace-sorting-inbound-p0",
            config={},
            logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
            normalized_input=None,
            session=SimpleNamespace(id=3001, context_json=session_context),
            services=SimpleNamespace(),
        ),
    )


def _command_inbox(data: dict[str, Any]) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=4001,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-SOURCE-PICK-SMOKE",
                "device_code": "SORT-SOURCE-ARM",
                "task_type": COMMAND_SOURCE_PICK,
                "result": "SUCCESS",
                "data": data,
            },
        ),
    )


def _apply_context(session_context: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    session_context.update(patch)
    return session_context


@pytest.mark.asyncio
async def test_source_pick_smoke_unmounts_source_and_opens_current_material_once() -> None:
    plugin = SmtSortingInboundPlugin()
    session_context: dict[str, Any] = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
        }
    }

    intents = await plugin.on_command_result(
        _ctx(session_context),
        _command_inbox(
            {
                "bin_code": "SRC-BIN-01",
                "bin_cell_index": "A01",
                "bin_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "pkg_code": "PKG-001",
                "reel_thickness": "7.125",
            }
        ),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.UPDATE_CONTEXT]
    assert intents[0].action == "MATERIAL_UNMOUNTED"
    session_context = _apply_context(session_context, intents[1].context_patch)
    assert session_context["sorting"]["current_material"]["material_identity_key"] == "mid:pkg-001"

    replay_intents = await plugin.on_command_result(
        _ctx(session_context),
        _command_inbox(
            {
                "bin_code": "SRC-BIN-01",
                "bin_cell_index": "A01",
                "bin_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "pkg_code": "PKG-001",
                "reel_thickness": "7.125",
            }
        ),
    )

    assert [intent.kind for intent in replay_intents] == [RuntimeIntentKind.BLOCK]
    assert replay_intents[0].reason_code == "SORTING_CURRENT_MATERIAL_OPEN"
