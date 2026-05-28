"""粗分机插件扫码入口测试。"""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.wms_integration.models import QueryInventoryResponse, WmsInventoryItem
from src.app.wms_integration.services.exceptions import WmsBusinessRejectedError, WmsUnavailableError
from src.app.workline.models.inbox import WorklineInbox
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    EVENT_SCAN_COMPLETED,
    PHASE_MEASURING,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin
from src.workline_runtime.plugin_context import PluginContext
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntentKind
from src.workline_runtime.services import WorklineRuntimeServices


class FakeWmsInventoryClient:
    def __init__(
        self,
        response: QueryInventoryResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or QueryInventoryResponse(items=[])
        self.error = error
        self.requests: list[Any] = []

    async def query_inventory(self, request: Any) -> QueryInventoryResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _ctx(**overrides: Any) -> PluginContext:
    values: dict[str, Any] = {
        "trace_id": "trace-rough-sorter-001",
        "config": {},
        "logger": SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
        "normalized_input": None,
    }
    values.update(overrides)
    return cast("PluginContext", SimpleNamespace(**values))


def _scan_payload(pkg_id: str = "PKG-ROUGH-001") -> dict[str, Any]:
    return {
        "device_code": "RS-SCAN-01",
        "event_type": EVENT_SCAN_COMPLETED,
        "data": {
            "HHPN": "HH-001",
            "MfrPN": "MFR-001",
            "Qty": "1500",
            "DateCode": "260528",
            "LotCode": "LOT-A",
            "PkgID": pkg_id,
        },
    }


def _inbox(payload: dict[str, Any]) -> WorklineInbox:
    return cast("WorklineInbox", SimpleNamespace(id=1, kind="DEVICE_EVENT", payload_json=payload))


def _rough_sorter_context(pkg_id: str = "PKG-ROUGH-001") -> dict[str, Any]:
    return {
        "six_in_one": {
            "HHPN": "HH-001",
            "MfrPN": "MFR-001",
            "Qty": "1500",
            "DateCode": "260528",
            "LotCode": "LOT-A",
            "PkgID": pkg_id,
        },
        "business_key": pkg_id,
        "phase": PHASE_MEASURING,
    }


def _measurement_ctx(
    *,
    wms_client: FakeWmsInventoryClient | None = None,
    normalized_result: str = "SUCCESS",
    source_result: str = "SUCCESS",
    data: dict[str, Any] | None = None,
    error_detail: dict[str, Any] | None = None,
    session_context: dict[str, Any] | None = None,
) -> PluginContext:
    return _ctx(
        session=SimpleNamespace(context_json=session_context or _rough_sorter_context()),
        services=WorklineRuntimeServices(wms_inventory_client=wms_client),
        normalized_input=NormalizedCommandResult(
            command_code="CMD-MEASURE-001",
            source_result=source_result,
            normalized_result=normalized_result,
            command_type=ACTION_MEASUREMENT_REEL,
            device_code="RS-MEASURE-01",
            data=data or {"reel_diameter": "178.0", "reel_thickness": "15.0"},
            error_detail=error_detail or {},
        ),
    )


def _measurement_inbox(
    *,
    result: str = "SUCCESS",
    data: dict[str, Any] | None = None,
    error_detail: dict[str, Any] | None = None,
) -> WorklineInbox:
    payload: dict[str, Any] = {
        "command_code": "CMD-MEASURE-001",
        "device_code": "RS-MEASURE-01",
        "task_type": "TEST",
        "result": result,
        "data": data or {"reel_diameter": "178.0", "reel_thickness": "15.0"},
    }
    if error_detail is not None:
        payload["error_detail"] = error_detail
    return cast("WorklineInbox", SimpleNamespace(id=2, kind="COMMAND_RESULT", payload_json=payload))


def _wms_response(*, sku: str = "HH-001", lot_no: str | None = "LOT-A") -> QueryInventoryResponse:
    return QueryInventoryResponse(
        items=[
            WmsInventoryItem(
                sku=sku,
                lot_no=lot_no,
                total_qty=Decimal("1500"),
                available_qty=Decimal("1500"),
                reserved_qty=Decimal("0"),
            )
        ]
    )


@pytest.mark.asyncio
async def test_scan_ok_updates_context_and_dispatches_measurement_command() -> None:
    intents = await RoughSorterPlugin().on_device_event(_ctx(), _inbox(_scan_payload()))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert intents[0].context_patch["phase"] == PHASE_MEASURING
    assert intents[0].context_patch["six_in_one"]["PkgID"] == "PKG-ROUGH-001"
    assert intents[1].action == ACTION_MEASUREMENT_REEL
    assert intents[1].device_role == ROLE_INPUT_ARM
    assert intents[1].payload_json["task_type"] == "TEST"
    assert intents[1].payload_json["params"]["action"] == ACTION_MEASUREMENT_REEL


@pytest.mark.asyncio
async def test_scan_ng_marks_material_ng_and_dispatches_move_to_ng() -> None:
    intents = await RoughSorterPlugin().on_device_event(_ctx(), _inbox(_scan_payload(pkg_id="PKG-SIZENG-001")))

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["phase"] == PHASE_NG_MOVING
    assert intents[0].context_patch["ng_reason"]["reason_code"] == "SCAN_NG_BY_RULE"
    assert intents[1].reason_code == "SCAN_NG_BY_RULE"
    assert intents[2].action == ACTION_MOVE_TO_NG
    assert intents[2].device_role == ROLE_OUTPUT_ARM
    assert intents[2].payload_json["params"]["action"] == ACTION_MOVE_TO_NG
    assert intents[2].payload_json["params"]["source_location"] == "RS-SCAN-01"


@pytest.mark.asyncio
async def test_measurement_callback_routes_by_command_params_action_when_device_reports_test_task_type() -> None:
    command = SimpleNamespace(task_type="TEST", params={"action": ACTION_MEASUREMENT_REEL})
    resolved_action = CallbackOrchestrationService()._resolve_command_type(
        {"task_type": "TEST"},
        command.params,
        command,
    )
    wms_client = FakeWmsInventoryClient(response=_wms_response())
    ctx = _measurement_ctx(wms_client=wms_client)
    inbox = _measurement_inbox()

    intents = await RoughSorterPlugin().on_command_result(ctx, inbox)

    assert resolved_action == ACTION_MEASUREMENT_REEL
    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert intents[0].context_patch["phase"] == PHASE_PICK_TO_PIPELINE
    assert intents[0].context_patch["wms_validation"]["matched"] is True
    assert intents[1].action == ACTION_PICK_AND_PUT
    assert intents[1].device_role == ROLE_INPUT_ARM
    assert intents[1].payload_json["params"]["action"] == ACTION_PICK_AND_PUT
    assert wms_client.requests[0].request_id == "rough-sorter:inventory:PKG-ROUGH-001"
    assert wms_client.requests[0].trace_id == "trace-rough-sorter-001"
    assert wms_client.requests[0].sku == "HH-001"
    assert wms_client.requests[0].lot_no == "LOT-A"


@pytest.mark.asyncio
async def test_measurement_success_with_wms_no_match_marks_ng_and_moves_to_ng() -> None:
    ctx = _measurement_ctx(wms_client=FakeWmsInventoryClient(response=QueryInventoryResponse(items=[])))

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox())

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["phase"] == PHASE_NG_MOVING
    assert intents[0].context_patch["wms_validation"]["matched"] is False
    assert intents[1].reason_code == "WMS_REJECTED"
    assert intents[2].action == ACTION_MOVE_TO_NG
    assert intents[2].device_role == ROLE_OUTPUT_ARM


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sku", "lot_no"),
    [
        ("WRONG-SKU", "LOT-A"),
        ("HH-001", "WRONG-LOT"),
    ],
)
async def test_measurement_success_with_wms_sku_or_lot_mismatch_marks_ng(sku: str, lot_no: str) -> None:
    ctx = _measurement_ctx(wms_client=FakeWmsInventoryClient(response=_wms_response(sku=sku, lot_no=lot_no)))

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox())

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[1].reason_code == "WMS_REJECTED"
    assert intents[2].payload_json["params"]["reason_code"] == "WMS_REJECTED"


