"""`notify_pkg_binding` typed EFFECT 硬切换合同。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind
from src.app.runtime.system_capabilities.definition import EffectCompletionMode, SystemCapabilityMode
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.scheduling_identity import WMS_PRODUCTION_PROFILE_IDENTITY
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    NotifyPackageBindingOperationRequest,
    NotifyPackageBindingOperationResult,
)


def _t10_modules() -> SimpleNamespace:
    callback_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.callback_adapter"
    )
    preparation_service = import_module(
        "src.app.runtime.orchestration.services.notify_package_binding_effect_preparation_service"
    )
    definition = import_module("src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.definition")
    effect_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter"
    )
    effect_contract = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_contract"
    )
    handler = import_module("src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.handler")
    intent_adapter = import_module(
        "src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.intent_adapter"
    )
    return SimpleNamespace(
        NotifyPackageBindingCallbackAdapter=callback_adapter.NotifyPackageBindingCallbackAdapter,
        NotifyPackageBindingEffectPreparationService=(preparation_service.NotifyPackageBindingEffectPreparationService),
        CAPABILITY_KEY=definition.CAPABILITY_KEY,
        CONTRACT_VERSION=definition.CONTRACT_VERSION,
        DEFINITION=definition.DEFINITION,
        NotifyPackageBindingEffectAdapter=effect_adapter.NotifyPackageBindingEffectAdapter,
        NotifyPackageBindingDispatchAccepted=effect_contract.NotifyPackageBindingDispatchAccepted,
        NotifyPackageBindingEffectAdmission=effect_contract.NotifyPackageBindingEffectAdmission,
        NotifyPackageBindingEffectPrecondition=effect_contract.NotifyPackageBindingEffectPrecondition,
        NotifyPackageBindingEffectHandler=handler.NotifyPackageBindingEffectHandler,
        NotifyPackageBindingIntentAdapter=intent_adapter.NotifyPackageBindingIntentAdapter,
    )


def _request(
    *,
    dispatch_key: str = "wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
) -> NotifyPackageBindingOperationRequest:
    return NotifyPackageBindingOperationRequest(
        dispatch_key=dispatch_key,
        provider_code="WMS",
        package_id="PKG-001",
        pallet_id="PALLET-001",
        station_code="STATION-001",
        workline_id=7,
        session_id=11,
        trace_id="trace-notify-pkg-binding",
    )


def _admission(modules: SimpleNamespace) -> Any:
    return modules.NotifyPackageBindingEffectAdmission(
        precondition=modules.NotifyPackageBindingEffectPrecondition(
            package_id="PKG-001",
            pallet_id="PALLET-001",
            local_physical_fact_recorded=True,
        ),
        fact_version="runtime-location:v7",
    )


def test_notify_pkg_binding_definition_is_operation_owned_outbox_async_effect() -> None:
    modules = _t10_modules()

    assert (modules.CAPABILITY_KEY, modules.CONTRACT_VERSION) == (
        "wms.fulfillment.notify_pkg_binding",
        "v1",
    )
    assert modules.DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert modules.DEFINITION.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
    assert modules.DEFINITION.input_model is NotifyPackageBindingOperationRequest
    assert modules.DEFINITION.output_model is modules.NotifyPackageBindingDispatchAccepted
    assert modules.DEFINITION.handler_factory is modules.NotifyPackageBindingEffectHandler
    assert modules.DEFINITION.required_ports == ()
    assert modules.DEFINITION.admission_model is modules.NotifyPackageBindingEffectAdmission


def test_intent_adapter_uses_provider_package_and_pallet_as_stable_business_identity() -> None:
    modules = _t10_modules()
    request = _request()
    intent = modules.NotifyPackageBindingIntentAdapter().build_intent(
        request,
        admission=_admission(modules),
        binding_id=23,
        binding_version=5,
    )

    assert intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert intent.operation_key == "WMS:PKG-001:PALLET-001"
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
    modules = _t10_modules()
    request = _request()
    pair_repository = _PairRepository()
    adapter = modules.NotifyPackageBindingEffectAdapter(
        gateway=NotifyPackageBindingDispatchGateway(
            registry=EndpointRegistry(
                {"WMS_PACKAGE_BINDING": "https://wms-v1.example/api/wes/fulfillment/package-binding"}
            )
        )
    )
    db = object()
    intent_log = SimpleNamespace(dispatch_key=request.dispatch_key, effect_status="PROPOSED")
    service = modules.NotifyPackageBindingEffectPreparationService(intent_repository=pair_repository)

    outbox = await service.prepare(
        db,
        request=request,
        intent_log=intent_log,
        adapter=adapter,
    )

    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.dispatch_key == request.dispatch_key
    assert outbox.operation_identity == CONTRACT.identity
    assert outbox.operation_key == "WMS:PKG-001:PALLET-001"
    assert outbox.target_snapshot_json["url"] == ("https://wms-v1.example/api/wes/fulfillment/package-binding")
    assert outbox.provider_profile_identity == WMS_PRODUCTION_PROFILE_IDENTITY
    assert outbox.canonical_payload_bytes == (
        b'{"package_id":"PKG-001","pallet_id":"PALLET-001","station_code":"STATION-001"}'
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
        (False, "PALLET_LOCKED", "REJECTED"),
    ],
)
async def test_callback_adapter_maps_typed_business_result_only_through_reducer(
    accepted: bool,
    reason_code: str | None,
    expected_outcome: str,
) -> None:
    modules = _t10_modules()
    bridge = _RecordingCallbackBridge()
    adapter = modules.NotifyPackageBindingCallbackAdapter(bridge=bridge)
    result = NotifyPackageBindingOperationResult(
        dispatch_key="wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
        package_id="PKG-001",
        pallet_id="PALLET-001",
        accepted=accepted,
        bound_at="2026-07-23T10:00:00Z" if accepted else None,
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
