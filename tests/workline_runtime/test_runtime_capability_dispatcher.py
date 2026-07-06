"""RuntimeCapabilityDispatcher target-state routing contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import Phase4SorterInboundRuntimeService
from src.app.runtime.capability_dispatcher import (
    RuntimeCapabilityCatalog,
    RuntimeCapabilityDefinition,
    RuntimeCapabilityDispatcher,
    RuntimeCapabilityRouteError,
    RuntimeCapabilityUndeclaredError,
)
from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
from src.app.runtime.normalization.normalizers import normalize_inbox_input
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorService
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind


@asynccontextmanager
async def _noop_lock():
    yield


@dataclass(frozen=True)
class _NormalizedInput:
    runtime_capability: str | None
    canonical_event_type: str | None = None


def _profile(*, effect_capabilities: list[str] | None = None) -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-07-06.phase5",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        runtime_capabilities_effect=effect_capabilities or [],
        inbound_normalizers_event=["DEVICE_SCAN"],
        inbound_normalizers_result=["COMMAND_RESULT"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30 if effect_capabilities else None,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/phase5",
        fixture_set_required_cases=["success"],
    )


def _rough_sorter_inbound_payload() -> dict[str, object]:
    return {
        "callback_type": "WMS_ROUGH_SORTER_INBOUND",
        "runtime_capability": "rough_sorter_inbound",
        "request_id": "rough-runtime-dispatch-001",
        "correlation_id": "corr-rough-dispatch-001",
        "source_system": "WMS",
        "provider_code": "WMS-A",
        "object_key": "PKG-ROUGH-DISPATCH-001",
        "bin_code": "BIN-A-01",
        "bin_cell_index": "1",
        "target_cell_code": "CELL-A-01",
        "pkg_code": "PKG-ROUGH-DISPATCH-001",
        "pallet_id": "PALLET-A-01",
        "station_code": "ROUGH-OUT-01",
        "material_code": "MAT-A",
        "quantity": 1,
        "warehouse_code": "WH-A",
        "source_event_id": "wms-rough-dispatch-001",
        "source_version": "wms.phase5",
    }


def _rough_sorter_inbound_envelope_payload() -> dict[str, object]:
    return {
        "callback_type": "WMS_ROUGH_SORTER_INBOUND",
        "runtime_capability": "rough_sorter_inbound",
        "source_system": "WMS",
        "source_event_id": "wms-rough-dispatch-001",
        "source_version": "wms.phase5",
        "occurred_at": "2026-07-06T08:00:00Z",
        "request_id": "REQ-ROUGH-INBOUND-001",
        "timestamp": "2026-07-06T08:00:01Z",
        "signature": "test-signature",
        "trace_id": "trace-rough-dispatch",
        "data": {
            "request_id": "rough-runtime-dispatch-001",
            "correlation_id": "corr-rough-dispatch-001",
            "provider_code": "WMS-A",
            "object_key": "PKG-ROUGH-DISPATCH-001",
            "bin_code": "BIN-A-01",
            "bin_cell_index": "1",
            "target_cell_code": "CELL-A-01",
            "pkg_code": "PKG-ROUGH-DISPATCH-001",
            "pallet_id": "PALLET-A-01",
            "station_code": "ROUGH-OUT-01",
            "material_code": "MAT-A",
            "quantity": 1,
            "warehouse_code": "WH-A",
        },
    }


def test_dispatcher_routes_declared_capability_to_static_handler() -> None:
    """已声明 capability 通过静态 catalog 路由到 handler。"""

    calls: list[_NormalizedInput] = []

    def handle_sorter(normalized: _NormalizedInput) -> dict[str, object]:
        calls.append(normalized)
        return {"legacy_plugin_entry_used": False, "capability": normalized.runtime_capability}

    catalog = RuntimeCapabilityCatalog(
        [
            RuntimeCapabilityDefinition(
                capability_key="sorter_inbound",
                contract_capability="WmsFulfillmentPort.notify_pkg_binding",
                handler=handle_sorter,
            )
        ]
    )
    dispatcher = RuntimeCapabilityDispatcher(catalog)

    result = dispatcher.dispatch(
        _NormalizedInput(runtime_capability="sorter_inbound", canonical_event_type="DEVICE_SCAN"),
        profile=_profile(effect_capabilities=["WmsFulfillmentPort.notify_pkg_binding"]),
    )

    assert result == {"legacy_plugin_entry_used": False, "capability": "sorter_inbound"}
    assert calls == [_NormalizedInput(runtime_capability="sorter_inbound", canonical_event_type="DEVICE_SCAN")]


def test_runtime_inbox_normalizer_dispatches_to_phase4_runtime_service() -> None:
    """RuntimeInbox -> InboundNormalizerRegistry -> dispatcher -> Phase4 service 成功链路。"""

    class RuntimeInboxPort:
        pass

    class RuntimeInboxNormalizer:
        def normalize(self, inbox: object) -> object:
            return normalize_inbox_input(inbox, trace_id="trace-rough-dispatch")

    inbound_registry = InboundNormalizerRegistry()
    inbound_registry.register(RuntimeInboxPort, RuntimeInboxNormalizer)
    normalized = inbound_registry.get(RuntimeInboxPort).normalize(
        SimpleNamespace(kind="EXTERNAL_HTTP", payload_json=_rough_sorter_inbound_payload())
    )
    service = Phase4SorterInboundRuntimeService()
    catalog = RuntimeCapabilityCatalog(
        [
            RuntimeCapabilityDefinition(
                capability_key="rough_sorter_inbound",
                contract_capability="WmsFulfillmentPort.notify_pkg_binding",
                contract_capabilities=(
                    "WmsFulfillmentPort.notify_pkg_binding",
                    "WmsInventoryTransactionPort.confirm_inbound",
                ),
                handler=lambda normalized_input: service.build_rough_sorter_inbound_plan(normalized_input.payload),
            )
        ]
    )
    dispatcher = RuntimeCapabilityDispatcher(catalog)

    plan = dispatcher.dispatch(
        normalized,
        profile=_profile(
            effect_capabilities=[
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
            ]
        ),
    )

    assert plan.legacy_plugin_entry_used is False
    assert [intent.kind for intent in plan.intents] == [
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    assert plan.effect_contracts["WmsFulfillmentPort.notify_pkg_binding"]["payload"]["package_id"] == (
        "PKG-ROUGH-DISPATCH-001"
    )
    assert plan.effect_contracts["WmsInventoryTransactionPort.confirm_inbound"]["payload"]["warehouse_code"] == "WH-A"


@pytest.mark.asyncio
async def test_orchestrator_process_inbox_uses_runtime_capability_dispatcher_for_external_payload() -> None:
    """生产 OrchestratorService 必须从普通 external payload 触发 runtime capability。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=101, contract_version="rough_sorter.v1"),
        workline=SimpleNamespace(contract_version="rough_sorter.v1", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            kind="EXTERNAL_HTTP", payload_json=_rough_sorter_inbound_envelope_payload(), trace_id="trace-rough"
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-rough",
    )

    assert result.success is True
    assert result.error is None
    assert [intent.kind for intent in result.intents or []] == [
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]


