"""粗分机插件扫码入口测试。"""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.resource.services.smt_rack_bin_scheduling_service import (
    SmtRackBinSchedulingDecision,
    SmtRackOperationRequest,
)
from src.app.wms_integration.models import QueryInventoryResponse, WmsInventoryItem
from src.app.wms_integration.services.exceptions import WmsBusinessRejectedError, WmsUnavailableError
from src.app.workline.models.inbox import WorklineInbox
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_SCAN_COMPLETED,
    PHASE_COMPLETED,
    PHASE_MEASURING,
    PHASE_MOVING_FORWARD,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
    PHASE_WAITING_RACK,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin
from src.workline_runtime.plugin_context import PluginContext
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntentKind
from src.workline_runtime.services import WorklineRuntimeServices


class FakeActiveRackSnapshotProvider:
    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self.snapshot = snapshot
        self.contexts: list[Any] = []

    async def active_bin_rack(self, *, context: Any | None = None) -> dict[str, Any] | None:
        self.contexts.append(context)
        return self.snapshot


class FakeBinAllocator:
    def __init__(self, decision: Any | None = None) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def allocate(self, barcode: str) -> dict[str, Any] | None:
        self.calls.append({"method": "allocate", "barcode": barcode})
        return None

    def plan_allocation(self, barcode: str, *, context: Any | None = None) -> Any:
        self.calls.append({"method": "plan_allocation", "barcode": barcode, "context": context})
        return self.decision


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


def _command_ctx(
    *,
    command_type: str,
    source_result: str = "SUCCESS",
    normalized_result: str = "SUCCESS",
    device_code: str = "RS-COMMAND-01",
    services: WorklineRuntimeServices | None = None,
    session_context: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    error_detail: dict[str, Any] | None = None,
) -> PluginContext:
    return _ctx(
        config=config or {},
        session=SimpleNamespace(context_json=session_context or _rough_sorter_context()),
        services=services or WorklineRuntimeServices(),
        normalized_input=NormalizedCommandResult(
            command_code=f"CMD-{command_type}",
            source_result=source_result,
            normalized_result=normalized_result,
            command_type=command_type,
            device_code=device_code,
            error_detail=error_detail or {},
        ),
    )


def _command_inbox(
    *,
    command_type: str,
    result: str = "SUCCESS",
    device_code: str = "RS-COMMAND-01",
    error_detail: dict[str, Any] | None = None,
) -> WorklineInbox:
    payload: dict[str, Any] = {
        "command_code": f"CMD-{command_type}",
        "device_code": device_code,
        "task_type": command_type,
        "result": result,
    }
    if error_detail is not None:
        payload["error_detail"] = error_detail
    return cast("WorklineInbox", SimpleNamespace(id=4, kind="COMMAND_RESULT", payload_json=payload))


def _external_http_ctx(
    *,
    session_context: dict[str, Any] | None = None,
    session_id: int = 123,
    services: WorklineRuntimeServices | None = None,
) -> PluginContext:
    return _ctx(
        session=SimpleNamespace(
            id=session_id, context_json=session_context or _rough_sorter_context_for_phase(PHASE_WAITING_RACK)
        ),
        services=services or WorklineRuntimeServices(),
    )


def _external_http_inbox(payload: dict[str, Any]) -> WorklineInbox:
    return cast(
        "WorklineInbox",
        SimpleNamespace(
            id=5,
            kind="EXTERNAL_HTTP",
            payload_json=payload,
            event_id=payload.get("source_event_id"),
        ),
    )


def _rough_sorter_context_for_phase(phase: str) -> dict[str, Any]:
    context = _rough_sorter_context()
    context["phase"] = phase
    context["measurement"] = {"reel_diameter": "178.0", "reel_thickness": "15.0"}
    context["wms_validation"] = {"matched": True}
    return context


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


@pytest.mark.asyncio
async def test_pick_and_put_success_at_pick_to_pipeline_dispatches_move_forward() -> None:
    ctx = _command_ctx(
        command_type=ACTION_PICK_AND_PUT,
        device_code="RS-INPUT-ARM-01",
        session_context=_rough_sorter_context_for_phase(PHASE_PICK_TO_PIPELINE),
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=ACTION_PICK_AND_PUT, device_code="RS-INPUT-ARM-01"),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert intents[0].context_patch["phase"] == PHASE_MOVING_FORWARD
    assert intents[1].action == ACTION_MOVE_FORWARD
    assert intents[1].device_role == ROLE_CONVEYOR
    assert intents[1].payload_json["params"]["action"] == ACTION_MOVE_FORWARD
    assert intents[1].payload_json["params"]["source_location"] == "RS-INPUT-ARM-01"


