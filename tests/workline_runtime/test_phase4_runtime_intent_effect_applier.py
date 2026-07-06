"""Phase4 RuntimeIntent effect applier 可执行合约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier


class _RecordingReservationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def apply_runtime_reservation(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status="RESERVED")


class _RecordingLocationEventService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"args": args, **kwargs})
        return SimpleNamespace(id=1)


class _UnexpectedResourceProjectionService:
    async def record_resource_fact(self, **_: Any) -> None:
        raise AssertionError("Phase4 runtime location facts must not be routed to ResourceProjectionService")


def _effect_ctx() -> dict[str, Any]:
    return {
        "db": SimpleNamespace(),
        "session": SimpleNamespace(id=31, trace_id=None, context_json={}),
        "workline": SimpleNamespace(id=41, line_code="LINE-A"),
        "inbox": SimpleNamespace(id=501),
        "trace_id": "trace-phase4-effect-applier",
        "orch_result": SimpleNamespace(),
    }


@pytest.mark.asyncio
async def test_rough_sorter_reservation_intent_uses_bin_cell_claim_contract() -> None:
    """粗分机预约 intent 必须能被 RuntimeIntentEffectApplier 执行为 CLAIM_BIN_CELL。"""

    from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import (
        Phase4SorterInboundRuntimeService,
    )

    plan = Phase4SorterInboundRuntimeService().build_rough_sorter_inbound_plan(
        {
            "request_id": "rough-runtime-executable-001",
            "correlation_id": "corr-rough-executable-001",
            "provider_code": "WMS-A",
            "object_key": "PKG-ROUGH-EXEC-001",
            "bin_code": "BIN-A-01",
            "bin_cell_index": "1",
            "target_cell_code": "CELL-A-01",
            "pkg_code": "PKG-ROUGH-EXEC-001",
            "pallet_id": "PALLET-A-01",
            "station_code": "ROUGH-OUT-01",
            "material_code": "MAT-A",
            "quantity": 1,
            "warehouse_code": "WH-A",
            "source_event_id": "ecs-scan-executable-001",
            "source_version": "ecs.v1",
        }
    )
    reservation_service = _RecordingReservationService()

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        _effect_ctx(),
        [plan.intents[0]],
    )

    assert len(reservation_service.calls) == 1
    call = reservation_service.calls[0]
    assert call["operation"] == "CLAIM_BIN_CELL"
    assert call["payload_json"]["pkg_code"] == "PKG-ROUGH-EXEC-001"
    assert call["payload_json"]["bin_code"] == "BIN-A-01"
    assert call["payload_json"]["bin_cell_index"] == "1"
    assert call["payload_json"]["bin_cell_code"] == "CELL-A-01"


@pytest.mark.asyncio
async def test_runtime_location_event_fact_records_runtime_location_without_resource_projection(monkeypatch) -> None:
    """RUNTIME_LOCATION_EVENT 是 runtime 位置事实，不应进入资源投影枚举。"""

    from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import (
        Phase4SorterInboundRuntimeService,
    )
    from src.app.runtime.orchestration.services import runtime_location_event_service as location_event_module

    location_event_service = _RecordingLocationEventService()
    monkeypatch.setattr(location_event_module, "runtime_location_event_service", location_event_service)
    plan = Phase4SorterInboundRuntimeService().build_rough_sorter_inbound_plan(
        {
            "request_id": "rough-runtime-location-001",
            "correlation_id": "corr-location-001",
            "provider_code": "WMS-A",
            "object_key": "PKG-LOCATION-001",
            "bin_code": "BIN-A-01",
            "bin_cell_index": "1",
            "target_cell_code": "CELL-A-01",
            "pkg_code": "PKG-LOCATION-001",
            "pallet_id": "PALLET-A-01",
            "station_code": "ROUGH-OUT-01",
            "material_code": "MAT-A",
            "quantity": 1,
            "warehouse_code": "WH-A",
            "source_event_id": "ecs-location-001",
            "source_version": "ecs.v1",
        }
    )

    await RuntimeIntentEffectApplier(resource_projection_service=_UnexpectedResourceProjectionService()).apply(
        _effect_ctx(),
        [plan.intents[1]],
    )

    assert len(location_event_service.calls) == 1
    call = location_event_service.calls[0]
    assert call["object_type"] == "PACKAGE"
    assert call["object_key"] == "PKG-LOCATION-001"
    assert call["location_scope"] == "CELL"
    assert call["location_code"] == "CELL-A-01"
    assert call["business_step"] == "LOCAL_PHYSICAL_FACT"
    assert call["source"] == "PHASE4_SORTER_INBOUND"
    assert call["idempotency_key"] == "phase4:rough-runtime-location-001:location-fact"
    assert call["provider_code"] == "WMS-A"
    assert call["auto_commit"] is False


@pytest.mark.asyncio
async def test_reconciliation_evidence_fact_records_runtime_location_evidence(monkeypatch) -> None:
    """RECONCILIATION_EVIDENCE 应作为 runtime 对账 evidence 落库，而不是走资源事实枚举。"""

    from src.app.runtime.capabilities.phase4.smt_ng_wms_reconciliation_runtime_service import (
        SmtNgWmsReconciliationRuntimeService,
    )
    from src.app.runtime.orchestration.services import runtime_location_event_service as location_event_module

    location_event_service = _RecordingLocationEventService()
    monkeypatch.setattr(location_event_module, "runtime_location_event_service", location_event_service)
    plan = SmtNgWmsReconciliationRuntimeService().build_reconciliation_plan(
        {
            "scenario": "WMS_REJECT",
            "provider_code": "WMS-A",
            "correlation_id": "corr-reconciliation-001",
            "object_type": "PACKAGE",
            "object_key": "PKG-RECONCILIATION-001",
            "source_event_id": "wms-reconciliation-001",
            "source_version": "wms.v2",
            "external_reference_type": "WMS_DOCUMENT",
            "external_reference_value": "DOC-001",
        }
    )

    await RuntimeIntentEffectApplier(resource_projection_service=_UnexpectedResourceProjectionService()).apply(
        _effect_ctx(),
        [plan.intents[0]],
    )

    assert len(location_event_service.calls) == 1
    call = location_event_service.calls[0]
    assert call["object_type"] == "PACKAGE"
    assert call["object_key"] == "PKG-RECONCILIATION-001"
    assert call["location_scope"] == "RECONCILIATION"
    assert call["location_code"] == "WMS_REJECTED_LOCAL_FACT"
    assert call["business_step"] == "RECONCILIATION_EVIDENCE"
    assert call["source"] == "PHASE4_RECONCILIATION"
    assert call["external_reference_type"] == "WMS_DOCUMENT"
    assert call["external_reference_value"] == "DOC-001"
    assert call["idempotency_key"] == "phase4:wms-reconciliation-001:reconciliation-evidence"
    assert call["auto_commit"] is False