@pytest.mark.asyncio
async def test_orchestrator_does_not_trust_raw_external_runtime_intents_without_profile() -> None:
    """外部 payload 里的 raw intents 不得绕过 provider profile admission。"""

    def reject_profile(_normalized_input: object) -> object:
        raise RuntimeCapabilityUndeclaredError("provider profile required for runtime capability: rough_sorter_inbound")

    payload = {
        **_rough_sorter_inbound_envelope_payload(),
        "runtime_intents": [
            RuntimeIntent.external_request(
                dispatch_key="unsafe:raw-intent",
                target_code="WMS_FULFILLMENT",
                payload={"package_id": "PKG-BYPASS"},
                timeout_seconds=30,
                source_system="WMS",
            ).model_dump(mode="json")
        ],
    }
    orchestrator = OrchestratorService(
        lock_provider=lambda _lock_key: _noop_lock(),
        runtime_profile_resolver=reject_profile,
    )

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=102, contract_version="rough_sorter.v1"),
        workline=SimpleNamespace(contract_version="rough_sorter.v1", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(kind="EXTERNAL_HTTP", payload_json=payload, trace_id="trace-raw-intent"),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-raw-intent",
    )

    assert result.success is False
    assert "provider profile required" in str(result.error)


def test_dispatcher_rejects_unknown_capability_without_fallback() -> None:
    """未知 capability 必须 fail closed，不能 fallback 到 null plugin。"""

    dispatcher = RuntimeCapabilityDispatcher(RuntimeCapabilityCatalog([]))

    with pytest.raises(RuntimeCapabilityRouteError, match="unknown runtime capability"):
        dispatcher.dispatch(_NormalizedInput(runtime_capability="missing"), profile=_profile())


def test_dispatcher_requires_provider_profile_for_effect_capability() -> None:
    """漏传 provider profile 时必须 fail closed，不能绕过 effect admission。"""

    catalog = RuntimeCapabilityCatalog(
        [
            RuntimeCapabilityDefinition(
                capability_key="sorter_inbound",
                contract_capability="WmsFulfillmentPort.notify_pkg_binding",
                handler=lambda normalized: normalized,
            )
        ]
    )
    dispatcher = RuntimeCapabilityDispatcher(catalog)

    with pytest.raises(RuntimeCapabilityUndeclaredError, match="provider profile required"):
        dispatcher.dispatch(_NormalizedInput(runtime_capability="sorter_inbound"))


def test_dispatcher_rejects_undeclared_provider_capability() -> None:
    """provider profile 未声明目标 effect capability 时必须拒绝。"""

    catalog = RuntimeCapabilityCatalog(
        [
            RuntimeCapabilityDefinition(
                capability_key="sorter_inbound",
                contract_capability="WmsFulfillmentPort.notify_pkg_binding",
                contract_capabilities=(
                    "WmsFulfillmentPort.notify_pkg_binding",
                    "WmsInventoryTransactionPort.confirm_inbound",
                ),
                handler=lambda normalized: normalized,
            )
        ]
    )
    dispatcher = RuntimeCapabilityDispatcher(catalog)

    with pytest.raises(RuntimeCapabilityUndeclaredError, match=r"WmsInventoryTransactionPort\.confirm_inbound"):
        dispatcher.dispatch(
            _NormalizedInput(runtime_capability="sorter_inbound"),
            profile=_profile(effect_capabilities=["WmsFulfillmentPort.notify_pkg_binding"]),
        )