@pytest.mark.asyncio
async def test_measurement_success_with_wms_business_rejected_marks_ng_with_evidence() -> None:
    wms_client = FakeWmsInventoryClient(
        error=WmsBusinessRejectedError(
            "WMS 拒绝库存校验",
            operation_name="query_inventory",
            evidence_key="wms-evidence-001",
            reason_code="LOT_BLOCKED",
            target_code="MOCK_WMS",
        )
    )
    ctx = _measurement_ctx(wms_client=wms_client)

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox())

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["wms_validation"]["evidence_key"] == "wms-evidence-001"
    assert intents[0].context_patch["wms_validation"]["reason_code"] == "LOT_BLOCKED"
    assert intents[0].context_patch["measurement"]["reel_diameter"] == "178.0"
    assert intents[0].context_patch["measurement"]["reel_thickness"] == "15.0"
    assert intents[1].reason_code == "WMS_REJECTED"
    assert intents[1].payload_json["measurement"]["reel_diameter"] == "178.0"


@pytest.mark.asyncio
async def test_measurement_success_with_wms_unavailable_blocks_material() -> None:
    wms_client = FakeWmsInventoryClient(
        error=WmsUnavailableError(
            "WMS 不可用",
            operation_name="query_inventory",
            evidence_key="wms-evidence-unavailable",
            reason_code="WMS_UNAVAILABLE",
            retryable=True,
        )
    )
    ctx = _measurement_ctx(wms_client=wms_client)

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "WMS_UNAVAILABLE"
    assert intents[0].payload_json["evidence_key"] == "wms-evidence-unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"reel_thickness": "15.0"},
        {"reel_diameter": "178.0", "reel_thickness": "bad-thickness"},
    ],
)
async def test_measurement_success_with_missing_or_unparseable_measurement_blocks(data: dict[str, Any]) -> None:
    ctx = _measurement_ctx(wms_client=FakeWmsInventoryClient(response=_wms_response()), data=data)

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox(data=data))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"reel_diameter": "178.0", "reel_thickness": "15.0", "size_judgement": "NG"},
        {"reel_diameter": "178.0", "reel_thickness": "15.0", "thickness_judgement": "NG"},
    ],
)
async def test_measurement_success_with_size_or_thickness_ng_marks_ng(data: dict[str, Any]) -> None:
    ctx = _measurement_ctx(wms_client=FakeWmsInventoryClient(response=_wms_response()), data=data)

    intents = await RoughSorterPlugin().on_command_result(ctx, _measurement_inbox(data=data))

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[1].reason_code == "MEASUREMENT_NG"
    assert intents[2].payload_json["params"]["reason_code"] == "MEASUREMENT_NG"


@pytest.mark.asyncio
async def test_measurement_failed_business_ng_marks_ng_and_moves_to_ng() -> None:
    error_detail = {"error_code": "INSPECTION_THICKNESS_NG", "error_message": "厚度检测 NG"}
    ctx = _measurement_ctx(
        source_result="FAILED",
        normalized_result="TERMINAL_FAILURE",
        error_detail=error_detail,
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _measurement_inbox(result="FAILED", error_detail=error_detail),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[1].reason_code == "MEASUREMENT_NG"


@pytest.mark.asyncio
async def test_measurement_failed_hardware_error_blocks_material() -> None:
    error_detail = {"error_code": "MOTOR_TIMEOUT", "error_message": "测量电机超时"}
    ctx = _measurement_ctx(
        source_result="FAILED",
        normalized_result="TERMINAL_FAILURE",
        error_detail=error_detail,
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _measurement_inbox(result="FAILED", error_detail=error_detail),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "ROUGH_SORTER_MEASUREMENT_FAILED"
