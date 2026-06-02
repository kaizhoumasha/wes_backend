"""SMT 分拣入库插件 manifest 合同测试。"""

from __future__ import annotations

import inspect

from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_NG_PLACE_RESULT,
    EVENT_SOURCE_PICK_RESULT,
    EVENT_TARGET_PLACE_RESULT,
    EVENT_WORKING_BIN_SCAN,
    ROLE_SORTING_NG_ARM,
    ROLE_SORTING_NG_STATION,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin


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
