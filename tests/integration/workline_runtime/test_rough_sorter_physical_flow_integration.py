from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.app.resource.services.smt_rack_bin_scheduling_service import SmtRackBinSchedulingDecision
from src.app.wms_integration.models import QueryInventoryResponse, WmsInventoryItem
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_FORWARD,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_SCAN_COMPLETED,
    PHASE_COMPLETED,
    PHASE_MOVING_FORWARD,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.services import WorklineRuntimeServices

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.workline_runtime.plugin_context import PluginContext

# End-to-end smoke boundary:
# SCAN -> MEASURE -> WMS query -> PICK -> MOVE -> allocate bin -> PUT_TO_BIN.
# Fake WMS/rack services stay outside RuntimeIntent effects.
# This test asserts the intent contract shape.


class FakeWmsInventoryClient:
    async def query_inventory(self, _request: Any) -> QueryInventoryResponse:
        return QueryInventoryResponse(
            items=[
                WmsInventoryItem(
                    sku="HH-001",
                    lot_no="LOT-A",
                    total_qty=Decimal("1500"),
                    available_qty=Decimal("1500"),
                    reserved_qty=Decimal("0"),
                )
            ]
        )


class FakeBinAllocator:
    def plan_allocation(self, _barcode: str, *, context: Any | None = None) -> SmtRackBinSchedulingDecision:
        assert context is not None
        assert context["active_bin_rack"] == {"rack_id": "RACK-001"}
        return SmtRackBinSchedulingDecision(
            bin_location={
                "bin_id": "BIN-001",
                "bin_cell_index": "4",
                "bin_cell_location": "BIN-001-4",
                "material_identity_key": "MAT:HH-001:MFR-001:260528:LOT-A",
                "capacity_depth_mm": 10.0,
            }
        )


class FakeActiveRackSnapshotProvider:
    async def active_bin_rack(self, *, context: Any | None = None) -> dict[str, Any]:
        assert context is not None
        return {"rack_id": "RACK-001"}


def _ctx(session_context: dict[str, Any] | None = None) -> PluginContext:
    return cast(
        "PluginContext",
        SimpleNamespace(
            trace_id="trace-rough-sorter-smoke",
            config={},
            logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
            normalized_input=None,
            session=SimpleNamespace(id=321, context_json=session_context or {}),
            services=WorklineRuntimeServices(
                wms_inventory_client=FakeWmsInventoryClient(),
                bin_allocator=FakeBinAllocator(),
                active_rack_snapshot_provider=FakeActiveRackSnapshotProvider(),
            ),
        ),
    )


def _device_inbox(payload: dict[str, Any]) -> WorklineInbox:
    return cast("WorklineInbox", SimpleNamespace(id=1, kind="DEVICE_EVENT", payload_json=payload))


def _command_inbox(command_type: str, *, result: str = "SUCCESS", data: dict[str, Any] | None = None) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": f"CMD-{command_type}",
                "device_code": "RS-SMOKE-01",
                "task_type": command_type,
                "result": result,
                "data": data or {},
            },
        ),
    )


def _apply_context(session_context: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    session_context.update(patch)
    return session_context


@pytest.mark.asyncio
async def test_rough_sorter_physical_flow_smoke_reaches_material_mounted_completion() -> None:
    plugin = RoughSorterPlugin()
    session_context: dict[str, Any] = {}

    scan_intents = await plugin.on_device_event(
        _ctx(session_context),
        _device_inbox(
            {
                "device_code": "RS-SCAN-01",
                "event_type": EVENT_SCAN_COMPLETED,
                "data": {
                    "HHPN": "HH-001",
                    "MfrPN": "MFR-001",
                    "Qty": "1500",
                    "DateCode": "260528",
                    "LotCode": "LOT-A",
                    "PkgID": "PKG-ROUGH-001",
                },
            }
        ),
    )
    assert [intent.kind for intent in scan_intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert scan_intents[1].action == ACTION_MEASUREMENT_REEL
    session_context = _apply_context(session_context, scan_intents[0].context_patch)

    measurement_intents = await plugin.on_command_result(
        _ctx(session_context),
        _command_inbox(
            ACTION_MEASUREMENT_REEL,
            data={"reel_diameter": "178.0", "reel_thickness": "15.0"},
        ),
    )
    assert [intent.kind for intent in measurement_intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert measurement_intents[1].action == ACTION_PICK_AND_PUT
    session_context = _apply_context(session_context, measurement_intents[0].context_patch)
    assert session_context["phase"] == PHASE_PICK_TO_PIPELINE

    pick_intents = await plugin.on_command_result(_ctx(session_context), _command_inbox(ACTION_PICK_AND_PUT))
    assert [intent.kind for intent in pick_intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert pick_intents[1].action == ACTION_MOVE_FORWARD
    session_context = _apply_context(session_context, pick_intents[0].context_patch)
    assert session_context["phase"] == PHASE_MOVING_FORWARD

    move_intents = await plugin.on_command_result(_ctx(session_context), _command_inbox(ACTION_MOVE_FORWARD))
    assert [intent.kind for intent in move_intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.COMMAND,
    ]
    assert move_intents[2].action == ACTION_PUT_TO_BIN
    session_context = _apply_context(session_context, move_intents[0].context_patch)
    assert session_context["phase"] == PHASE_PUTTING_TO_BIN

    put_intents = await plugin.on_command_result(_ctx(session_context), _command_inbox(ACTION_PUT_TO_BIN))
    assert [intent.kind for intent in put_intents] == [
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.COMPLETE,
    ]
    assert put_intents[0].action == "CONSUME_BIN_CELL"
    assert put_intents[1].action == "MATERIAL_MOUNTED"
    assert put_intents[1].payload_json["pkg_code"] == session_context["six_in_one"]["PkgID"]
    assert put_intents[1].payload_json["bin_code"] == "BIN-001"
    assert put_intents[2].context_patch["phase"] == PHASE_COMPLETED