@pytest.mark.asyncio
async def test_pick_and_put_success_at_ng_moving_completes_session() -> None:
    ctx = _command_ctx(
        command_type=ACTION_MOVE_TO_NG,
        session_context=_rough_sorter_context_for_phase(PHASE_NG_MOVING),
    )

    intents = await RoughSorterPlugin().on_command_result(ctx, _command_inbox(command_type=ACTION_MOVE_TO_NG))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.COMPLETE]
    assert intents[0].context_patch["phase"] == "COMPLETED"


@pytest.mark.asyncio
async def test_move_forward_success_with_allocated_bin_claims_cell_and_dispatches_put_to_bin() -> None:
    decision = SmtRackBinSchedulingDecision(
        bin_location={
            "bin_id": "BIN-001",
            "bin_cell_index": "4",
            "bin_cell_location": "BIN-001-4",
            "material_identity_key": "HH-001:LOT-A:260528",
        }
    )
    allocator = FakeBinAllocator(decision=decision)
    active_rack_provider = FakeActiveRackSnapshotProvider(snapshot={"rack_id": "RACK-001"})
    ctx = _command_ctx(
        command_type=ACTION_MOVE_FORWARD,
        device_code="RS-CONVEYOR-01",
        session_context=_rough_sorter_context_for_phase(PHASE_MOVING_FORWARD),
        services=WorklineRuntimeServices(
            bin_allocator=allocator,
            active_rack_snapshot_provider=active_rack_provider,
        ),
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=ACTION_MOVE_FORWARD, device_code="RS-CONVEYOR-01"),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["phase"] == PHASE_PUTTING_TO_BIN
    assert intents[0].context_patch["target_bin_location"]["bin_id"] == "BIN-001"
    context_snapshot = RoughSorterContext.model_validate(intents[0].context_patch)
    assert isinstance(context_snapshot.target_bin_location, dict)
    assert context_snapshot.target_bin_location["bin_id"] == "BIN-001"
    assert intents[1].action == "CLAIM_BIN_CELL"
    assert intents[1].payload_json["pkg_code"] == "PKG-ROUGH-001"
    assert intents[1].payload_json["bin_code"] == "BIN-001"
    assert intents[1].payload_json["bin_cell_index"] == "4"
    assert intents[2].action == ACTION_PUT_TO_BIN
    assert intents[2].device_role == ROLE_OUTPUT_ARM
    assert intents[2].payload_json["params"]["bin_location"] == "BIN-001-4"
    assert allocator.calls[0]["barcode"] == "PKG-ROUGH-001"
    assert allocator.calls[0]["context"]["active_bin_rack"] == {"rack_id": "RACK-001"}


