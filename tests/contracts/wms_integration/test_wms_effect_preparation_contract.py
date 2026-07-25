"""三个 WMS EFFECT operation 共享 preparation 基础设施的特征合同。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from src.app.runtime.orchestration.services.wms_effect_preparation_service import WmsEffectPreparationService
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.contract import (
    CONTRACT as FULL_BOX_EXCHANGE_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.effect_adapter import (
    FullBoxExchangeEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.gateway import (
    FullBoxExchangeDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.contract import (
    CONTRACT as NOTIFY_PKG_BINDING_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
    NotifyPackageBindingEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.contract import (
    CONTRACT as CONFIRM_INBOUND_CONTRACT,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_adapter import (
    ConfirmInboundEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.gateway import ConfirmInboundDispatchGateway
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.confirm_inbound_operation import (
    ConfirmInboundOperationRequest,
    ConfirmInboundOperationResult,
)
from src.app.wms_integration.ports.full_box_exchange_operation import (
    FullBoxExchangeOperationRequest,
    FullBoxExchangeOperationResult,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    NotifyPackageBindingOperationRequest,
    NotifyPackageBindingOperationResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from src.app.runtime.system_capabilities.wms.contracts import WmsOperationContract


class _PairRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    async def add_proposed_pair(self, db: object, *, intent_log: object, outbox: object) -> None:
        self.calls.append((db, intent_log, outbox))


class _EnvelopeMutationAdapter:
    """模拟 adapter 返回结构合法但关联 identity 被错误替换的包络。"""

    def __init__(self, adapter: object, **mutations: object) -> None:
        self._adapter = adapter
        self._mutations = mutations

    def build_envelope(self, request: object, *, idempotency_key: str):
        envelope = self._adapter.build_envelope(request, idempotency_key=idempotency_key)  # type: ignore[attr-defined]
        return replace(envelope, **self._mutations)


@dataclass(frozen=True)
class _OperationCase:
    name: str
    contract: WmsOperationContract
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    request_factory: Callable[[], BaseModel]
    adapter_factory: Callable[[EndpointRegistry], object]
    operation_key: str
    canonical_payload: bytes


CASES = (
    _OperationCase(
        name="confirm_inbound",
        contract=CONFIRM_INBOUND_CONTRACT,
        request_model=ConfirmInboundOperationRequest,
        result_model=ConfirmInboundOperationResult,
        request_factory=lambda: ConfirmInboundOperationRequest(
            dispatch_key="wms-confirm-inbound:WMS:PKG-001",
            inbound_key="PKG-001",
            material_code="MAT-001",
            quantity=Decimal("1.25"),
            warehouse_code="WH-01",
            owner_code="OWNER-01",
            lot_no="LOT-01",
            workline_id=7,
            session_id=11,
            trace_id="trace-confirm-inbound",
        ),
        adapter_factory=lambda registry: ConfirmInboundEffectAdapter(
            gateway=ConfirmInboundDispatchGateway(registry=registry)
        ),
        operation_key="PKG-001",
        canonical_payload=(
            b'{"dispatch_key":"wms-confirm-inbound:WMS:PKG-001","inbound_key":"PKG-001","lot_no":"LOT-01",'
            b'"material_code":"MAT-001","owner_code":"OWNER-01","quantity":"1.25","warehouse_code":"WH-01"}'
        ),
    ),
    _OperationCase(
        name="full_box_exchange",
        contract=FULL_BOX_EXCHANGE_CONTRACT,
        request_model=FullBoxExchangeOperationRequest,
        result_model=FullBoxExchangeOperationResult,
        request_factory=lambda: FullBoxExchangeOperationRequest(
            dispatch_key="wms-full-box-exchange:WMS:RACK-001:EMPTY-001:FULL-001",
            provider_code="WMS",
            rack_id="RACK-001",
            empty_box_id="EMPTY-001",
            full_box_id="FULL-001",
            workline_id=7,
            session_id=11,
            trace_id="trace-full-box-exchange",
        ),
        adapter_factory=lambda registry: FullBoxExchangeEffectAdapter(
            gateway=FullBoxExchangeDispatchGateway(registry=registry)
        ),
        operation_key="WMS:RACK-001:EMPTY-001:FULL-001",
        canonical_payload=(
            b'{"dispatch_key":"wms-full-box-exchange:WMS:RACK-001:EMPTY-001:FULL-001","empty_box_id":"EMPTY-001",'
            b'"full_box_id":"FULL-001","rack_id":"RACK-001"}'
        ),
    ),
    _OperationCase(
        name="notify_pkg_binding",
        contract=NOTIFY_PKG_BINDING_CONTRACT,
        request_model=NotifyPackageBindingOperationRequest,
        result_model=NotifyPackageBindingOperationResult,
        request_factory=lambda: NotifyPackageBindingOperationRequest(
            dispatch_key="wms-notify-pkg-binding:WMS:PKG-001:PALLET-001",
            provider_code="WMS",
            package_id="PKG-001",
            pallet_id="PALLET-001",
            station_code="STATION-001",
            workline_id=7,
            session_id=11,
            trace_id="trace-notify-pkg-binding",
        ),
        adapter_factory=lambda registry: NotifyPackageBindingEffectAdapter(
            gateway=NotifyPackageBindingDispatchGateway(registry=registry)
        ),
        operation_key="WMS:PKG-001:PALLET-001",
        canonical_payload=(
            b'{"dispatch_key":"wms-notify-pkg-binding:WMS:PKG-001:PALLET-001","package_id":"PKG-001",'
            b'"pallet_id":"PALLET-001","station_code":"STATION-001"}'
        ),
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_shared_preparation_preserves_typed_operation_and_outbox_invariants(case: _OperationCase) -> None:
    request = case.request_factory()
    registry = EndpointRegistry({case.contract.target_code: f"https://wms-v1.example{case.contract.endpoint_path}"})
    pair_repository = _PairRepository()
    service = WmsEffectPreparationService(intent_repository=pair_repository)
    adapter = case.adapter_factory(registry)
    db = object()
    intent_log = SimpleNamespace(
        dispatch_key=request.dispatch_key,
        idempotency_key=f"intent-{case.name}-001",
        effect_status="PROPOSED",
        capability_key=case.contract.identity.removesuffix("@v1"),
        capability_contract_version="v1",
        operation_identity=case.operation_key,
    )

    outbox = await service.prepare(
        db,
        operation=case.contract,
        request=request,
        intent_log=intent_log,
        adapter=adapter,
    )

    assert case.contract.request_model is case.request_model
    assert case.contract.result_model is case.result_model
    assert case.contract.retry_policy.model_dump(mode="json") == {
        "max_attempts": 3,
        "backoff_seconds": [1.0, 4.0],
    }
    assert outbox.status == SystemOutboxStatus.NEW
    assert outbox.dispatch_key == request.dispatch_key
    assert outbox.idempotency_key == f"intent-{case.name}-001"
    assert outbox.operation_identity == case.contract.identity
    assert outbox.operation_key == case.operation_key
    assert outbox.provider_profile_identity == "wms.2026-07-06.material-flow.sandbox"
    assert outbox.target_code == case.contract.target_code
    assert outbox.target_snapshot_json["url"] == f"https://wms-v1.example{case.contract.endpoint_path}"
    assert outbox.canonical_payload_bytes == case.canonical_payload
    assert outbox.payload_hash == hashlib.sha256(case.canonical_payload).hexdigest()
    assert outbox.provider_profile_hash
    assert outbox.binding_revision
    assert outbox.target_snapshot_hash
    assert outbox.auth_scheme == "HMAC_SHA256"
    assert outbox.credential_reference == "secret://wms/material-flow-sandbox-hmac@v2"
    assert intent_log.status_binding_snapshot_json["provider_profile_identity"] == outbox.provider_profile_identity
    assert len(intent_log.status_binding_snapshot_hash) == 64
    assert pair_repository.calls == [(db, intent_log, outbox)]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ({"idempotency_key": "adapter-replaced-idempotency-key"}, "envelope idempotency_key mismatch"),
        ({"dispatch_key": "adapter-replaced-dispatch-key"}, "envelope dispatch_key mismatch"),
    ),
    ids=("idempotency-key", "dispatch-key"),
)
async def test_shared_preparation_rejects_adapter_identity_mutation_without_persistence(
    case: _OperationCase,
    mutation: dict[str, str],
    expected_error: str,
) -> None:
    request = case.request_factory()
    pair_repository = _PairRepository()
    service = WmsEffectPreparationService(intent_repository=pair_repository)
    adapter = _EnvelopeMutationAdapter(
        case.adapter_factory(
            EndpointRegistry({case.contract.target_code: f"https://wms-v1.example{case.contract.endpoint_path}"})
        ),
        **mutation,
    )
    intent_log = SimpleNamespace(
        dispatch_key=request.dispatch_key,
        idempotency_key=f"intent-{case.name}-001",
        effect_status="PROPOSED",
        capability_key=case.contract.identity.removesuffix("@v1"),
        capability_contract_version="v1",
        operation_identity=case.operation_key,
    )

    with pytest.raises(ValueError, match=rf"^{case.name} {expected_error}$"):
        await service.prepare(
            object(),
            operation=case.contract,
            request=request,
            intent_log=intent_log,
            adapter=adapter,
        )

    assert pair_repository.calls == []
    assert not hasattr(intent_log, "status_binding_snapshot_json")
    assert not hasattr(intent_log, "status_binding_snapshot_hash")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_shared_preparation_preserves_operation_specific_validation_messages(case: _OperationCase) -> None:
    request = case.request_factory()
    service = WmsEffectPreparationService(intent_repository=_PairRepository())
    adapter = case.adapter_factory(EndpointRegistry({case.contract.target_code: "https://wms-v1.example/effect"}))

    with pytest.raises(ValueError, match=rf"^{case.name} intent/outbox dispatch_key mismatch$"):
        await service.prepare(
            object(),
            operation=case.contract,
            request=request,
            intent_log=SimpleNamespace(dispatch_key="different", idempotency_key="intent-001"),
            adapter=adapter,
        )

    with pytest.raises(ValueError, match=rf"^{case.name} intent requires persisted idempotency_key$"):
        await service.prepare(
            object(),
            operation=case.contract,
            request=request,
            intent_log=SimpleNamespace(dispatch_key=request.dispatch_key, idempotency_key=" "),
            adapter=adapter,
        )
