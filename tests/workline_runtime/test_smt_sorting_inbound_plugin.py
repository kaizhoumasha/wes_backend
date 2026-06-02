"""SMT 分拣入库插件 manifest 合同测试。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_NG_PLACE_RESULT,
    EVENT_SOURCE_PICK_RESULT,
    EVENT_TARGET_PLACE_RESULT,
    EVENT_WORKING_BIN_SCAN,
    PHASE_WAITING_SCAN,
    ROLE_SORTING_NG_ARM,
    ROLE_SORTING_NG_STATION,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.runtime_intent import RuntimeIntentKind

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


def test_smt_sorting_inbound_plugin_is_registered() -> None:
    definition = get_workline_plugin_definition(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert definition is not None
    assert definition.plugin_class is SmtSortingInboundPlugin
    assert definition.manifest is SmtSortingInboundPlugin.manifest


def test_smt_sorting_inbound_manifest_declares_required_roles() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert {requirement.role for requirement in manifest.required_device_roles} == {
        ROLE_SORTING_SOURCE_ARM,
        ROLE_SORTING_TARGET_ARM,
        ROLE_SORTING_NG_ARM,
        ROLE_SORTING_SCAN_PLATFORM,
        ROLE_SORTING_NG_STATION,
        ROLE_SORTING_WORKSTATION,
    }
    assert all(requirement.min_count == 1 for requirement in manifest.required_device_roles)


def test_smt_sorting_inbound_manifest_declares_command_and_event_roles() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert manifest.command_target_roles == {
        COMMAND_SOURCE_PICK: (ROLE_SORTING_SOURCE_ARM,),
        COMMAND_TARGET_PLACE: (ROLE_SORTING_TARGET_ARM,),
        COMMAND_NG_PLACE: (ROLE_SORTING_NG_ARM,),
    }
    assert manifest.event_source_roles == {
        EVENT_SOURCE_PICK_RESULT: (ROLE_SORTING_SOURCE_ARM,),
        EVENT_TARGET_PLACE_RESULT: (ROLE_SORTING_TARGET_ARM,),
        EVENT_NG_PLACE_RESULT: (ROLE_SORTING_NG_ARM,),
        EVENT_WORKING_BIN_SCAN: (ROLE_SORTING_SCAN_PLATFORM,),
    }


def test_smt_sorting_inbound_manifest_keeps_platform_start_out_of_business_events() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert "WORKLINE_START_REQUESTED" not in manifest.supported_events
    assert "WORKLINE_START_REQUESTED" not in manifest.event_source_roles


def test_smt_sorting_inbound_plugin_does_not_hard_code_device_codes() -> None:
    source = inspect.getsource(SmtSortingInboundPlugin)

    assert "ARM01" not in source
    assert "ARM02" not in source


def _ctx(session_context: dict[str, Any] | None = None) -> PluginContext:
    return cast(
        "PluginContext",
        SimpleNamespace(
            trace_id="trace-sorting-inbound",
            config={},
            logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
            normalized_input=None,
            session=SimpleNamespace(
                id=1001,
                context_json=session_context
                or {
                    "sorting": {
                        "context_schema_version": 1,
                        "stations": {"scan_platform": "EMPTY"},
                    }
                },
            ),
            services=SimpleNamespace(),
        ),
    )


def _source_pick_success_inbox(data: dict[str, Any] | None = None) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2001,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-SOURCE-PICK-001",
                "device_code": "SORT-SOURCE-ARM",
                "task_type": COMMAND_SOURCE_PICK,
                "result": "SUCCESS",
                "data": {
                    "bin_code": "SRC-BIN-01",
                    "bin_cell_index": "A01",
                    "bin_cell_code": "A01",
                    "material_identity_key": "mid:pkg-001",
                    "pkg_code": "PKG-001",
                    "wms_inventory_id": "WMS-001",
                    "reel_thickness": "7.125",
                    "source_version": "12",
                    **(data or {}),
                },
            },
        ),
    )


@pytest.mark.asyncio
async def test_source_pick_success_emits_unmounted_fact_before_opening_current_material() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(_ctx(), _source_pick_success_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.UPDATE_CONTEXT]
    unmounted_intent = intents[0]
    assert unmounted_intent.action == "MATERIAL_UNMOUNTED"
    assert unmounted_intent.payload_json == {
        "bin_code": "SRC-BIN-01",
        "bin_cell_index": "A01",
        "bin_cell_code": "A01",
        "material_identity_key": "mid:pkg-001",
        "pkg_code": "PKG-001",
        "wms_inventory_id": "WMS-001",
        "reel_thickness": "7.125",
        "source_version": "12",
        "source_event_id": "CMD-SOURCE-PICK-001",
    }

    sorting_patch = intents[1].context_patch["sorting"]
    assert sorting_patch["current_material"]["source_bin_code"] == "SRC-BIN-01"
    assert sorting_patch["current_material"]["source_cell_code"] == "A01"
    assert sorting_patch["current_material"]["material_identity_key"] == "mid:pkg-001"
    assert sorting_patch["current_material"]["reel_thickness_mm"] == "7.125"
    assert sorting_patch["stations"]["scan_platform"] == "OCCUPIED"
    assert sorting_patch["business_phase"] == PHASE_WAITING_SCAN


@pytest.mark.asyncio
async def test_source_pick_success_requires_empty_scan_platform() -> None:
    plugin = SmtSortingInboundPlugin()
    context = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "OCCUPIED"},
        }
    }

    intents = await plugin.on_command_result(_ctx(context), _source_pick_success_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_SCAN_PLATFORM_OCCUPIED"


@pytest.mark.asyncio
async def test_source_pick_success_does_not_unmount_again_when_current_material_is_open() -> None:
    plugin = SmtSortingInboundPlugin()
    context = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
            "current_material": {
                "source_bin_code": "SRC-BIN-00",
                "source_cell_code": "A00",
                "material_identity_key": "mid:existing",
                "reel_thickness_mm": "7.000",
            },
        }
    }

    intents = await plugin.on_command_result(_ctx(context), _source_pick_success_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_CURRENT_MATERIAL_OPEN"