@pytest.mark.asyncio
async def test_move_forward_success_with_rack_operation_required_stores_resume_anchor() -> None:
    rack_operation_request = SmtRackOperationRequest(
        operation_key="external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        target_code="WMS_RCS_RACK_OPERATION",
        payload={
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "trace_id": "trace-rough-sorter-001",
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": "SINGLE_LAYER_A",
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                }
            ],
        },
    )
    allocator = FakeBinAllocator(decision=SmtRackBinSchedulingDecision(rack_operation_request=rack_operation_request))
    ctx = _command_ctx(
        command_type=ACTION_MOVE_FORWARD,
        device_code="RS-CONVEYOR-01",
        session_context=_rough_sorter_context_for_phase(PHASE_MOVING_FORWARD),
        services=WorklineRuntimeServices(bin_allocator=allocator),
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=ACTION_MOVE_FORWARD, device_code="RS-CONVEYOR-01"),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RACK_OPERATION_REQUEST,
    ]
    assert intents[0].context_patch["phase"] == PHASE_WAITING_RACK
    assert intents[0].context_patch["resume_source_device_code"] == "RS-CONVEYOR-01"
    assert intents[0].context_patch["rack_operation"]["operation_key"] == rack_operation_request.operation_key
    assert intents[1].action == "REPLACE_CLASSIFIER_WORK_RACK"
    assert intents[1].idempotency_key == rack_operation_request.operation_key
    assert intents[1].target_code == "WMS_RCS_RACK_OPERATION"
    assert intents[1].payload_json["rack_tasks"][0]["task_type"] == "ALLOCATE_AND_MOVE_RACK"
    assert intents[0].context_patch["rack_operation"]["rack_kind"] == "SINGLE_LAYER"
    assert intents[0].context_patch["rack_operation"]["target_position_code"] == "SINGLE_LAYER_A"
    assert intents[0].context_patch["rack_operation"]["work_position_code"] == "SINGLE_LAYER_A"


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_type", ["WMS_RACK_ARRIVED", "RCS_RACK_ARRIVED"])
async def test_rack_arrived_external_http_emits_resource_facts_and_stable_retry_event(callback_type: str) -> None:
    session_context = _rough_sorter_context_for_phase(PHASE_WAITING_RACK)
    session_context["rack_operation"] = {
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
        "target_code": "WMS_RCS_RACK_OPERATION",
        "status": "REQUESTED",
    }
    session_context["resume_source_device_code"] = "RS-CONVEYOR-01"
    ctx = _external_http_ctx(session_context=session_context, session_id=321)
    callback_payload = {
        "callback_type": callback_type,
        "dispatch_key": "rack-operation:dispatch-001",
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "source_event_id": "wms-rack-arrived-001",
        "rack_code": "RACK-001",
        "rack_kind": "SINGLE_LAYER",
        "target_position_code": "SINGLE_LAYER_A",
        "active_bin_rack": {"rack_id": "RACK-001", "cells": []},
        "bin_mounts": [
            {"bin_code": "BIN-001", "rack_code": "RACK-001", "slot_code": "A"},
            {"bin_code": "BIN-002", "rack_code": "RACK-001", "rack_slot_code": "B"},
        ],
    }

    intents = await RoughSorterPlugin().on_external_http(ctx, _external_http_inbox(callback_payload))

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.DEVICE_EVENT,
    ]
    assert intents[0].action == "RACK_ARRIVED"
    assert intents[0].idempotency_key == "RACK_ARRIVED:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION"
    assert intents[0].payload_json["rack_code"] == "RACK-001"
    assert intents[0].payload_json["rack_kind"] == "SINGLE_LAYER"
    assert intents[0].payload_json["position_code"] == "SINGLE_LAYER_A"
    assert intents[1].action == "BIN_MOUNTED"
    assert intents[1].payload_json["rack_code"] == "RACK-001"
    assert intents[1].payload_json["bin_mounts"] == [
        {"rack_slot_code": "A", "bin_code": "BIN-001"},
        {"rack_slot_code": "B", "bin_code": "BIN-002"},
    ]
    assert intents[2].action is None
    assert intents[2].payload_json["event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert intents[2].payload_json["canonical_event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert (
        intents[2].payload_json["event_id"]
        == "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321"
    )
    assert intents[2].payload_json["device_code"] == "RS-CONVEYOR-01"
    assert intents[2].payload_json["data"]["PkgID"] == "PKG-ROUGH-001"
    assert intents[2].payload_json["data"]["idempotency_key"] == intents[2].payload_json["event_id"]

    repeated_payload = {**callback_payload, "source_event_id": "wms-rack-arrived-duplicate"}
    repeated_intents = await RoughSorterPlugin().on_external_http(ctx, _external_http_inbox(repeated_payload))
    assert repeated_intents[2].payload_json["event_id"] == intents[2].payload_json["event_id"]


@pytest.mark.asyncio
async def test_rack_arrived_external_http_derives_projection_fields_from_session_context() -> None:
    session_context = _rough_sorter_context_for_phase(PHASE_WAITING_RACK)
    session_context["rack_operation"] = {
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
        "target_code": "WMS_RCS_RACK_OPERATION",
        "status": "PENDING",
        "rack_kind": "SINGLE_LAYER",
        "target_position_code": "SINGLE_LAYER_A",
        "work_position_code": "SINGLE_LAYER_A",
        "released_rack_codes": ["RACK-OLD"],
    }
    session_context["resume_source_device_code"] = "RS-CONVEYOR-01"
    ctx = _external_http_ctx(session_context=session_context, session_id=321)
    callback_payload = {
        "callback_type": "WMS_RACK_ARRIVED",
        "dispatch_key": "rack-operation:dispatch-001",
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "source_event_id": "wms-rack-arrived-001",
        "source_system": "WMS",
        "active_bin_rack": {"rack_id": "RACK-NEW", "cells": []},
    }

    intents = await RoughSorterPlugin().on_external_http(ctx, _external_http_inbox(callback_payload))

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.DEVICE_EVENT,
    ]
    assert intents[0].action == "RACK_ARRIVED"
    assert intents[0].payload_json["rack_code"] == "RACK-NEW"
    assert intents[0].payload_json["rack_kind"] == "SINGLE_LAYER"
    assert intents[0].payload_json["position_code"] == "SINGLE_LAYER_A"
    assert intents[0].payload_json["released_rack_codes"] == ["RACK-OLD"]


