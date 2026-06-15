"""SMT 分拣入库 WorkLine 插件 P0 manifest。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_NG_PLACE_RESULT,
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_SOURCE_PICK_REQUESTED,
    EVENT_SOURCE_PICK_RESULT,
    EVENT_TARGET_PLACE_RESULT,
    EVENT_WORKING_BIN_SCAN,
    NG_REASON_LOCAL_SORTING_NG,
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
from src.workline_runtime.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_base import WorklinePlugin, on_command, on_event
from src.workline_runtime.plugin_manifest import (
    CommandBinding,
    CommandResultBinding,
    DeviceRequirement,
    EventBinding,
    EventCategory,
    FlowEdge,
    FlowEdgeType,
    NodeRef,
    NodeRefKind,
    RackPosition,
    RackPositionArg,
    RackPositionArgRole,
    RackPositionArgSource,
    RackPositionArgSourceKind,
    RackPositionCarrierCapability,
    ResourceBoundary,
    TopologySpec,
    WorklinePluginManifest,
)

if TYPE_CHECKING:
    from src.app.workline.models import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext
    from src.workline_runtime.runtime_intent import RuntimeIntent

POSITION_SOURCE_STATION_A = "SOURCE_STATION_A"
POSITION_SOURCE_STATION_B = "SOURCE_STATION_B"
POSITION_TARGET_STATION = "TARGET_STATION"
POSITION_NG_STATION = "NG_STATION"
POSITION_WORKSTATION = "WORKSTATION"

COMMAND_RESULT_EVENTS = {
    COMMAND_SOURCE_PICK: EVENT_SOURCE_PICK_RESULT,
    COMMAND_TARGET_PLACE: EVENT_TARGET_PLACE_RESULT,
    COMMAND_NG_PLACE: EVENT_NG_PLACE_RESULT,
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


def _carrier(*rack_kinds: str, min_capacity: int = 0, max_capacity: int = 1) -> RackPositionCarrierCapability:
    return RackPositionCarrierCapability(
        allowed_rack_kinds=rack_kinds,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
    )


def _position(
    code: str,
    *,
    role: str,
    station_code: str,
    rack_kinds: tuple[str, ...],
    min_capacity: int = 0,
    max_capacity: int = 1,
) -> RackPosition:
    return RackPosition(
        code=code,
        role=role,
        station_code=station_code,
        carrier_capability=_carrier(*rack_kinds, min_capacity=min_capacity, max_capacity=max_capacity),
    )


def _node(kind: NodeRefKind, ref: str) -> NodeRef:
    return NodeRef(kind=kind, ref=ref)


def _command_result_bindings(command: str) -> tuple[CommandResultBinding, ...]:
    event = COMMAND_RESULT_EVENTS[command]
    return (
        CommandResultBinding(
            result="SUCCESS",
            event=event,
            category=EventCategory.COMMAND_RESULT,
            classification="success",
        ),
        CommandResultBinding(
            result="FAILED",
            event=event,
            category=EventCategory.COMMAND_RESULT,
            classification="hardware_failure",
            terminal=True,
        ),
    )


def _static_position_arg(name: str, *, role: RackPositionArgRole, rack_position_ref: str) -> RackPositionArg:
    return RackPositionArg(name=name, role=role, rack_position_ref=rack_position_ref)


def _position_arg_from_source(
    name: str,
    *,
    role: RackPositionArgRole,
    kind: RackPositionArgSourceKind,
    path: str,
    fallback_rack_position_ref: str,
) -> RackPositionArg:
    return RackPositionArg(
        name=name,
        role=role,
        source=RackPositionArgSource(kind=kind, path=path, fallback_rack_position_ref=fallback_rack_position_ref),
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

    manifest = WorklinePluginManifest(
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        devices=(
            DeviceRequirement(role=ROLE_SORTING_SOURCE_ARM, min_count=1, max_count=1),
            DeviceRequirement(role=ROLE_SORTING_TARGET_ARM, min_count=1, max_count=1),
            DeviceRequirement(role=ROLE_SORTING_SCAN_PLATFORM, min_count=1, max_count=1),
            DeviceRequirement(role=ROLE_SORTING_NG_STATION, min_count=1, max_count=1),
            DeviceRequirement(role=ROLE_SORTING_WORKSTATION, min_count=1, max_count=1),
        ),
        rack_positions=(
            _position(
                POSITION_SOURCE_STATION_A,
                role="SOURCE",
                station_code=POSITION_SOURCE_STATION_A,
                rack_kinds=("SINGLE_LAYER",),
                min_capacity=1,
            ),
            _position(
                POSITION_SOURCE_STATION_B,
                role="SOURCE",
                station_code=POSITION_SOURCE_STATION_B,
                rack_kinds=("SINGLE_LAYER",),
                min_capacity=1,
            ),
            _position(
                POSITION_TARGET_STATION,
                role="TARGET",
                station_code=POSITION_TARGET_STATION,
                rack_kinds=("FIVE_LAYER",),
                min_capacity=1,
            ),
            _position(
                POSITION_NG_STATION,
                role="NG",
                station_code=POSITION_NG_STATION,
                rack_kinds=("SINGLE_LAYER",),
            ),
            _position(
                POSITION_WORKSTATION,
                role="WORK",
                station_code=POSITION_WORKSTATION,
                rack_kinds=("SINGLE_LAYER", "FIVE_LAYER"),
            ),
        ),
        topology=TopologySpec(
            flow_edges=(
                FlowEdge(
                    from_node=_node(NodeRefKind.DEVICE_ROLE, ROLE_SORTING_SOURCE_ARM),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_SOURCE_STATION_A),
                    type=FlowEdgeType.OPERATION,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.RACK_POSITION, POSITION_SOURCE_STATION_A),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_WORKSTATION),
                    type=FlowEdgeType.MATERIAL_FLOW,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.DEVICE_ROLE, ROLE_SORTING_SOURCE_ARM),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_SOURCE_STATION_B),
                    type=FlowEdgeType.OPERATION,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.RACK_POSITION, POSITION_SOURCE_STATION_B),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_WORKSTATION),
                    type=FlowEdgeType.MATERIAL_FLOW,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.DEVICE_ROLE, ROLE_SORTING_SCAN_PLATFORM),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_WORKSTATION),
                    type=FlowEdgeType.OPERATION,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.RACK_POSITION, POSITION_WORKSTATION),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_TARGET_STATION),
                    type=FlowEdgeType.MATERIAL_FLOW,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.RACK_POSITION, POSITION_WORKSTATION),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_NG_STATION),
                    type=FlowEdgeType.MATERIAL_FLOW,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.DEVICE_ROLE, ROLE_SORTING_TARGET_ARM),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_TARGET_STATION),
                    type=FlowEdgeType.OPERATION,
                ),
                FlowEdge(
                    from_node=_node(NodeRefKind.DEVICE_ROLE, ROLE_SORTING_TARGET_ARM),
                    to_node=_node(NodeRefKind.RACK_POSITION, POSITION_NG_STATION),
                    type=FlowEdgeType.OPERATION,
                ),
            )
        ),
        commands=(
            CommandBinding(
                command=COMMAND_SOURCE_PICK,
                target_device_role=ROLE_SORTING_SOURCE_ARM,
                rack_position_args=(
                    _position_arg_from_source(
                        "source_position_code",
                        role=RackPositionArgRole.SOURCE,
                        kind=RackPositionArgSourceKind.EVENT_PAYLOAD,
                        path="data.source_position_code",
                        fallback_rack_position_ref=POSITION_SOURCE_STATION_A,
                    ),
                    _static_position_arg(
                        "target_position_code",
                        role=RackPositionArgRole.TARGET,
                        rack_position_ref=POSITION_WORKSTATION,
                    ),
                ),
                result_bindings=_command_result_bindings(COMMAND_SOURCE_PICK),
            ),
            CommandBinding(
                command=COMMAND_TARGET_PLACE,
                target_device_role=ROLE_SORTING_TARGET_ARM,
                rack_position_args=(
                    _static_position_arg(
                        "source_position_code",
                        role=RackPositionArgRole.SOURCE,
                        rack_position_ref=POSITION_WORKSTATION,
                    ),
                    _position_arg_from_source(
                        "target_position_code",
                        role=RackPositionArgRole.TARGET,
                        kind=RackPositionArgSourceKind.SESSION_CONTEXT,
                        path="sorting.pending_target_placement.target_bin_code",
                        fallback_rack_position_ref=POSITION_TARGET_STATION,
                    ),
                ),
                result_bindings=_command_result_bindings(COMMAND_TARGET_PLACE),
            ),
            CommandBinding(
                command=COMMAND_NG_PLACE,
                target_device_role=ROLE_SORTING_TARGET_ARM,
                rack_position_args=(
                    _static_position_arg(
                        "source_position_code",
                        role=RackPositionArgRole.SOURCE,
                        rack_position_ref=POSITION_WORKSTATION,
                    ),
                    _static_position_arg(
                        "target_position_code",
                        role=RackPositionArgRole.TARGET,
                        rack_position_ref=POSITION_NG_STATION,
                    ),
                ),
                result_bindings=_command_result_bindings(COMMAND_NG_PLACE),
            ),
        ),
        events=(
            EventBinding(
                event=EVENT_WORKING_BIN_SCAN,
                source_device_roles=(ROLE_SORTING_SCAN_PLATFORM,),
                category=EventCategory.ENTRY_DEVICE,
            ),
            EventBinding(
                event=EVENT_SESSION_COMPLETE_REQUESTED,
                source_device_roles=(ROLE_SORTING_WORKSTATION,),
                category=EventCategory.ENTRY_DEVICE,
            ),
        ),
        resource_boundaries=(
            ResourceBoundary(
                rack_position_code=POSITION_SOURCE_STATION_A,
                rack_kind="SINGLE_LAYER",
                business_demand_type="SORTING_INBOUND_SOURCE",
                wms_operation_type="SUPPLY_SINGLE_LAYER_RACK",
                snapshot_kind="ACTIVE_SOURCE_BIN_RACK",
                lease_scope="STATION",
            ),
            ResourceBoundary(
                rack_position_code=POSITION_SOURCE_STATION_B,
                rack_kind="SINGLE_LAYER",
                business_demand_type="SORTING_INBOUND_SOURCE",
                wms_operation_type="SUPPLY_SINGLE_LAYER_RACK",
                snapshot_kind="ACTIVE_SOURCE_BIN_RACK",
                lease_scope="STATION",
            ),
            ResourceBoundary(
                rack_position_code=POSITION_TARGET_STATION,
                rack_kind="FIVE_LAYER",
                business_demand_type="SORTING_INBOUND_TARGET",
                wms_operation_type="ALLOCATE_SORTING_TARGET_BIN",
                snapshot_kind="ACTIVE_TARGET_BIN_RACK",
                lease_scope="STATION",
            ),
            ResourceBoundary(
                rack_position_code=POSITION_NG_STATION,
                rack_kind="SINGLE_LAYER",
                business_demand_type="SORTING_INBOUND_NG",
                wms_operation_type="PLACE_LOCAL_NG",
                snapshot_kind="ACTIVE_NG_RACK",
                lease_scope="STATION",
            ),
            ResourceBoundary(
                rack_position_code=POSITION_WORKSTATION,
                rack_kind="SINGLE_LAYER",
                business_demand_type="SORTING_INBOUND_WORK",
                wms_operation_type="SCAN_AND_CLASSIFY_MATERIAL",
                snapshot_kind="ACTIVE_WORK_MATERIAL",
                lease_scope="STATION",
            ),
            ResourceBoundary(
                rack_position_code=POSITION_WORKSTATION,
                rack_kind="FIVE_LAYER",
                business_demand_type="SORTING_INBOUND_WORK",
                wms_operation_type="SCAN_AND_CLASSIFY_MATERIAL",
                snapshot_kind="ACTIVE_WORK_MATERIAL",
                lease_scope="STATION",
            ),
        ),
    )

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

    @on_event(EVENT_SESSION_COMPLETE_REQUESTED)
    async def handle_session_complete_requested(self, ctx: PluginContext, inbox: WorklineInbox) -> list[RuntimeIntent]:
        """人工/工作站请求完成 Session 前，检查本地闭环状态。"""

        return await self._flow_service.handle_session_complete_requested(ctx, inbox)


__all__ = [
    "SmtSortingInboundPlugin",
    "classify_sorting_inbound_result",
    "resolve_sorting_inbound_business_key",
]
