"""SMT 分拣入库 P0 插件 intent smoke。"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_WORKING_BIN_SCAN,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.runtime_intent import RuntimeIntentKind

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext


class _FakeStationLeaseStatusProvider:
    available = True

    async def station_lease_status(
        self,
        _position_code: str,
        *,
        rack_kind: object | None = None,
        allow_active_rack_bound: bool = False,
    ) -> _FakeStationLeaseStatusProvider:
        return self


class _SessionActiveTargetSnapshotProvider:
    def __init__(self, session_context: dict[str, Any]) -> None:
        self._session_context = session_context

    async def active_bin_rack(self, *, context: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        sorting = self._session_context.get("sorting")
        active_target_bin = sorting.get("active_target_bin") if isinstance(sorting, dict) else None
        return dict(cast("Mapping[str, Any]", active_target_bin)) if isinstance(active_target_bin, Mapping) else None


def _ctx(session_context: dict[str, Any]) -> PluginContext:
    return cast(
        "PluginContext",
        SimpleNamespace(
            trace_id="trace-sorting-inbound-p0",
            config={},
            logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
            normalized_input=None,
            session=SimpleNamespace(id=3001, context_json=session_context),
            services=SimpleNamespace(
                active_rack_snapshot_provider=_SessionActiveTargetSnapshotProvider(session_context),
                station_lease_status_provider=_FakeStationLeaseStatusProvider(),
            ),
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


def _scan_inbox(data: dict[str, Any]) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=4002,
            kind="DEVICE_EVENT",
            payload_json={
                "event_id": "SCAN-EVENT-SMOKE",
                "device_code": "SORT-SCAN-PLATFORM",
                "event_type": EVENT_WORKING_BIN_SCAN,
                "data": data,
            },
        ),
    )


def _target_place_inbox() -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=4003,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-TARGET-PLACE-SMOKE",
                "device_code": "SORT-TARGET-ARM",
                "task_type": COMMAND_TARGET_PLACE,
                "result": "SUCCESS",
                "data": {},
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

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.CREATE_MATERIAL_UNIT,
        RuntimeIntentKind.UPDATE_CONTEXT,
    ]
    assert intents[0].action == "MATERIAL_UNMOUNTED"
    assert intents[1].payload_json["pkg_code"] == "PKG-001"
    assert intents[1].payload_json["status"] == "IN_TRANSIT"
    session_context = _apply_context(session_context, intents[2].context_patch)
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


@pytest.mark.asyncio
async def test_scan_smoke_allocates_pending_target_placement_from_active_snapshot() -> None:
    plugin = SmtSortingInboundPlugin()
    session_context: dict[str, Any] = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "OCCUPIED"},
            "business_phase": "WAITING_SCAN",
            "current_material": {
                "source_bin_code": "SRC-BIN-01",
                "source_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "reel_thickness_mm": "7.125",
            },
            "active_target_bin": {
                "snapshot_version": "snap-target-001",
                "cells": [
                    {
                        "bin_code": "TGT-BIN-01",
                        "bin_cell_index": "B02",
                        "status": "EMPTY",
                        "capacity_depth_mm": "30.500",
                        "used_depth_mm": "0",
                    }
                ],
            },
        }
    }

    intents = await plugin.on_device_event(
        _ctx(session_context),
        _scan_inbox(
            {
                "material_identity_key": "mid:pkg-001",
                "pkg_code": "PKG-001",
                "reel_thickness": "7.125",
            }
        ),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    session_context = _apply_context(session_context, intents[0].context_patch)
    assert session_context["sorting"]["pending_target_placement"]["target_bin_code"] == "TGT-BIN-01"
    assert session_context["sorting"]["pending_target_placement"]["target_cell_code"] == "B02"

    target_intents = await plugin.on_command_result(_ctx(session_context), _target_place_inbox())

    assert [intent.kind for intent in target_intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.COMPLETE,
    ]
    assert target_intents[0].action == "MATERIAL_MOUNTED"
    session_context = _apply_context(session_context, target_intents[1].context_patch)
    assert "current_material" not in session_context["sorting"]
    assert "pending_target_placement" not in session_context["sorting"]