@pytest.mark.asyncio
async def test_rough_sorter_storage_retry_event_replans_allocation_after_resource_projection() -> None:
    decision = SmtRackBinSchedulingDecision(
        bin_location={
            "bin_id": "BIN-001",
            "bin_cell_index": "4",
            "bin_cell_location": "BIN-001-4",
        }
    )
    allocator = FakeBinAllocator(decision=decision)
    active_rack_provider = FakeActiveRackSnapshotProvider(snapshot={"rack_id": "RACK-001", "cells": []})
    session_context = _rough_sorter_context_for_phase(PHASE_WAITING_RACK)
    session_context["rack_operation"] = {
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "status": "REQUESTED",
    }
    ctx = _ctx(
        session=SimpleNamespace(id=321, context_json=session_context),
        services=WorklineRuntimeServices(
            bin_allocator=allocator,
            active_rack_snapshot_provider=active_rack_provider,
        ),
    )

    intents = await RoughSorterPlugin().on_device_event(
        ctx,
        _inbox(
            {
                "device_code": "RS-CONVEYOR-01",
                "event_type": "ROUGH_SORTER_STORAGE_RETRY",
                "event_id": "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321",
                "data": {
                    "PkgID": "PKG-ROUGH-001",
                    "rack_operation": {"status": "ARRIVED"},
                    "idempotency_key": "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321",
                },
            }
        ),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["phase"] == PHASE_PUTTING_TO_BIN
    assert intents[2].action == ACTION_PUT_TO_BIN
    assert allocator.calls[0]["context"]["active_bin_rack"] == {"rack_id": "RACK-001", "cells": []}


@pytest.mark.asyncio
async def test_rough_sorter_storage_retry_uses_callback_active_bin_rack_without_provider() -> None:
    decision = SmtRackBinSchedulingDecision(
        bin_location={
            "bin_id": "BIN-001",
            "bin_cell_index": "4",
            "bin_cell_location": "BIN-001-4",
        }
    )
    allocator = FakeBinAllocator(decision=decision)
    session_context = _rough_sorter_context_for_phase(PHASE_WAITING_RACK)
    session_context["rack_operation"] = {
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "status": "REQUESTED",
    }
    callback_active_bin_rack = {"rack_id": "RACK-CALLBACK", "cells": [{"slot_code": "A"}]}
    ctx = _ctx(
        session=SimpleNamespace(id=321, context_json=session_context),
        services=WorklineRuntimeServices(bin_allocator=allocator),
    )

    intents = await RoughSorterPlugin().on_device_event(
        ctx,
        _inbox(
            {
                "device_code": "RS-CONVEYOR-01",
                "event_type": "ROUGH_SORTER_STORAGE_RETRY",
                "event_id": "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321",
                "data": {
                    "PkgID": "PKG-ROUGH-001",
                    "rack_operation": {"status": "ARRIVED"},
                    "active_bin_rack": callback_active_bin_rack,
                    "idempotency_key": "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321",
                },
            }
        ),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.COMMAND,
    ]
    assert allocator.calls[0]["context"]["active_bin_rack"] == callback_active_bin_rack


@pytest.mark.asyncio
async def test_put_to_bin_success_consumes_reservation_records_material_and_completes() -> None:
    session_context = _rough_sorter_context_for_phase(PHASE_PUTTING_TO_BIN)
    session_context["target_bin_location"] = {
        "bin_id": "BIN-001",
        "bin_cell_index": "4",
        "bin_cell_location": "BIN-001-4",
        "material_identity_key": "MAT:HH-001:MFR-001:260528:LOT-A",
        "capacity_depth_mm": 10.0,
    }
    session_context["wms_validation"] = {
        "matched": True,
        "wms_inventory_id": "INV-ROUGH-001",
    }
    ctx = _command_ctx(
        command_type=ACTION_PUT_TO_BIN,
        device_code="RS-OUTPUT-01",
        session_context=session_context,
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=ACTION_PUT_TO_BIN, device_code="RS-OUTPUT-01"),
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.COMPLETE,
    ]
    assert intents[0].action == "CONSUME_BIN_CELL"
    assert intents[0].payload_json["bin_code"] == "BIN-001"
    assert intents[0].payload_json["bin_cell_index"] == "4"
    assert intents[0].idempotency_key == "CONSUME_BIN_CELL:PKG-ROUGH-001:BIN-001:4"
    assert intents[1].action == "MATERIAL_MOUNTED"
    assert intents[1].payload_json["pkg_code"] == "PKG-ROUGH-001"
    assert intents[1].payload_json["bin_code"] == "BIN-001"
    assert intents[1].payload_json["bin_cell_index"] == "4"
    assert intents[1].payload_json["bin_cell_code"] == "BIN-001-4"
    assert intents[1].payload_json["material_identity_key"] == "MAT:HH-001:MFR-001:260528:LOT-A"
    assert intents[1].payload_json["material_code"] == "HH-001"
    assert intents[1].payload_json["lot_code"] == "LOT-A"
    assert intents[1].payload_json["date_code"] == "260528"
    assert intents[1].payload_json["wms_inventory_id"] == "INV-ROUGH-001"
    assert intents[1].payload_json["reel_diameter"] == "178.0"
    assert intents[1].payload_json["reel_thickness"] == "15.0"
    assert intents[1].payload_json["cell_capacity_depth_mm"] == 10.0
    assert intents[1].idempotency_key == "MATERIAL_MOUNTED:PKG-ROUGH-001:BIN-001:4"
    assert intents[2].context_patch["phase"] == PHASE_COMPLETED


