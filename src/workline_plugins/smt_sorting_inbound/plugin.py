"""SMT 分拣入库 WorkLine 插件 P0 manifest。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_NG_PLACE_RESULT,
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_SOURCE_PICK_RESULT,
    EVENT_TARGET_PLACE_RESULT,
    EVENT_WORKING_BIN_SCAN,
    NG_REASON_LOCAL_SORTING_NG,
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
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext
    from src.workline_runtime.runtime_intent import RuntimeIntent

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
    EVENT_SESSION_COMPLETE_REQUESTED: ROLE_SORTING_WORKSTATION,
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


def _ng_reason(canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        maps_from=(canonical_code,),
    )


class SmtSortingInboundPlugin(WorklinePlugin):
    """SMT 分拣入库插件。

    manifest 声明角色/事件/命令合同，具体 P0 业务编排委托给 flow service。
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
        ng_reason_catalog=(_ng_reason(NG_REASON_LOCAL_SORTING_NG, "本地分拣 NG"),),
    )

    def __init__(self, flow_service: SmtSortingInboundFlowService | None = None) -> None:
        self._flow_service = flow_service or SmtSortingInboundFlowService()

    @on_command(COMMAND_SOURCE_PICK, result="SUCCESS")
    async def handle_source_pick_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """源端机械臂取盘成功后，触发源格出账和扫码平台占用。"""

        return await self._flow_service.handle_source_pick_success(ctx, inbox)

    @on_event(EVENT_WORKING_BIN_SCAN)
    async def handle_working_bin_scan(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """扫码平台完成物料识别后，分配目标料格。"""

        return await self._flow_service.handle_working_bin_scan(ctx, inbox)

    @on_command(COMMAND_TARGET_PLACE, result="SUCCESS")
    async def handle_target_place_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """目标机械臂放盘成功后，触发目标格入账。"""

        return await self._flow_service.handle_target_place_success(ctx, inbox)

    @on_command(COMMAND_TARGET_PLACE, result="FAILED")
    async def handle_target_place_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """目标机械臂放盘失败后，保留证据并停止自动流转。"""

        return await self._flow_service.handle_target_place_failed(ctx, inbox)

    @on_command(COMMAND_NG_PLACE, result="SUCCESS")
    async def handle_ng_place_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """NG 机械臂放置成功后，关闭本地 NG 物料。"""

        return await self._flow_service.handle_ng_place_success(ctx, inbox)

    @on_command(COMMAND_NG_PLACE, result="FAILED")
    async def handle_ng_place_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """NG 机械臂放置失败后，阻断自动流转。"""

        return await self._flow_service.handle_ng_place_failed(ctx, inbox)

    @on_event(EVENT_SESSION_COMPLETE_REQUESTED)
    async def handle_session_complete_requested(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """人工/工作站请求完成 Session 前，检查本地闭环状态。"""

        return await self._flow_service.handle_session_complete_requested(ctx, inbox)


__all__ = [
    "COMMAND_TARGET_ROLES",
    "EVENT_SOURCE_ROLES",
    "SmtSortingInboundPlugin",
    "classify_sorting_inbound_result",
    "resolve_sorting_inbound_business_key",
]
