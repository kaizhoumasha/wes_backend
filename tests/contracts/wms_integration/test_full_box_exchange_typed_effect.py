"""`full_box_exchange` typed EFFECT 硬切换合同。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.system_capabilities.definition import EffectCompletionMode, SystemCapabilityMode
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.gateway import (
    FullBoxExchangeDispatchGateway,
)
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.full_box_exchange_operation import (
    FullBoxExchangeOperationRequest,
    FullBoxExchangeOperationResult,
)


def _modules() -> SimpleNamespace:
    callback_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.callback_adapter"
    )
    preparation_service = import_module(
        "src.app.runtime.orchestration.services.full_box_exchange_effect_preparation_service"
    )
    definition = import_module("src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.definition")
    effect_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.effect_adapter"
    )
    effect_contract = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.effect_contract"
    )
    handler = import_module("src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.handler")
    intent_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.intent_adapter"
    )
    return SimpleNamespace(
        FullBoxExchangeCallbackAdapter=callback_adapter.FullBoxExchangeCallbackAdapter,
        FullBoxExchangeEffectPreparationService=preparation_service.FullBoxExchangeEffectPreparationService,
        CAPABILITY_KEY=definition.CAPABILITY_KEY,
        CONTRACT_VERSION=definition.CONTRACT_VERSION,
        DEFINITION=definition.DEFINITION,
        FullBoxExchangeEffectAdapter=effect_adapter.FullBoxExchangeEffectAdapter,
        FullBoxExchangeDispatchAccepted=effect_contract.FullBoxExchangeDispatchAccepted,
        FullBoxExchangeEffectAdmission=effect_contract.FullBoxExchangeEffectAdmission,
        FullBoxExchangeEffectPrecondition=effect_contract.FullBoxExchangeEffectPrecondition,
        FullBoxExchangeEffectHandler=handler.FullBoxExchangeEffectHandler,
        FullBoxExchangeIntentAdapter=intent_adapter.FullBoxExchangeIntentAdapter,
    )


def _request(
    *,
    dispatch_key: str = "wms-full-box-exchange:WMS:RACK-001:EMPTY-001:FULL-001",
) -> FullBoxExchangeOperationRequest:
    return FullBoxExchangeOperationRequest(
        dispatch_key=dispatch_key,
        provider_code="WMS",
        rack_id="RACK-001",
        empty_box_id="EMPTY-001",
        full_box_id="FULL-001",
        workline_id=7,
        session_id=11,
        trace_id="trace-full-box-exchange",
    )


def _admission(modules: SimpleNamespace) -> Any:
    return modules.FullBoxExchangeEffectAdmission(
        precondition=modules.FullBoxExchangeEffectPrecondition(
            rack_id="RACK-001",
            empty_box_id="EMPTY-001",
            full_box_id="FULL-001",
            local_physical_fact_recorded=True,
        ),
        fact_version="rack-state:v7",
    )


def test_definition_is_operation_owned_outbox_async_effect() -> None:
    modules = _modules()

    assert (modules.CAPABILITY_KEY, modules.CONTRACT_VERSION) == (
        "wms.fulfillment.full_box_exchange",
        "v1",
    )
    assert modules.DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert modules.DEFINITION.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
    assert modules.DEFINITION.input_model is FullBoxExchangeOperationRequest
    assert modules.DEFINITION.output_model is modules.FullBoxExchangeDispatchAccepted
    assert modules.DEFINITION.handler_factory is modules.FullBoxExchangeEffectHandler
    assert modules.DEFINITION.required_ports == ()
    assert modules.DEFINITION.admission_model is modules.FullBoxExchangeEffectAdmission


def test_intent_adapter_uses_provider_rack_and_boxes_as_stable_business_identity() -> None:
    modules = _modules()
    request = _request()
    intent = modules.FullBoxExchangeIntentAdapter().build_intent(
        request,
        admission=_admission(modules),
        binding_id=23,
        binding_version=5,
    )

    assert intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert intent.operation_key == "WMS:RACK-001:EMPTY-001:FULL-001"
    assert intent.dispatch_key == request.dispatch_key
    assert intent.payload_json == request.model_dump(mode="json")
    assert intent.binding_snapshot == {"binding_id": 23, "binding_version": 5}
    assert intent.provider_snapshot == {"provider_code": "RUNTIME", "profile": "runtime"}


class _PairRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    async def add_proposed_pair(self, db: object, *, intent_log: object, outbox: object) -> None:
        self.calls.append((db, intent_log, outbox))


@pytest.mark.asyncio
async def test_effect_adapter_freezes_provider_binding_and_adds_existing_t8_pair() -> None:
    modules = _modules()
    request = _request()
    pair_repository = _PairRepository()
    adapter = modules.FullBoxExchangeEffectAdapter(
        gateway=FullBoxExchangeDispatchGateway(
            registry=EndpointRegistry(
                {"WMS_FULL_BOX_EXCHANGE": "https://wms-v1.example/api/wes/fulfillment/full-box-exchange"}
            )
        )
    )
    db = object()
    intent_log = SimpleNamespace(dispatch_key=request.dispatch_key, effect_status="PROPOSED")
    service = modules.FullBoxExchangeEffectPreparationService(intent_repository=pair_repository)

    outbox = await service.prepare(db, request=request, intent_log=intent_log, adapter=adapter)

    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.operation_identity == CONTRACT.identity
    assert outbox.operation_key == "WMS:RACK-001:EMPTY-001:FULL-001"
    assert outbox.target_snapshot_json["url"] == ("https://wms-v1.example/api/wes/fulfillment/full-box-exchange")
    assert outbox.provider_profile_identity == "wms.2026-07-06.material-flow.sandbox"
    assert outbox.canonical_payload_bytes == (
        b'{"empty_box_id":"EMPTY-001","full_box_id":"FULL-001","rack_id":"RACK-001"}'
    )
    assert pair_repository.calls == [(db, intent_log, outbox)]


class _RecordingCallbackBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, db: object, **values: object) -> object:
        self.calls.append({"db": db, **values})
        return SimpleNamespace(effect_status="recorded")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accepted", "reason_code", "expected_outcome"),
    [(True, None, "COMPLETED"), (False, "RACK_LOCKED", "REJECTED")],
)
async def test_callback_adapter_maps_typed_business_result_only_through_reducer(
    accepted: bool,
    reason_code: str | None,
    expected_outcome: str,
) -> None:
    modules = _modules()
    bridge = _RecordingCallbackBridge()
    adapter = modules.FullBoxExchangeCallbackAdapter(bridge=bridge)
    result = FullBoxExchangeOperationResult(
        dispatch_key=_request().dispatch_key,
        rack_id=_request().rack_id,
        empty_box_id=_request().empty_box_id,
        full_box_id=_request().full_box_id,
        accepted=accepted,
        exchange_request_code="EXCHANGE-001" if accepted else None,
        reason_code=reason_code,
        source_version="wms:v12",
    )

    await adapter.record(
        object(),
        result=result,
        occurred_at_ms=123_456,
        source_event_id="wms-callback:event-1",
    )

    assert bridge.calls[0]["dispatch_key"] == result.dispatch_key
    assert bridge.calls[0]["outcome"].value == expected_outcome
    assert bridge.calls[0]["reason_code"] == reason_code
    assert bridge.calls[0]["evidence_json"] == result.model_dump(mode="json")
