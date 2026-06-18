"""SMT 分拣入库插件 manifest 合同测试。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.app.resource.models import RackKind
from src.workline_plugin_registry import get_workline_plugin_definition
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
    PHASE_WAITING_SCAN,
    PHASE_WAITING_SOURCE_PICK,
    PHASE_WAITING_TARGET_BIN_SWITCH,
    PHASE_WAITING_TARGET_PLACE,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.plugin_manifest import EventCategory, FlowEdgeType, NodeRefKind
from src.workline_runtime.plugin_sdk import normalize_inbox_input
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.topology import WorklineTopologyView, validate_topology_manifest

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


def test_smt_sorting_inbound_plugin_is_registered() -> None:
    definition = get_workline_plugin_definition(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert definition is not None
    assert definition.plugin_class is SmtSortingInboundPlugin
    assert definition.manifest is SmtSortingInboundPlugin.manifest


def _command_role_map(manifest) -> dict[str, tuple[str, ...]]:
    return {command.command: (command.target_device_role,) for command in manifest.commands}


def _command_by_name(manifest):
    return {command.command: command for command in manifest.commands}


def _event_role_map(manifest) -> dict[str, tuple[str, ...]]:
    return {event.event: event.source_device_roles for event in manifest.events}


def _event_category_map(manifest) -> dict[str, EventCategory]:
    return {event.event: event.category for event in manifest.events}


def _topology_device(
    device_id: int,
    *,
    code: str,
    role: str,
    supports_event_types: list[str] | None = None,
    supports_command_types: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id,
        device_code=code,
        device_role=role,
        role_index=device_id,
        sort_order=device_id,
        upstream_device_id=None,
        capabilities_json={
            "supports_event_types": supports_event_types or [],
            "supports_command_types": supports_command_types or [],
        },
    )


def test_smt_sorting_inbound_manifest_declares_required_roles() -> None:
    manifest = SmtSortingInboundPlugin.manifest
    device_roles = {requirement.role for requirement in manifest.devices}

    assert device_roles == {
        ROLE_SORTING_SOURCE_ARM,
        ROLE_SORTING_TARGET_ARM,
        ROLE_SORTING_SCAN_PLATFORM,
        ROLE_SORTING_WORKSTATION,
    }
    assert "SORTING_NG_STATION" not in device_roles
    assert all(requirement.min_count == 1 for requirement in manifest.devices)


def test_smt_sorting_inbound_manifest_declares_command_and_event_roles() -> None:
    manifest = SmtSortingInboundPlugin.manifest
    command_roles = _command_role_map(manifest)
    command_by_name = _command_by_name(manifest)
    event_roles = _event_role_map(manifest)
    event_categories = _event_category_map(manifest)

    assert command_roles == {
        COMMAND_SOURCE_PICK: (ROLE_SORTING_SOURCE_ARM,),
        COMMAND_TARGET_PLACE: (ROLE_SORTING_TARGET_ARM,),
        COMMAND_NG_PLACE: (ROLE_SORTING_TARGET_ARM,),
    }
    assert event_roles[EVENT_WORKING_BIN_SCAN] == (ROLE_SORTING_SCAN_PLATFORM,)
    assert event_roles[EVENT_SESSION_COMPLETE_REQUESTED] == (ROLE_SORTING_WORKSTATION,)
    assert event_categories[EVENT_WORKING_BIN_SCAN] == EventCategory.ENTRY_DEVICE
    assert event_categories[EVENT_SESSION_COMPLETE_REQUESTED] == EventCategory.ENTRY_DEVICE
    assert EVENT_SOURCE_PICK_REQUESTED not in event_roles
    assert event_roles[EVENT_SOURCE_PICK_RESULT] == (ROLE_SORTING_SOURCE_ARM,)
    assert event_roles[EVENT_TARGET_PLACE_RESULT] == (ROLE_SORTING_TARGET_ARM,)
    assert event_roles[EVENT_NG_PLACE_RESULT] == (ROLE_SORTING_TARGET_ARM,)
    assert event_categories[EVENT_SOURCE_PICK_RESULT] == EventCategory.COMMAND_RESULT
    assert event_categories[EVENT_TARGET_PLACE_RESULT] == EventCategory.COMMAND_RESULT
    assert event_categories[EVENT_NG_PLACE_RESULT] == EventCategory.COMMAND_RESULT
    assert command_by_name[COMMAND_NG_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert set(command_by_name[COMMAND_TARGET_PLACE].__dataclass_fields__) == {"command", "target_device_role"}


def test_smt_sorting_inbound_manifest_uses_only_managed_rack_positions_and_resource_boundaries() -> None:
    manifest = SmtSortingInboundPlugin.manifest
    rack_position_codes = {rack_position.code for rack_position in manifest.rack_positions}
    business_demand_types = {boundary.business_demand_type for boundary in manifest.resource_boundaries}

    assert rack_position_codes == {"SOURCE_STATION_A", "SOURCE_STATION_B", "TARGET_STATION"}
    assert "NG_STATION" not in rack_position_codes
    assert "WORKSTATION" not in rack_position_codes
    assert business_demand_types == {"SORTING_INBOUND_SOURCE", "SORTING_INBOUND_TARGET"}
    assert "SORTING_INBOUND_NG" not in business_demand_types
    assert "SORTING_INBOUND_WORK" not in business_demand_types


def test_smt_sorting_inbound_topology_edges_follow_physical_process() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert {
        (edge.from_node.kind, edge.from_node.ref, edge.to_node.kind, edge.to_node.ref, edge.type)
        for edge in manifest.topology.flow_edges
    } == {
        (
            NodeRefKind.RACK_POSITION,
            "SOURCE_STATION_A",
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_SOURCE_ARM,
            FlowEdgeType.OPERATION,
        ),
        (
            NodeRefKind.RACK_POSITION,
            "SOURCE_STATION_B",
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_SOURCE_ARM,
            FlowEdgeType.OPERATION,
        ),
        (
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_SOURCE_ARM,
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_SCAN_PLATFORM,
            FlowEdgeType.OPERATION,
        ),
        (
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_SCAN_PLATFORM,
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_TARGET_ARM,
            FlowEdgeType.OPERATION,
        ),
        (
            NodeRefKind.DEVICE_ROLE,
            ROLE_SORTING_TARGET_ARM,
            NodeRefKind.RACK_POSITION,
            "TARGET_STATION",
            FlowEdgeType.OPERATION,
        ),
    }


def test_smt_sorting_inbound_real_manifest_validates_seed_like_device_capabilities() -> None:
    workstation_supported_events = [EVENT_SESSION_COMPLETE_REQUESTED]
    topology = WorklineTopologyView.from_devices(
        [
            _topology_device(
                1,
                code="SORT-SOURCE-ARM",
                role=ROLE_SORTING_SOURCE_ARM,
                supports_command_types=[COMMAND_SOURCE_PICK],
            ),
            _topology_device(
                2,
                code="SORT-TARGET-ARM",
                role=ROLE_SORTING_TARGET_ARM,
                supports_command_types=[COMMAND_TARGET_PLACE, COMMAND_NG_PLACE],
            ),
            _topology_device(
                3,
                code="SORT-SCAN-PLATFORM",
                role=ROLE_SORTING_SCAN_PLATFORM,
                supports_event_types=[EVENT_WORKING_BIN_SCAN],
            ),
            _topology_device(
                5,
                code="SORT-WORKSTATION",
                role=ROLE_SORTING_WORKSTATION,
                supports_event_types=workstation_supported_events,
            ),
        ]
    )

    assert EVENT_SOURCE_PICK_REQUESTED not in workstation_supported_events
    validate_topology_manifest(SmtSortingInboundPlugin.manifest, topology)


def test_smt_sorter_has_only_source_and_target_arm_roles_for_business_commands() -> None:
    manifest = SmtSortingInboundPlugin.manifest
    command_roles_by_name = _command_role_map(manifest)
    command_roles = {role for roles in command_roles_by_name.values() for role in roles}
    required_roles = {requirement.role for requirement in manifest.devices}
    manifest_surface = {
        "declared_roles": sorted(required_roles),
        "command_roles": {command: sorted(roles) for command, roles in command_roles_by_name.items()},
        "commands": sorted(command.command for command in manifest.commands),
    }

    assert command_roles == {ROLE_SORTING_SOURCE_ARM, ROLE_SORTING_TARGET_ARM}
    assert command_roles_by_name[COMMAND_NG_PLACE] == (ROLE_SORTING_TARGET_ARM,)
    assert "NG_ARM" not in command_roles
    assert "NG_ARM" not in required_roles
    assert "NG_ARM" not in repr(manifest_surface)


def test_ng_place_uses_target_arm_role() -> None:
    manifest = SmtSortingInboundPlugin.manifest

    assert _command_role_map(manifest)[COMMAND_NG_PLACE] == (ROLE_SORTING_TARGET_ARM,)


def test_smt_sorting_inbound_manifest_keeps_platform_start_out_of_business_events() -> None:
    manifest = SmtSortingInboundPlugin.manifest
    event_names = {event.event for event in manifest.events}

    assert "WORKLINE_START_REQUESTED" not in event_names


def test_smt_sorting_inbound_plugin_does_not_hard_code_device_codes() -> None:
    source = inspect.getsource(SmtSortingInboundPlugin)

    assert "ARM01" not in source
    assert "ARM02" not in source


def test_smt_sorting_inbound_classifier_leaves_success_and_failed_to_generic_classifier() -> None:
    success_inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-success",
        payload_json={
            "command_code": "CMD-SUCCESS",
            "device_code": "SORT-SOURCE-ARM",
            "task_type": COMMAND_SOURCE_PICK,
            "result": "SUCCESS",
            "data": {},
        },
    )
    failed_inbox = SimpleNamespace(
        kind=SimpleNamespace(value="COMMAND_RESULT"),
        trace_id="trace-failed",
        payload_json={
            "command_code": "CMD-FAILED",
            "device_code": "SORT-SOURCE-ARM",
            "task_type": COMMAND_SOURCE_PICK,
            "result": "FAILED",
            "error_detail": {"error_code": "ARM_JAM", "error_message": "机械臂卡料"},
            "data": {},
        },
    )

    success = normalize_inbox_input(success_inbox, plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY)
    failed = normalize_inbox_input(failed_inbox, plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert success.result_classification is None
    assert failed.result_classification == "hardware_failure"


class FakeStationLeaseStatusProvider:
    def __init__(self, *, available: bool = True, reason_code: str | None = None) -> None:
        self.available = available
        self.reason_code = reason_code
        self.active_rack_code = None
        self.active_session_id = None
        self.active_dispatch_key = None
        self.calls: list[tuple[str, bool, RackKind | None]] = []

    async def station_lease_status(
        self,
        position_code: str,
        *,
        allow_active_rack_bound: bool = False,
        rack_kind: RackKind | None = None,
    ) -> FakeStationLeaseStatusProvider:
        self.calls.append((position_code, allow_active_rack_bound, rack_kind))
        return self


class FailingStationLeaseStatusProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, RackKind | None]] = []

    async def station_lease_status(
        self,
        position_code: str,
        *,
        allow_active_rack_bound: bool = False,
        rack_kind: RackKind | None = None,
    ) -> None:
        self.calls.append((position_code, allow_active_rack_bound, rack_kind))
        raise ValueError("workline rack position not found: WL-SMT-SORTING-INBOUND-TEST/TARGET_STATION")


def _ctx(
    session_context: dict[str, Any] | None = None,
    *,
    services: SimpleNamespace | None = None,
) -> PluginContext:
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
            services=services or SimpleNamespace(station_lease_status_provider=FakeStationLeaseStatusProvider()),
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


def _source_pick_requested_inbox(data: dict[str, Any] | None = None) -> WorklineInbox:
    payload_data = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 22,
        "claim_attempt_no": 2,
        "rack_release_id": "release-001",
        "single_layer_rack_code": "RACK-001",
        "bin_code": "SRC-BIN-01",
        "bin_cell_index": 3,
        "bin_cell_code": "A03",
        "material_identity_key": "mid:pkg-001",
        "pkg_code": "PKG-001",
        "reel_thickness_mm": "7.125",
        "route_evidence": {"selected_workline_code": "SMT-SORT-01"},
    }
    payload_data.update(data or {})
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2101,
            kind="INTERNAL_EVENT",
            payload_json={
                "message_type": "INTERNAL_EVENT",
                "event_type": EVENT_SOURCE_PICK_REQUESTED,
                "canonical_event_type": EVENT_SOURCE_PICK_REQUESTED,
                "event_id": "smt-inbound-handoff-source-item:22:claim:2",
                "causation_id": "handoff-source-item:22",
                "trace_id": "trace-handoff-1",
                "data": payload_data,
            },
        ),
    )


def _working_bin_scan_inbox(data: dict[str, Any] | None = None) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2002,
            kind="DEVICE_EVENT",
            payload_json={
                "event_id": "SCAN-EVENT-001",
                "device_code": "SORT-SCAN-PLATFORM",
                "event_type": EVENT_WORKING_BIN_SCAN,
                "data": {
                    "material_identity_key": "mid:pkg-001",
                    "pkg_code": "PKG-001",
                    "reel_thickness": "7.125",
                    **(data or {}),
                },
            },
        ),
    )


def _session_complete_inbox() -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2005,
            kind="DEVICE_EVENT",
            payload_json={
                "event_id": "COMPLETE-EVENT-001",
                "device_code": "SORT-WORKSTATION",
                "event_type": EVENT_SESSION_COMPLETE_REQUESTED,
                "data": {},
            },
        ),
    )


def _target_place_result_inbox(
    *,
    result: str = "SUCCESS",
    data: dict[str, Any] | None = None,
) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2003,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-TARGET-PLACE-001",
                "device_code": "SORT-TARGET-ARM",
                "task_type": COMMAND_TARGET_PLACE,
                "result": result,
                "data": data or {},
            },
        ),
    )


def _ng_place_result_inbox(
    *,
    result: str = "SUCCESS",
    data: dict[str, Any] | None = None,
) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2004,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-NG-PLACE-001",
                "device_code": "SORT-TARGET-ARM",
                "task_type": COMMAND_NG_PLACE,
                "result": result,
                "data": data or {},
            },
        ),
    )


def _sorting_context_with_current_material() -> dict[str, Any]:
    return {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "OCCUPIED"},
            "active_target_bin_code": "TGT-BIN-01",
            "business_phase": PHASE_WAITING_SCAN,
            "current_material": {
                "source_bin_code": "SRC-BIN-01",
                "source_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "reel_thickness_mm": "7.125",
            },
        }
    }


def _sorting_context_with_pending_target() -> dict[str, Any]:
    context = _sorting_context_with_current_material()
    sorting = context["sorting"]
    sorting["business_phase"] = PHASE_WAITING_TARGET_PLACE
    sorting["current_material"]["pkg_code"] = "PKG-001"
    sorting["pending_target_placement"] = {
        "target_bin_code": "TGT-BIN-01",
        "target_cell_code": "B02",
        "material_identity_key": "mid:pkg-001",
        "reel_thickness_mm": "7.125",
        "allocation_snapshot_version": "snap-target-001",
        "capacity_evidence": {"remaining_depth_mm": "30.500"},
    }
    return context


def _sorting_context_with_ng_current_material() -> dict[str, Any]:
    context = _sorting_context_with_current_material()
    sorting = context["sorting"]
    sorting["business_phase"] = "WAITING_NG_PLACE"
    sorting["current_material"]["pkg_code"] = "PKG-001"
    sorting["current_material"]["ng_status"] = "MOVING_TO_NG"
    sorting["current_material"]["actual_material_identity_key"] = "mid:actual-other"
    return context


class RecordingAllocationPolicy:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def allocate(
        self,
        *,
        active_snapshot: dict[str, Any],
        material_identity_key: str,
        reel_thickness_mm: Any,
    ) -> Any:
        self.calls.append(
            {
                "active_snapshot": active_snapshot,
                "material_identity_key": material_identity_key,
                "reel_thickness_mm": reel_thickness_mm,
            }
        )
        return self.result


class FakeActiveRackSnapshotProvider:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.calls: list[dict[str, Any] | None] = []

    async def active_bin_rack(self, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(context)
        return self.snapshot


def _allocated_result() -> SimpleNamespace:
    return SimpleNamespace(
        kind="ALLOCATED",
        target_bin_code="TGT-BIN-01",
        target_cell_index="B02",
        allocation_snapshot_version="snap-target-001",
        reason_code=None,
        message=None,
        capacity_evidence={
            "selection_reason": "compatible-material",
            "remaining_depth_mm": "30.500",
            "projected_used_depth_mm": "17.125",
        },
    )


def _rejected_result(reason_code: str) -> SimpleNamespace:
    return SimpleNamespace(
        kind="REJECTED",
        target_bin_code="TGT-BIN-01",
        target_cell_index="B02",
        allocation_snapshot_version="snap-target-001",
        reason_code=reason_code,
        message=f"{reason_code} message",
        capacity_evidence={"reason_code": reason_code},
    )


def _plugin_with_policy_and_snapshot(
    policy: RecordingAllocationPolicy, snapshot: dict[str, Any]
) -> SmtSortingInboundPlugin:
    return SmtSortingInboundPlugin(
        flow_service=SmtSortingInboundFlowService(
            allocation_policy=policy,
            active_snapshot_provider=FakeActiveRackSnapshotProvider(snapshot),
        )
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
async def test_source_pick_failure_enters_manual_suspend_instead_of_empty_intents() -> None:
    plugin = SmtSortingInboundPlugin()
    inbox = _source_pick_success_inbox({"error_code": "ARM_JAM", "error_message": "source arm jam"})
    inbox.payload_json["result"] = "FAILED"

    intents = await plugin.on_command_result(
        _ctx(),
        inbox,
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_SOURCE_PICK_FAILED"
    assert intents[0].message == "source arm jam"
    assert intents[0].payload_json["error_detail"]["error_code"] == "ARM_JAM"


@pytest.mark.asyncio
async def test_source_pick_requested_returns_source_pick_command_intent() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(_ctx(), _source_pick_requested_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.COMMAND]
    intent = intents[0]
    assert intent.action == COMMAND_SOURCE_PICK
    assert intent.device_role == ROLE_SORTING_SOURCE_ARM
    assert intent.payload_json == {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 22,
        "claim_attempt_no": 2,
        "source_pick_inbox_id": 2101,
        "source_pick_request_event_id": "smt-inbound-handoff-source-item:22:claim:2",
        "rack_release_id": "release-001",
        "single_layer_rack_code": "RACK-001",
        "bin_code": "SRC-BIN-01",
        "source_bin_code": "SRC-BIN-01",
        "bin_cell_index": 3,
        "bin_cell_code": "A03",
        "source_cell_code": "A03",
        "material_identity_key": "mid:pkg-001",
        "pkg_code": "PKG-001",
        "reel_thickness": "7.125",
        "reel_thickness_mm": "7.125",
        "route_evidence": {"selected_workline_code": "SMT-SORT-01"},
    }


@pytest.mark.asyncio
async def test_source_pick_requested_invalid_payload_returns_plugin_contract_block() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(_ctx(), _source_pick_requested_inbox({"handoff_source_item_id": None}))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "PLUGIN_CONTRACT_INVALID"


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


@pytest.mark.asyncio
async def test_working_bin_scan_uses_shared_policy_and_writes_pending_target_placement() -> None:
    snapshot = {"snapshot_version": "snap-target-001", "cells": []}
    policy = RecordingAllocationPolicy(_allocated_result())
    plugin = _plugin_with_policy_and_snapshot(policy, snapshot)
    lease_provider = FakeStationLeaseStatusProvider()

    intents = await plugin.on_device_event(
        _ctx(
            _sorting_context_with_current_material(),
            services=SimpleNamespace(station_lease_status_provider=lease_provider),
        ),
        _working_bin_scan_inbox(),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert lease_provider.calls == [("TARGET_STATION", True, RackKind.FIVE_LAYER)]
    assert policy.calls == [
        {
            "active_snapshot": snapshot,
            "material_identity_key": "mid:pkg-001",
            "reel_thickness_mm": "7.125",
        }
    ]
    assert len(plugin._flow_service._active_snapshot_provider.calls) == 1
    assert plugin._flow_service._active_snapshot_provider.calls[0]["station"] == {"position_code": "TARGET_STATION"}
    assert plugin._flow_service._active_snapshot_provider.calls[0]["target_station_code"] == "TARGET_STATION"
    sorting_patch = intents[0].context_patch["sorting"]
    assert sorting_patch["pending_target_placement"] == {
        "target_bin_code": "TGT-BIN-01",
        "target_cell_code": "B02",
        "material_identity_key": "mid:pkg-001",
        "reel_thickness_mm": "7.125",
        "allocation_snapshot_version": "snap-target-001",
        "capacity_evidence": {
            "selection_reason": "compatible-material",
            "remaining_depth_mm": "30.500",
            "projected_used_depth_mm": "17.125",
        },
    }
    assert sorting_patch["business_phase"] == PHASE_WAITING_TARGET_PLACE
    assert intents[1].action == COMMAND_TARGET_PLACE
    assert intents[1].device_role == ROLE_SORTING_TARGET_ARM
    assert intents[1].payload_json["target_bin_code"] == "TGT-BIN-01"
    assert intents[1].payload_json["target_cell_code"] == "B02"


@pytest.mark.asyncio
async def test_working_bin_scan_waits_when_target_station_lease_is_busy() -> None:
    policy = RecordingAllocationPolicy(_allocated_result())
    plugin = _plugin_with_policy_and_snapshot(policy, {"snapshot_version": "snap-target-001", "cells": []})
    lease_provider = FakeStationLeaseStatusProvider(available=False, reason_code="ACTIVE_DISPATCH_LEASE")

    intents = await plugin.on_device_event(
        _ctx(
            _sorting_context_with_current_material(),
            services=SimpleNamespace(station_lease_status_provider=lease_provider),
        ),
        _working_bin_scan_inbox(),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_WAIT]
    assert intents[0].reason_code == "SORTING_TARGET_STATION_LEASE_BUSY"
    assert intents[0].payload_json["resource_kind"] == "STATION"
    assert intents[0].payload_json["resource_key"] == "station:TARGET_STATION"
    assert intents[0].payload_json["position_code"] == "TARGET_STATION"
    assert lease_provider.calls == [("TARGET_STATION", True, RackKind.FIVE_LAYER)]
    assert policy.calls == []
    assert plugin._flow_service._active_snapshot_provider.calls == []


@pytest.mark.asyncio
async def test_working_bin_scan_blocks_when_target_station_lease_provider_missing() -> None:
    policy = RecordingAllocationPolicy(_allocated_result())
    plugin = SmtSortingInboundPlugin(flow_service=SmtSortingInboundFlowService(allocation_policy=policy))
    context = _sorting_context_with_current_material()
    embedded_snapshot = {"snapshot_version": "snap-target-embedded", "cells": []}
    context["sorting"]["active_target_bin"] = embedded_snapshot

    intents = await plugin.on_device_event(
        _ctx(context, services=SimpleNamespace()),
        _working_bin_scan_inbox(),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_TARGET_STATION_LEASE_UNKNOWN"
    assert policy.calls == []


@pytest.mark.asyncio
async def test_working_bin_scan_blocks_when_target_station_lease_config_invalid() -> None:
    policy = RecordingAllocationPolicy(_allocated_result())
    plugin = _plugin_with_policy_and_snapshot(policy, {"snapshot_version": "snap-target-001", "cells": []})
    lease_provider = FailingStationLeaseStatusProvider()

    intents = await plugin.on_device_event(
        _ctx(
            _sorting_context_with_current_material(),
            services=SimpleNamespace(station_lease_status_provider=lease_provider),
        ),
        _working_bin_scan_inbox(),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_TARGET_STATION_LEASE_UNKNOWN"
    assert intents[0].payload_json == {
        "position_code": "TARGET_STATION",
        "error": "workline rack position not found: WL-SMT-SORTING-INBOUND-TEST/TARGET_STATION",
    }
    assert lease_provider.calls == [("TARGET_STATION", True, RackKind.FIVE_LAYER)]
    assert policy.calls == []


@pytest.mark.asyncio
async def test_working_bin_scan_uses_actual_thickness_when_same_identity_reports_mismatch() -> None:
    policy = RecordingAllocationPolicy(_allocated_result())
    plugin = _plugin_with_policy_and_snapshot(policy, {"snapshot_version": "snap-target-001", "cells": []})

    intents = await plugin.on_device_event(
        _ctx(_sorting_context_with_current_material()),
        _working_bin_scan_inbox({"reel_thickness": "7.250"}),
    )

    assert policy.calls[0]["reel_thickness_mm"] == "7.250"
    sorting_patch = intents[0].context_patch["sorting"]
    assert sorting_patch["current_material"]["reel_thickness_mm"] == "7.250"
    assert sorting_patch["current_material"]["scan_evidence"]["expected_reel_thickness_mm"] == "7.125"
    assert sorting_patch["pending_target_placement"]["reel_thickness_mm"] == "7.250"


@pytest.mark.asyncio
async def test_working_bin_scan_no_capacity_waits_for_target_bin_switch_without_new_source_pick() -> None:
    policy = RecordingAllocationPolicy(_rejected_result("NO_CAPACITY"))
    plugin = _plugin_with_policy_and_snapshot(policy, {"snapshot_version": "snap-target-001", "cells": []})

    intents = await plugin.on_device_event(_ctx(_sorting_context_with_current_material()), _working_bin_scan_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    sorting_patch = intents[0].context_patch["sorting"]
    assert sorting_patch["business_phase"] == PHASE_WAITING_TARGET_BIN_SWITCH
    assert "pending_target_placement" not in sorting_patch
    assert sorting_patch["allocation_rejection"]["reason_code"] == "NO_CAPACITY"


@pytest.mark.asyncio
async def test_working_bin_scan_missing_or_invalid_thickness_blocks_automatic_placement() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(
        _ctx(_sorting_context_with_current_material()),
        _working_bin_scan_inbox({"reel_thickness": ""}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_REEL_THICKNESS_INVALID"


@pytest.mark.asyncio
async def test_working_bin_scan_projection_inconsistent_blocks_target_cell() -> None:
    policy = RecordingAllocationPolicy(_rejected_result("PROJECTION_INCONSISTENT"))
    plugin = _plugin_with_policy_and_snapshot(policy, {"snapshot_version": "snap-target-001", "cells": []})

    intents = await plugin.on_device_event(_ctx(_sorting_context_with_current_material()), _working_bin_scan_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_TARGET_CELL_RECONCILING"


@pytest.mark.asyncio
async def test_working_bin_scan_identity_mismatch_sends_reel_to_local_ng() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(
        _ctx(_sorting_context_with_current_material()),
        _working_bin_scan_inbox({"material_identity_key": "mid:actual-other"}),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    sorting_patch = intents[0].context_patch["sorting"]
    assert "pending_target_placement" not in sorting_patch
    assert sorting_patch["current_material"]["ng_status"] == "MOVING_TO_NG"
    assert sorting_patch["current_material"]["actual_material_identity_key"] == "mid:actual-other"
    assert intents[0].context_patch["scan_ng_reason_code"] == "LOCAL_SORTING_NG"
    assert intents[1].reason_code == "LOCAL_SORTING_NG"
    assert intents[2].action == COMMAND_NG_PLACE
    assert intents[2].device_role == ROLE_SORTING_TARGET_ARM
    assert "NG_ARM" not in {
        intents[2].device_role,
        *_command_role_map(SmtSortingInboundPlugin.manifest)[COMMAND_NG_PLACE],
    }


@pytest.mark.asyncio
async def test_target_place_success_requires_pending_target_placement() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_current_material()), _target_place_result_inbox()
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_PENDING_TARGET_MISSING"


@pytest.mark.asyncio
async def test_target_place_success_mounts_material_and_releases_scan_platform() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(_ctx(_sorting_context_with_pending_target()), _target_place_result_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.UPDATE_CONTEXT]
    mounted_intent = intents[0]
    assert mounted_intent.action == "MATERIAL_MOUNTED"
    assert mounted_intent.payload_json["bin_code"] == "TGT-BIN-01"
    assert mounted_intent.payload_json["bin_cell_index"] == "B02"
    assert mounted_intent.payload_json["material_identity_key"] == "mid:pkg-001"
    assert mounted_intent.payload_json["pkg_code"] == "PKG-001"
    assert mounted_intent.payload_json["reel_thickness"] == "7.125"

    sorting_patch = intents[1].context_patch["sorting"]
    assert "pending_target_placement" not in sorting_patch
    assert "current_material" not in sorting_patch
    assert sorting_patch["stations"]["scan_platform"] == "EMPTY"
    assert sorting_patch["business_phase"] == PHASE_WAITING_SOURCE_PICK


@pytest.mark.asyncio
async def test_target_place_success_preserves_handoff_source_pick_request_for_terminal_ledger() -> None:
    plugin = SmtSortingInboundPlugin()
    context = _sorting_context_with_pending_target()
    source_pick_request = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 22,
        "claim_attempt_no": 1,
        "event_id": "smt-inbound-handoff-source-item:22:claim:1",
        "target_workline_code": "SMT-SORT-01",
        "manifest_contract_version": "2026-06-01.p0",
        "source_rack_position_code": "SOURCE_STATION_A",
        "target_rack_position_code": "TARGET_STATION",
        "route_evidence": {},
    }
    context["sorting"]["source_pick_request"] = source_pick_request

    intents = await plugin.on_command_result(_ctx(context), _target_place_result_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.UPDATE_CONTEXT]
    sorting_patch = intents[1].context_patch["sorting"]
    assert sorting_patch["source_pick_request"] == source_pick_request
    assert "pending_target_placement" not in sorting_patch
    assert "current_material" not in sorting_patch


@pytest.mark.asyncio
async def test_target_place_failure_with_known_location_enters_manual_suspend() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_pending_target()),
        _target_place_result_inbox(
            result="FAILED",
            data={"target_bin_code": "TGT-BIN-01", "target_cell_code": "B02", "error_message": "gripper alarm"},
        ),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_TARGET_PLACE_FAILED"
    assert intents[0].payload_json["target_location_known"] is True


@pytest.mark.asyncio
async def test_target_place_failure_with_unknown_location_enters_reconciliation() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_pending_target()),
        _target_place_result_inbox(result="FAILED", data={"target_location_known": False}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_TARGET_PLACE_LOCATION_UNKNOWN"


@pytest.mark.asyncio
async def test_ng_place_success_closes_material_and_keeps_ng_return_context() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_ng_current_material()),
        _ng_place_result_inbox(data={"ng_location": "NG-01", "ng_reason_code": "LOCAL_SORTING_NG"}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    patch = intents[0].context_patch
    assert patch["scan_ng_reason_code"] == "LOCAL_SORTING_NG"
    assert patch["ng_reason"] == "LOCAL_SORTING_NG"
    assert "current_material" not in patch["sorting"]
    assert patch["sorting"]["stations"]["scan_platform"] == "EMPTY"
    assert "smt_inbound_handoff_terminal_result" not in patch


@pytest.mark.asyncio
async def test_ng_place_success_writes_terminal_marker_for_handoff_session() -> None:
    plugin = SmtSortingInboundPlugin()
    context = _sorting_context_with_ng_current_material()
    context["sorting"]["source_pick_request"] = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 22,
        "claim_attempt_no": 1,
        "event_id": "source-pick-requested:11:22:1",
        "target_workline_code": "SMT_SORTER_01",
        "manifest_contract_version": "v1",
        "source_rack_position_code": "SINGLE_LAYER_A",
        "target_rack_position_code": "TARGET_STATION",
        "route_evidence": {},
    }

    intents = await plugin.on_command_result(
        _ctx(context),
        _ng_place_result_inbox(data={"ng_location": "NG-01", "ng_reason_code": "LOCAL_SORTING_NG"}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    patch = intents[0].context_patch
    assert patch["smt_inbound_handoff_terminal_result"]["terminal_status"] == "SKIPPED"
    terminal_evidence = patch["smt_inbound_handoff_terminal_result"]["terminal_evidence"]
    assert terminal_evidence["ng_command_payload"]["data"]["ng_location"] == "NG-01"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{}, {"ng_location": "", "ng_location_code": None, "ng_reason_code": " "}])
async def test_ng_place_success_without_payload_evidence_blocks_terminal_marker(
    data: dict[str, Any],
) -> None:
    plugin = SmtSortingInboundPlugin()
    context = _sorting_context_with_ng_current_material()
    context["sorting"]["source_pick_request"] = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 22,
        "claim_attempt_no": 1,
        "event_id": "source-pick-requested:11:22:1",
        "target_workline_code": "SMT_SORTER_01",
        "manifest_contract_version": "v1",
        "source_rack_position_code": "SINGLE_LAYER_A",
        "target_rack_position_code": "TARGET_STATION",
        "route_evidence": {},
    }

    intents = await plugin.on_command_result(
        _ctx(context),
        _ng_place_result_inbox(data=data),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_NG_PLACE_EVIDENCE_MISSING"
    assert "smt_inbound_handoff_terminal_result" not in intents[0].context_patch


@pytest.mark.asyncio
async def test_ng_place_failure_with_known_location_enters_manual_suspend() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_ng_current_material()),
        _ng_place_result_inbox(result="FAILED", data={"ng_location_code": "NG-01", "error_message": "ng arm alarm"}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_NG_PLACE_FAILED"
    assert intents[0].payload_json["ng_location_known"] is True


@pytest.mark.asyncio
async def test_ng_place_failure_with_unknown_location_enters_reconciliation() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_command_result(
        _ctx(_sorting_context_with_ng_current_material()),
        _ng_place_result_inbox(result="FAILED", data={"ng_location_known": False}),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_NG_PLACE_LOCATION_UNKNOWN"


@pytest.mark.asyncio
async def test_session_completion_blocks_when_current_material_is_open() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(_ctx(_sorting_context_with_current_material()), _session_complete_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_CURRENT_MATERIAL_OPEN"


@pytest.mark.asyncio
async def test_session_completion_blocks_when_pending_target_exists() -> None:
    plugin = SmtSortingInboundPlugin()

    intents = await plugin.on_device_event(_ctx(_sorting_context_with_pending_target()), _session_complete_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "SORTING_PENDING_TARGET_OPEN"


@pytest.mark.asyncio
async def test_session_completion_allows_closed_target_or_local_ng_context() -> None:
    plugin = SmtSortingInboundPlugin()
    closed_context = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
            "business_phase": PHASE_WAITING_SOURCE_PICK,
        },
        "ng_reason": "LOCAL_SORTING_NG",
    }

    intents = await plugin.on_device_event(_ctx(closed_context), _session_complete_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.COMPLETE]
    assert intents[0].context_patch["sorting"]["business_phase"] == "COMPLETED"
