"""`confirm_inbound` typed EFFECT 硬切换合同。"""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.system_capabilities.definition import EffectCompletionMode, SystemCapabilityMode
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.gateway import ConfirmInboundDispatchGateway
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_PRODUCTION_PROFILE_IDENTITY
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.confirm_inbound_operation import (
    ConfirmInboundOperationRequest,
    ConfirmInboundOperationResult,
)


def _t9_modules() -> SimpleNamespace:
    callback_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.callback_adapter"
    )
    preparation_service = import_module(
        "src.app.runtime.orchestration.services.confirm_inbound_effect_preparation_service"
    )
    definition = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.definition")
    effect_adapter = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_adapter")
    effect_contract = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_contract")
    handler = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.handler")
    intent_adapter = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.intent_adapter")
    return SimpleNamespace(
        ConfirmInboundCallbackAdapter=callback_adapter.ConfirmInboundCallbackAdapter,
        ConfirmInboundEffectPreparationService=preparation_service.ConfirmInboundEffectPreparationService,
        CAPABILITY_KEY=definition.CAPABILITY_KEY,
        CONTRACT_VERSION=definition.CONTRACT_VERSION,
        DEFINITION=definition.DEFINITION,
        ConfirmInboundEffectAdapter=effect_adapter.ConfirmInboundEffectAdapter,
        ConfirmInboundDispatchAccepted=effect_contract.ConfirmInboundDispatchAccepted,
        ConfirmInboundEffectAdmission=effect_contract.ConfirmInboundEffectAdmission,
        ConfirmInboundEffectPrecondition=effect_contract.ConfirmInboundEffectPrecondition,
        ConfirmInboundEffectHandler=handler.ConfirmInboundEffectHandler,
        ConfirmInboundIntentAdapter=intent_adapter.ConfirmInboundIntentAdapter,
    )


def _request(*, dispatch_key: str = "wms-confirm-inbound:WMS:PKG-001") -> ConfirmInboundOperationRequest:
    return ConfirmInboundOperationRequest(
        dispatch_key=dispatch_key,
        inbound_key="PKG-001",
        material_code="MAT-001",
        quantity=Decimal("1.25"),
        warehouse_code="WH-01",
        owner_code="OWNER-01",
        lot_no="LOT-01",
        workline_id=7,
        session_id=11,
        trace_id="trace-confirm-inbound",
    )


def _admission(modules: SimpleNamespace) -> Any:
    return modules.ConfirmInboundEffectAdmission(
        precondition=modules.ConfirmInboundEffectPrecondition(
            inbound_key="PKG-001",
            local_physical_fact_recorded=True,
        ),
        fact_version="runtime-location:v7",
    )


def test_confirm_inbound_definition_is_operation_owned_outbox_async_effect() -> None:
    modules = _t9_modules()

    assert (modules.CAPABILITY_KEY, modules.CONTRACT_VERSION) == ("wms.inventory.confirm_inbound", "v1")
    assert modules.DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert modules.DEFINITION.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
    assert modules.DEFINITION.input_model is ConfirmInboundOperationRequest
    assert modules.DEFINITION.output_model is modules.ConfirmInboundDispatchAccepted
    assert modules.DEFINITION.handler_factory is modules.ConfirmInboundEffectHandler
    assert modules.DEFINITION.required_ports == ()
    assert modules.DEFINITION.admission_model is modules.ConfirmInboundEffectAdmission


def test_intent_adapter_keeps_runtime_claim_identity_typed_and_immutable() -> None:
    modules = _t9_modules()
    request = _request()
    intent = modules.ConfirmInboundIntentAdapter().build_intent(
        request,
        admission=_admission(modules),
        binding_id=23,
        binding_version=5,
    )

    assert intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert intent.operation_key == "PKG-001"
    assert intent.dispatch_key == request.dispatch_key
    assert intent.payload_json == request.model_dump(mode="json")
    assert intent.creator_authority == "WORKLINE_PLUGIN"
    assert intent.authorization_policy == "PLUGIN_DECLARED_CAPABILITY"
    assert intent.binding_snapshot == {"binding_id": 23, "binding_version": 5}
    assert intent.provider_snapshot == {"provider_code": "RUNTIME", "profile": "runtime"}


class _PairRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    async def add_proposed_pair(self, db: object, *, intent_log: object, outbox: object) -> None:
        self.calls.append((db, intent_log, outbox))


@pytest.mark.asyncio
async def test_effect_adapter_freezes_provider_binding_and_adds_existing_t8_pair() -> None:
    modules = _t9_modules()
    request = _request()
    pair_repository = _PairRepository()
    adapter = modules.ConfirmInboundEffectAdapter(
        gateway=ConfirmInboundDispatchGateway(
            registry=EndpointRegistry(
                {"WMS_INBOUND_CONFIRM": "https://wms-v1.example/api/wes/inventory/confirm-inbound"}
            )
        )
    )
    db = object()
    intent_log = SimpleNamespace(dispatch_key=request.dispatch_key, effect_status="PROPOSED")
    service = modules.ConfirmInboundEffectPreparationService(intent_repository=pair_repository)

    outbox = await service.prepare(
        db,
        request=request,
        intent_log=intent_log,
        adapter=adapter,
    )

    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.dispatch_key == request.dispatch_key
    assert outbox.operation_identity == CONTRACT.identity
    assert outbox.target_snapshot_json["url"] == "https://wms-v1.example/api/wes/inventory/confirm-inbound"
    assert outbox.provider_profile_identity == WMS_PRODUCTION_PROFILE_IDENTITY
    assert outbox.canonical_payload_bytes == (
        b'{"inbound_key":"PKG-001","lot_no":"LOT-01","material_code":"MAT-001",'
        b'"owner_code":"OWNER-01","quantity":"1.25","warehouse_code":"WH-01"}'
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
    [
        (True, None, "COMPLETED"),
        (False, "MATERIAL_BLOCKED", "REJECTED"),
    ],
)
async def test_callback_adapter_maps_typed_business_result_only_through_reducer(
    accepted: bool,
    reason_code: str | None,
    expected_outcome: str,
) -> None:
    modules = _t9_modules()
    bridge = _RecordingCallbackBridge()
    adapter = modules.ConfirmInboundCallbackAdapter(bridge=bridge)
    result = ConfirmInboundOperationResult(
        dispatch_key="wms-confirm-inbound:WMS:PKG-001",
        inbound_key="PKG-001",
        accepted=accepted,
        document_no="GRN-001" if accepted else None,
        reason_code=reason_code,
        source_version="wms:v12",
    )

    reduced = await adapter.record(
        object(),
        result=result,
        occurred_at_ms=123_456,
        source_event_id="wms-callback:event-1",
    )

    assert reduced.effect_status == "recorded"
    assert bridge.calls[0]["dispatch_key"] == result.dispatch_key
    assert bridge.calls[0]["outcome"].value == expected_outcome
    assert bridge.calls[0]["reason_code"] == reason_code
    assert bridge.calls[0]["evidence_json"] == result.model_dump(mode="json")
