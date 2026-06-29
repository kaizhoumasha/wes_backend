"""SMT 分拣入库 WorkLine 插件 P0 manifest。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.app.workline.domain.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.app.workline.domain.ng_reason import NgReasonDefinition, NgReasonSource
from src.app.workline.domain.plugin_manifest import WorklinePluginManifest
from src.app.workline.plugins.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_SOURCE_PICK_REQUESTED,
    EVENT_WORKING_BIN_SCAN,
    NG_REASON_LOCAL_SORTING_NG,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.context import SortingInboundContext
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.workline.models import WorklineInbox
    from src.app.workline.plugins.plugin_context import PluginContext

POSITION_SOURCE_STATION_A = "SOURCE_STATION_A"
POSITION_SOURCE_STATION_B = "SOURCE_STATION_B"
POSITION_TARGET_STATION = "TARGET_STATION"


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
    """返回插件拥有的业务分类；普通成功/失败交给通用分类器。"""

    data = _payload_data(payload_json)
    reason_code = _non_empty_str(data.get("reason_code")) or _non_empty_str(payload_json.get("reason_code"))
    if reason_code == NG_REASON_LOCAL_SORTING_NG:
        return "business_decision"
    return None


def _ng_reason(canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        maps_from=(canonical_code,),
    )


def _sorting_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    return (_ng_reason(NG_REASON_LOCAL_SORTING_NG, "本地分拣 NG"),)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else {}


class SmtSortingInboundPlugin(WorklinePlugin):
    """SMT 分拣入库插件。

    manifest 声明角色/事件/命令合同，具体 P0 业务编排委托给 flow service。
    """

    plugin_key = SMT_SORTING_INBOUND_PLUGIN_KEY
    contract_version = SMT_SORTING_INBOUND_CONTRACT_VERSION

    manifest = WorklinePluginManifest.from_yaml_file(Path(__file__).with_name("manifest.yaml"))

    def __init__(self, flow_service: SmtSortingInboundFlowService | None = None) -> None:
        self._flow_service = flow_service or SmtSortingInboundFlowService()

    def resolve_business_key(self, payload_json: dict[str, Any]) -> str | None:
        return resolve_sorting_inbound_business_key(payload_json)

    def classify_result(self, payload_json: dict[str, Any]) -> str | None:
        return classify_sorting_inbound_result(payload_json)

    def get_context_model(self) -> type[SortingInboundContext]:
        return SortingInboundContext

    def list_ng_reasons(self) -> tuple[NgReasonDefinition, ...]:
        return _sorting_ng_reasons()

    def resolve_material_identity(self, input_value: MaterialIdentityInput) -> MaterialIdentity:
        source_payload = dict(cast("Mapping[str, Any]", input_value.source_payload or {}))
        command_payload = dict(cast("Mapping[str, Any]", input_value.command_payload or {}))
        session_context = _dict_or_empty(input_value.session_context)
        sorting_context = _dict_or_empty(session_context.get("sorting"))
        current_material = _dict_or_empty(sorting_context.get("current_material"))
        business_key = (
            self.resolve_business_key(source_payload)
            or self.resolve_business_key(command_payload)
            or _non_empty_str(current_material.get("material_identity_key"))
            or _non_empty_str(current_material.get("pkg_code"))
        )
        if business_key is None:
            return MaterialIdentity(
                resolution_status=MaterialIdentityResolutionStatus.MISSING,
                raw_evidence_hash=material_identity_input_to_hash(input_value),
            )
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
            idempotency_key=business_key,
            business_key=business_key,
            display={key: value for key, value in current_material.items() if value is not None},
            raw_evidence_hash=material_identity_input_to_hash(input_value),
        )

    @on_event(EVENT_SOURCE_PICK_REQUESTED)
    async def handle_source_pick_requested(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """内部 handoff 事件请求源端首盘取盘，只返回 command intent。"""

        return await self._flow_service.handle_source_pick_requested(ctx, inbox)

    @on_command(COMMAND_SOURCE_PICK, result="SUCCESS")
    async def handle_source_pick_success(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """源端机械臂取盘成功后，触发源格出账和扫码平台占用。"""

        return await self._flow_service.handle_source_pick_success(ctx, inbox)

    @on_command(COMMAND_SOURCE_PICK, result="FAILED")
    async def handle_source_pick_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """源端机械臂取盘失败后，阻断自动流转并保留失败证据。"""

        return await self._flow_service.handle_source_pick_failed(ctx, inbox)

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
        """目标机械臂完成 NG 放置后，关闭本地 NG 物料。"""

        return await self._flow_service.handle_ng_place_success(ctx, inbox)

    @on_command(COMMAND_NG_PLACE, result="FAILED")
    async def handle_ng_place_failed(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """目标机械臂 NG 放置失败后，阻断自动流转。"""

        return await self._flow_service.handle_ng_place_failed(ctx, inbox)


__all__ = [
    "SmtSortingInboundPlugin",
    "classify_sorting_inbound_result",
    "resolve_sorting_inbound_business_key",
]
