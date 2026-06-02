"""SMT 分拣入库 WorkLine 插件 P0 manifest。"""

from __future__ import annotations

from typing import Any, cast

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
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.context import SortingInboundContext
from src.workline_runtime.plugin_base import WorklinePlugin
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest

COMMAND_TARGET_ROLES: dict[str, str] = {
    COMMAND_SOURCE_PICK: ROLE_SORTING_SOURCE_ARM,
    COMMAND_TARGET_PLACE: ROLE_SORTING_TARGET_ARM,
    COMMAND_NG_PLACE: ROLE_SORTING_NG_ARM,
}

EVENT_SOURCE_ROLES: dict[str, str] = {
    EVENT_SOURCE_PICK_RESULT: ROLE_SORTING_SOURCE_ARM,
    EVENT_TARGET_PLACE_RESULT: ROLE_SORTING_TARGET_ARM,
    EVENT_NG_PLACE_RESULT: ROLE_SORTING_NG_ARM,
    EVENT_WORKING_BIN_SCAN: ROLE_SORTING_SCAN_PLATFORM,
}


def _payload_data(payload_json: dict[str, Any]) -> dict[str, Any]:
    data = payload_json.get("data")
    return cast("dict[str, Any]", data.copy()) if isinstance(data, dict) else {}


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def resolve_sorting_inbound_business_key(payload_json: dict[str, Any]) -> str | None:
    """按现场扫码/命令 payload 派生分拣入库业务主键。"""

    data = _payload_data(payload_json)
    return (
        _non_empty_str(data.get("material_identity_key"))
        or _non_empty_str(data.get("PkgID"))
        or _non_empty_str(data.get("pkg_code"))
        or _non_empty_str(payload_json.get("business_key"))
    )


def classify_sorting_inbound_result(payload_json: dict[str, Any]) -> str | None:
    """读取设备结果字段，后续 flow handler 再解释业务分支。"""

    data = _payload_data(payload_json)
    return (
        _non_empty_str(payload_json.get("normalized_result"))
        or _non_empty_str(payload_json.get("result"))
        or _non_empty_str(data.get("result"))
        or _non_empty_str(data.get("status"))
    )


class SmtSortingInboundPlugin(WorklinePlugin):
    """SMT 分拣入库插件。

    Task 7 只声明 manifest/role 合同；业务 handler 在后续 flow task 中接入。
    """

    plugin_key = SMT_SORTING_INBOUND_PLUGIN_KEY
    contract_version = SMT_SORTING_INBOUND_CONTRACT_VERSION

    manifest = WorklinePluginManifest(
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        required_device_roles=(
            DeviceRoleRequirement(role=ROLE_SORTING_SOURCE_ARM, min_count=1, max_count=1),
            DeviceRoleRequirement(role=ROLE_SORTING_TARGET_ARM, min_count=1, max_count=1),
            DeviceRoleRequirement(role=ROLE_SORTING_NG_ARM, min_count=1, max_count=1),
            DeviceRoleRequirement(role=ROLE_SORTING_SCAN_PLATFORM, min_count=1, max_count=1),
            DeviceRoleRequirement(role=ROLE_SORTING_NG_STATION, min_count=1, max_count=1),
            DeviceRoleRequirement(role=ROLE_SORTING_WORKSTATION, min_count=1, max_count=1),
        ),
        business_key_resolver=resolve_sorting_inbound_business_key,
        result_classifier=classify_sorting_inbound_result,
        context_model=SortingInboundContext,
        supported_events=frozenset(EVENT_SOURCE_ROLES),
        supported_commands=frozenset(COMMAND_TARGET_ROLES),
        event_source_roles=EVENT_SOURCE_ROLES,
        command_target_roles=COMMAND_TARGET_ROLES,
    )


__all__ = [
    "COMMAND_TARGET_ROLES",
    "EVENT_SOURCE_ROLES",
    "SmtSortingInboundPlugin",
    "classify_sorting_inbound_result",
    "resolve_sorting_inbound_business_key",
]
