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
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.confirm_inbound_operation import (
    ConfirmInboundOperationRequest,
    ConfirmInboundOperationResult,
)


def _t9_modules() -> SimpleNamespace:
    preparation_service = import_module("src.app.runtime.orchestration.services.wms_effect_preparation_service")
    definition = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.definition")
    effect_adapter = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_adapter")
    effect_contract = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_contract")
    handler = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.handler")
    intent_adapter = import_module("src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.intent_adapter")
    return SimpleNamespace(
        WmsEffectPreparationService=preparation_service.WmsEffectPreparationService,
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
    intent_log = SimpleNamespace(
        dispatch_key=request.dispatch_key,
        idempotency_key="intent-confirm-inbound-001",
        effect_status="PROPOSED",
        capability_key="wms.inventory.confirm_inbound",
        capability_contract_version="v1",
        operation_identity="PKG-001",
    )
    service = modules.WmsEffectPreparationService(intent_repository=pair_repository)

    outbox = await service.prepare(
        db,
        operation=CONTRACT,
        request=request,
        intent_log=intent_log,
        adapter=adapter,
    )

    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.dispatch_key == request.dispatch_key
    assert outbox.idempotency_key == "intent-confirm-inbound-001"
    assert outbox.operation_identity == CONTRACT.identity
    assert outbox.target_snapshot_json["url"] == "https://wms-v1.example/api/wes/inventory/confirm-inbound"
    assert outbox.provider_profile_identity == "wms.2026-07-06.material-flow.sandbox"
    assert outbox.canonical_payload_bytes == (
        b'{"dispatch_key":"wms-confirm-inbound:WMS:PKG-001","inbound_key":"PKG-001","lot_no":"LOT-01",'
        b'"material_code":"MAT-001",'
        b'"owner_code":"OWNER-01","quantity":"1.25","warehouse_code":"WH-01"}'
    )
    assert intent_log.status_binding_snapshot_json["provider_profile_identity"] == outbox.provider_profile_identity
    assert len(intent_log.status_binding_snapshot_hash) == 64
    assert intent_log.operation_identity == "PKG-001"
    assert pair_repository.calls == [(db, intent_log, outbox)]

    with pytest.raises(ValueError, match="idempotency_key"):
        await service.prepare(
            db,
            operation=CONTRACT,
            request=request,
            intent_log=SimpleNamespace(
                dispatch_key=request.dispatch_key,
                idempotency_key=" ",
                effect_status="PROPOSED",
            ),
            adapter=adapter,
        )