@pytest.mark.asyncio
async def test_put_to_bin_failed_blocks_without_releasing_reservation() -> None:
    error_detail = {"error_code": "OUTPUT_ARM_JAMMED", "error_message": "出料机械臂卡料"}
    session_context = _rough_sorter_context_for_phase(PHASE_PUTTING_TO_BIN)
    session_context["target_bin_location"] = {
        "bin_id": "BIN-001",
        "bin_cell_index": "4",
        "bin_cell_location": "BIN-001-4",
    }
    ctx = _command_ctx(
        command_type=ACTION_PUT_TO_BIN,
        source_result="FAILED",
        normalized_result="TERMINAL_FAILURE",
        device_code="RS-OUTPUT-01",
        error_detail=error_detail,
        session_context=session_context,
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=ACTION_PUT_TO_BIN, result="FAILED", error_detail=error_detail),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "ROUGH_SORTER_HANDLING_COMMAND_FAILED"
    assert intents[0].payload_json["error_detail"]["error_code"] == "OUTPUT_ARM_JAMMED"
    assert intents[0].payload_json["error_detail"]["error_message"] == "出料机械臂卡料"


@pytest.mark.asyncio
async def test_move_forward_success_with_blocked_allocator_blocks_material() -> None:
    allocator = FakeBinAllocator(
        decision=SmtRackBinSchedulingDecision(
            kind="BLOCKED",
            reason_code="NO_AVAILABLE_BIN_CELL",
            message="没有可用料格",
        )
    )
    ctx = _command_ctx(
        command_type=ACTION_MOVE_FORWARD,
        session_context=_rough_sorter_context_for_phase(PHASE_MOVING_FORWARD),
        services=WorklineRuntimeServices(bin_allocator=allocator),
    )

    intents = await RoughSorterPlugin().on_command_result(ctx, _command_inbox(command_type=ACTION_MOVE_FORWARD))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "NO_AVAILABLE_BIN_CELL"


@pytest.mark.asyncio
@pytest.mark.parametrize("command_type", [ACTION_PICK_AND_PUT, ACTION_MOVE_TO_NG, ACTION_MOVE_FORWARD])
async def test_handling_command_failed_blocks_without_marking_business_ng(command_type: str) -> None:
    error_detail = {"error_code": "DEVICE_TIMEOUT", "error_message": "设备执行超时"}
    ctx = _command_ctx(
        command_type=command_type,
        source_result="FAILED",
        normalized_result="TERMINAL_FAILURE",
        error_detail=error_detail,
    )

    intents = await RoughSorterPlugin().on_command_result(
        ctx,
        _command_inbox(command_type=command_type, result="FAILED", error_detail=error_detail),
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].reason_code == "ROUGH_SORTER_HANDLING_COMMAND_FAILED"
