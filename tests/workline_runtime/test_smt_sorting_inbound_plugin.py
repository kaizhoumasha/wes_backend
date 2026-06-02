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
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_SOURCE_PICK_RESULT,
    EVENT_TARGET_PLACE_RESULT,
    EVENT_WORKING_BIN_SCAN,
    PHASE_WAITING_SCAN,
    PHASE_WAITING_SOURCE_PICK,
    PHASE_WAITING_TARGET_BIN_SWITCH,
    PHASE_WAITING_TARGET_PLACE,
    ROLE_SORTING_NG_ARM,
    ROLE_SORTING_NG_STATION,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService
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
        EVENT_SESSION_COMPLETE_REQUESTED: (ROLE_SORTING_WORKSTATION,),
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
                "device_code": "SORT-NG-ARM",
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

    intents = await plugin.on_device_event(_ctx(_sorting_context_with_current_material()), _working_bin_scan_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert policy.calls == [
        {
            "active_snapshot": snapshot,
            "material_identity_key": "mid:pkg-001",
            "reel_thickness_mm": "7.125",
        }
    ]
    assert len(plugin._flow_service._active_snapshot_provider.calls) == 1
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
    assert intents[2].device_role == ROLE_SORTING_NG_ARM


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
        _ctx(_sorting_context_with_ng_current_material()), _ng_place_result_inbox()
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    patch = intents[0].context_patch
    assert patch["scan_ng_reason_code"] == "LOCAL_SORTING_NG"
    assert patch["ng_reason"] == "LOCAL_SORTING_NG"
    assert "current_material" not in patch["sorting"]
    assert patch["sorting"]["stations"]["scan_platform"] == "EMPTY"


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
