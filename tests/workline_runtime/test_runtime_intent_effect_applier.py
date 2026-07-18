"""material-flow RuntimeIntent effect applier 可执行合约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
from src.app.runtime.system_capabilities.definition import EffectCompletionMode
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.app.runtime.workline_plugins.schema import ResourceBoundary, WorklinePluginSchema


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
        raise AssertionError("material-flow runtime location facts must not be routed to ResourceProjectionService")


def _effect_ctx() -> dict[str, Any]:
    return {
        "db": SimpleNamespace(),
        "session": SimpleNamespace(id=31, trace_id=None, context_json={}),
        "workline": SimpleNamespace(id=41, line_code="LINE-A"),
        "inbox": SimpleNamespace(id=501),
        "trace_id": "trace-runtime-effect-applier",
        "orch_result": SimpleNamespace(),
    }


class _RecordingSystemCapabilityEffectService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, RuntimeIntent]] = []

    async def apply(self, ctx: object, intent: RuntimeIntent) -> SimpleNamespace:
        self.calls.append((ctx, intent))
        return SimpleNamespace(
            outcome=Success(payload={"accepted": True}),
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            durably_accepted=False,
            remote_completed=True,
            idempotent_replay=False,
            retryable=False,
        )


class _StaleMaterialEffectService(_RecordingSystemCapabilityEffectService):
    async def apply(self, ctx: object, intent: RuntimeIntent) -> SimpleNamespace:
        self.calls.append((ctx, intent))
        if intent.capability_key != "material_flow.material_unit_write":
            raise AssertionError("BusinessReject 后不得执行后续 device effect")
        return SimpleNamespace(
            outcome=BusinessReject(reason_code="STALE_PRECONDITION", message="material fact changed"),
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            durably_accepted=False,
            remote_completed=False,
            idempotent_replay=False,
            retryable=False,
            evidence=SimpleNamespace(outcome_kind="business_reject"),
        )


def test_resource_wait_schema_requires_subject_projection_from_same_boundary() -> None:
    schema = WorklinePluginSchema(
        resource_boundaries=(
            ResourceBoundary("A", "SINGLE_LAYER", "SUBJECT_A", "OP_A", "PROJECTION_A", "STATION"),
            ResourceBoundary("B", "FIVE_LAYER", "SUBJECT_B", "OP_B", "PROJECTION_B", "STATION"),
        )
    )

    schema.validate_resource_wait_subject(subject_type="SUBJECT_A", projection_type="PROJECTION_A")
    with pytest.raises(ValueError, match="same resource boundary"):
        schema.validate_resource_wait_subject(subject_type="SUBJECT_A", projection_type="PROJECTION_B")


@pytest.mark.asyncio
async def test_effect_applier_rejects_cross_boundary_resource_wait(monkeypatch) -> None:
    import src.app.runtime.orchestration.runtime_intent_effects as effect_module

    schema = WorklinePluginSchema(
        resource_boundaries=(
            ResourceBoundary("A", "SINGLE_LAYER", "SUBJECT_A", "OP_A", "PROJECTION_A", "STATION"),
            ResourceBoundary("B", "FIVE_LAYER", "SUBJECT_B", "OP_B", "PROJECTION_B", "STATION"),
        )
    )
    requested_identities: list[tuple[str | None, str | None]] = []

    def get_definition(plugin_key: str | None, contract_version: str | None = None) -> SimpleNamespace:
        requested_identities.append((plugin_key, contract_version))
        return SimpleNamespace(schema=schema)

    monkeypatch.setattr(effect_module, "get_workline_capability_definition", get_definition)
    rejected: list[str] = []
    applier = RuntimeIntentEffectApplier()

    async def reject(*_args: Any, contract_error: str, **_kwargs: Any) -> RuntimeIntentEffectResult:
        rejected.append(contract_error)
        return RuntimeIntentEffectResult.processed()

    monkeypatch.setattr(applier, "_reject_resource_wait_subject_contract", reject)
    ctx = _effect_ctx()
    ctx["session"].plugin_key = "demo"
    ctx["session"].contract_version = "v2"
    ctx["workline"].plugin_key = "demo"
    ctx["workline"].contract_version = "v3"
    intent = RuntimeIntent.resource_wait(
        subject_type="SUBJECT_A",
        subject_key="A-1",
        projection_type="PROJECTION_B",
        reason_code="WAIT",
        message="wait",
    )

    result = await applier._apply_resource_wait(ctx, intent)

    assert result.disposition.value == "PROCESSED"
    assert requested_identities == [("demo", "v2")]
    assert rejected == ["RESOURCE_WAIT subject/projection must belong to the same resource boundary"]


@pytest.mark.asyncio
async def test_system_capability_intent_uses_one_generic_effect_service_branch() -> None:
    service = _RecordingSystemCapabilityEffectService()
    intent = RuntimeIntent.system_capability(
        capability_key="material_flow.material_unit_write",
        contract_version="v1",
        operation_key="scan:PKG-001:create",
        payload={"operation": "CREATE", "pkg_code": "PKG-001"},
        precondition={"expected_absent": True},
        fact_version="material-unit:v0",
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 7, "binding_version": 2},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )
    ctx = _effect_ctx()

    await RuntimeIntentEffectApplier(system_capability_effect_service=service).apply(ctx, [intent])

    assert service.calls == [(ctx, intent)]
    assert ctx["system_capability_outcomes"][0].outcome.kind == "success"


@pytest.mark.asyncio
async def test_stale_material_effect_short_circuits_following_device_effects() -> None:
    service = _StaleMaterialEffectService()
    common = {
        "timeout_seconds": 5,
        "creator_authority": "WORKLINE_PLUGIN",
        "authorization_policy": "PLUGIN_DECLARED_CAPABILITY",
        "binding_snapshot": {"binding_id": 7, "binding_version": 2},
        "provider_snapshot": {"provider_code": "RUNTIME", "profile": "runtime"},
    }
    material = RuntimeIntent.system_capability(
        capability_key="material_flow.material_unit_write",
        contract_version="v1",
        operation_key="scan:PKG-001:create",
        payload={"operation": "CREATE", "pkg_code": "PKG-001"},
        precondition={"expected_absent": True},
        fact_version="material-unit:v0",
        **common,
    )
    device = RuntimeIntent.system_capability(
        capability_key="device.device_command_write",
        contract_version="v1",
        operation_key="scan:PKG-001:dispatch",
        payload={"target_device_id": 71, "action": "PICK_AND_PUT"},
        precondition={"expected_available": True},
        fact_version="device:v1",
        **common,
    )
    ctx = _effect_ctx()
    ctx["db"] = SimpleNamespace(added=[])

    await RuntimeIntentEffectApplier(system_capability_effect_service=service).apply(ctx, [material, device])

    assert [intent.capability_key for _, intent in service.calls] == ["material_flow.material_unit_write"]
    assert [result.outcome.kind for result in ctx["system_capability_outcomes"]] == ["business_reject"]
    assert ctx["system_capability_outcomes"][0].evidence.outcome_kind == "business_reject"
    assert ctx["db"].added == []


@pytest.mark.asyncio
async def test_rough_sorter_reservation_intent_uses_bin_cell_claim_contract() -> None:
    """粗分机预约 intent 必须能被 RuntimeIntentEffectApplier 执行为 CLAIM_BIN_CELL。"""

    from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
        SorterInboundRuntimeService,
    )

    plan = SorterInboundRuntimeService().build_rough_sorter_inbound_plan(
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

    from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
        SorterInboundRuntimeService,
    )
    from src.app.runtime.orchestration.services import runtime_location_event_service as location_event_module

    location_event_service = _RecordingLocationEventService()
    monkeypatch.setattr(location_event_module, "runtime_location_event_service", location_event_service)
    plan = SorterInboundRuntimeService().build_rough_sorter_inbound_plan(
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
    assert call["source"] == "MATERIAL_FLOW_SORTER_INBOUND"
    assert call["idempotency_key"] == "material-flow:rough-runtime-location-001:location-fact"
    assert call["provider_code"] == "WMS-A"
    assert call["auto_commit"] is False


@pytest.mark.asyncio
async def test_reconciliation_evidence_fact_records_runtime_location_evidence(monkeypatch) -> None:
    """RECONCILIATION_EVIDENCE 应作为 runtime 对账 evidence 落库，而不是走资源事实枚举。"""

    from src.app.runtime.capabilities.material_flow.smt_ng_wms_reconciliation_runtime_service import (
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
    assert call["source"] == "MATERIAL_FLOW_RECONCILIATION"
    assert call["external_reference_type"] == "WMS_DOCUMENT"
    assert call["external_reference_value"] == "DOC-001"
    assert call["idempotency_key"] == "material-flow:wms-reconciliation-001:reconciliation-evidence"
    assert call["auto_commit"] is False


@pytest.mark.asyncio
async def test_device_event_intent_preserves_canonical_routing_payload(monkeypatch) -> None:
    """生成型设备事件必须保留 processor 解析会话所需的顶层路由字段。"""

    accepted: list[dict[str, Any]] = []

    async def accept_device_event(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        accepted.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(id=91), created=True)

    from src.app.runtime.orchestration.services import runtime_inbox as runtime_inbox_module

    monkeypatch.setattr(
        runtime_inbox_module,
        "runtime_inbox_service",
        SimpleNamespace(accept_device_event=accept_device_event),
    )
    intent = RuntimeIntent.device_event(
        device_code="ARM_01",
        event_type="SCAN_COMPLETED",
        data={"barcode": "PKG-001"},
        timestamp=1_702_627_300_000,
        event_id="device-event-001",
    )

    await RuntimeIntentEffectApplier().apply(_effect_ctx(), [intent])

    [call] = accepted
    assert call["payload_json"] == {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_702_627_300_000,
        "data": {"barcode": "PKG-001"},
        "event_id": "device-event-001",
    }
