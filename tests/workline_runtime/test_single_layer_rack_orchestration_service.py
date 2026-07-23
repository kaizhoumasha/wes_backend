"""Single-layer rack orchestration station-claim regressions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.capabilities.material_flow import single_layer_rack_orchestration_service as service_module
from src.app.runtime.capabilities.material_flow.single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationDecisionCode,
    SingleLayerRackOrchestrationService,
    _is_active_station_claim_outbox,
)
from src.app.sys.models import SystemOutbox, SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.services.transport_contract import (
    DEFAULT_RACK_OPERATION_ENDPOINT,
    WmsTransportContractService,
    freeze_legacy_transport_binding,
)


def test_station_claim_active_status_accepts_system_outbox_status_enum() -> None:
    """station claim 幂等冲突判断必须接受 SQLModel 返回的 Enum 状态。"""

    assert _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.NEW, finished_at=None)) is True
    assert (
        _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.RETRY_WAIT, finished_at=None)) is True
    )


def test_station_claim_does_not_treat_finished_retry_wait_as_active() -> None:
    assert (
        _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.RETRY_WAIT, finished_at=object()))
        is False
    )


class _ReplayOutboxRepository:
    def __init__(self, outbox: SystemOutbox) -> None:
        self.outbox = outbox
        self.calls: list[str] = []

    async def get_by_dispatch_key_for_update(self, _db: Any, dispatch_key: str) -> SystemOutbox | None:
        self.calls.append(dispatch_key)
        if dispatch_key == self.outbox.dispatch_key:
            return self.outbox
        return None


class _ReplayStationLeaseService:
    def __init__(self) -> None:
        self.claim_calls: list[dict[str, Any]] = []

    async def get_station_lease_status(self, _db: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            available=True,
            reason_code=None,
            workline_code="WL-SMT-REPLAY",
            position_code="STATION-REPLAY",
            active_rack_code=None,
            active_session_id=None,
            active_dispatch_key=None,
        )

    async def claim_station_dispatch_lease(self, _db: Any, **kwargs: Any) -> SystemOutbox | None:
        self.claim_calls.append(kwargs)
        raise AssertionError("existing dispatch replay must not create a new station lease")


def _persisted_single_layer_outbox(*, dispatch_key: str) -> SystemOutbox:
    transport = WmsTransportContractService()
    request = transport.build_single_layer_rack_operation_request(
        business_demand_key="DEMAND-REPLAY",
        workline_code="WL-SMT-REPLAY",
        endpoint_code="STATION-REPLAY",
        rack_kind="SINGLE_LAYER",
        operation_type=SingleLayerRackOrchestrationService.DEFAULT_OPERATION_TYPE,
        payload=SingleLayerRackOrchestrationService._rack_operation_payload(
            {},
            station_code="STATION-REPLAY",
        ),
        timeout_seconds=SingleLayerRackOrchestrationService.DEFAULT_TIMEOUT_SECONDS,
        dispatch_key=dispatch_key,
        target_code=DEFAULT_RACK_OPERATION_ENDPOINT,
        trace_id="trace-frozen-replay",
    )
    envelope = SingleLayerRackOrchestrationService._dispatch_envelope(
        request,
        workline_id=45,
        session_id=300,
        trace_id="trace-frozen-replay",
    )
    persisted_binding = freeze_legacy_transport_binding(
        operation_identity="wms.transport.rack@v1",
        target_code=DEFAULT_RACK_OPERATION_ENDPOINT,
        registry=EndpointRegistry({DEFAULT_RACK_OPERATION_ENDPOINT: "https://old-wms.example/rack"}),
    )
    return SystemOutbox(
        **persisted_binding.as_persisted_fields(),
        id=901,
        session_id=envelope.session_id,
        workline_id=envelope.workline_id,
        operation_domain=envelope.operation_domain,
        operation_key=envelope.operation_key,
        dispatch_type=envelope.dispatch_type,
        dispatch_key=envelope.dispatch_key,
        target_type=envelope.target_type,
        payload_json=envelope.payload_json,
        canonical_payload_bytes=envelope.canonical_payload_bytes,
        payload_hash=envelope.payload_hash,
        status=SystemOutboxStatus.NEW,
        trace_id=envelope.trace_id,
    )


async def _ready_workline(*_args: Any, **_kwargs: Any) -> bool:
    return True


@pytest.mark.asyncio
async def test_single_layer_replay_after_binding_rotation_uses_persisted_snapshot_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_key = "single-layer:frozen-binding-replay"
    existing_outbox = _persisted_single_layer_outbox(dispatch_key=dispatch_key)
    repository = _ReplayOutboxRepository(existing_outbox)
    station_lease = _ReplayStationLeaseService()
    service = SingleLayerRackOrchestrationService(
        station_lease_service=station_lease,  # type: ignore[arg-type]
        outbox_repository=repository,  # type: ignore[arg-type]
        transport_contract_service=WmsTransportContractService(),
        smt_inbound_handoff_service=None,
    )
    monkeypatch.setattr(service_module.workline_runtime_status_projection_service, "is_ready", _ready_workline)

    def fail_current_binding_lookup(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("existing dispatch replay must not resolve the current binding")

    monkeypatch.setattr(service_module, "freeze_legacy_transport_binding", fail_current_binding_lookup)

    decision = await service.plan_single_layer_rack_dispatch(
        object(),  # type: ignore[arg-type]
        business_demand_key="DEMAND-REPLAY",
        demand_type="SUPPLY_RACK",
        workline=SimpleNamespace(id=45, line_code="WL-SMT-REPLAY"),
        session=SimpleNamespace(id=300),
        station_code="STATION-REPLAY",
        dispatch_key=dispatch_key,
        target_code=DEFAULT_RACK_OPERATION_ENDPOINT,
        trace_id="trace-frozen-replay",
        payload={},
    )

    assert decision.decision is SingleLayerRackOrchestrationDecisionCode.DISPATCH_WMS
    assert decision.diagnostics == {"outbox_id": existing_outbox.id}
    assert existing_outbox.target_snapshot_json == {
        "code": DEFAULT_RACK_OPERATION_ENDPOINT,
        "http_method": "POST",
        "timeout_seconds": 30,
        "url": "https://old-wms.example/rack",
    }
    assert repository.calls == [existing_outbox.dispatch_key]
    assert station_lease.claim_calls == []


@pytest.mark.asyncio
async def test_single_layer_replay_after_binding_rotation_rejects_changed_immutable_request_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_key = "single-layer:frozen-binding-conflict"
    existing_outbox = _persisted_single_layer_outbox(dispatch_key=dispatch_key)
    repository = _ReplayOutboxRepository(existing_outbox)
    station_lease = _ReplayStationLeaseService()
    service = SingleLayerRackOrchestrationService(
        station_lease_service=station_lease,  # type: ignore[arg-type]
        outbox_repository=repository,  # type: ignore[arg-type]
        transport_contract_service=WmsTransportContractService(),
        smt_inbound_handoff_service=None,
    )
    monkeypatch.setattr(service_module.workline_runtime_status_projection_service, "is_ready", _ready_workline)

    def fail_current_binding_lookup(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("existing dispatch replay must not resolve the current binding")

    monkeypatch.setattr(service_module, "freeze_legacy_transport_binding", fail_current_binding_lookup)

    with pytest.raises(ValueError, match="canonical_payload_bytes differs from request"):
        await service.plan_single_layer_rack_dispatch(
            object(),  # type: ignore[arg-type]
            business_demand_key="DEMAND-REPLAY",
            demand_type="SUPPLY_RACK",
            workline=SimpleNamespace(id=45, line_code="WL-SMT-REPLAY"),
            session=SimpleNamespace(id=300),
            station_code="STATION-REPLAY",
            rack_code="RACK-CHANGED",
            dispatch_key=dispatch_key,
            target_code=DEFAULT_RACK_OPERATION_ENDPOINT,
            trace_id="trace-frozen-replay",
            payload={},
        )

    assert station_lease.claim_calls == []
