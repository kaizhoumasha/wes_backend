"""RuntimeCapabilityDispatcher target-state routing contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import SorterInboundRuntimeService
from src.app.runtime.capability_dispatcher import (
    RuntimeCapabilityCatalog,
    RuntimeCapabilityDefinition,
    RuntimeCapabilityDispatcher,
    RuntimeCapabilityRouteError,
    RuntimeCapabilityUndeclaredError,
)
from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
from src.app.runtime.normalization.normalizers import normalize_inbox_input
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorService
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier


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
        contract_version="2026-07-06.material-flow",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        runtime_capabilities_effect=effect_capabilities or [],
        inbound_normalizers_event=["DEVICE_SCAN"],
        inbound_normalizers_result=["COMMAND_RESULT"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30 if effect_capabilities else None,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
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
        "source_version": "wms.material-flow",
    }


def _rough_sorter_inbound_envelope_payload() -> dict[str, object]:
    return {
        "callback_type": "WMS_ROUGH_SORTER_INBOUND",
        "runtime_capability": "rough_sorter_inbound",
        "source_system": "WMS",
        "source_event_id": "wms-rough-dispatch-001",
        "source_version": "wms.material-flow",
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


def test_runtime_inbox_normalizer_dispatches_to_material_flow_runtime_service() -> None:
    """RuntimeInbox -> normalizer registry -> dispatcher -> material-flow service 成功链路。"""

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
    service = SorterInboundRuntimeService()
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


def test_runtime_inbox_normalizer_uses_persisted_event_type_as_canonical_fact() -> None:
    """RuntimeInbox.event_type 是 canonical 事实源，payload 只保留上游原始事件类型。"""

    normalized = normalize_inbox_input(
        SimpleNamespace(
            kind="DEVICE_EVENT",
            event_type="SCAN_COMPLETED",
            payload_json={
                "event_type": "PROVIDER_SCAN_DONE",
                "device_code": "SCAN-01",
                "data": {},
            },
        )
    )

    assert normalized.source_event_type == "PROVIDER_SCAN_DONE"
    assert normalized.canonical_event_type == "SCAN_COMPLETED"


def test_runtime_inbox_normalizer_rejects_missing_persisted_event_type() -> None:
    """Device event 缺少持久化 canonical 事实时必须 fail-closed。"""

    with pytest.raises(ValueError, match="RuntimeInbox event_type is required"):
        normalize_inbox_input(
            SimpleNamespace(
                kind="DEVICE_EVENT",
                payload_json={"event_type": "PROVIDER_SCAN_DONE", "data": {}},
            )
        )


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


@pytest.mark.asyncio
async def test_orchestrator_routes_command_result_outside_runtime_capability_dispatcher() -> None:
    """常规 COMMAND_RESULT 不应被误送入 runtime capability dispatcher。"""

    def fail_if_profile_resolved(_normalized_input: object) -> object:
        raise AssertionError("COMMAND_RESULT must not resolve a runtime capability provider profile")

    orchestrator = OrchestratorService(
        lock_provider=lambda _lock_key: _noop_lock(),
        runtime_profile_resolver=fail_if_profile_resolved,
    )

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=201, contract_version="rough_sorter.v1"),
        workline=SimpleNamespace(contract_version="rough_sorter.v1", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-201",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "device_code": "ARM-01",
            },
            trace_id="trace-command-result",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-command-result",
    )

    assert result.success is True
    assert [intent.kind for intent in result.intents or []] == [RuntimeIntentKind.CONTINUE_NEXT]


@pytest.mark.asyncio
async def test_orchestrator_blocks_failed_command_result_without_runtime_capability_profile() -> None:
    """常规 COMMAND_RESULT 失败回调应进入 block，而不是 provider profile 错误。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=202, contract_version="rough_sorter.v1"),
        workline=SimpleNamespace(contract_version="rough_sorter.v1", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-202",
                "command_type": "PICK_AND_PUT",
                "result": "FAILED",
                "device_code": "ARM-01",
                "error_detail": {"error_code": "DEVICE_BUSY", "error_message": "设备忙"},
            },
            trace_id="trace-command-failed",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-command-failed",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.block_scope == BlockScope.COMMAND
    assert intent.reason_code == "DEVICE_BUSY"
    assert intent.message == "设备忙"


@pytest.mark.asyncio
async def test_orchestrator_blocks_unknown_command_result_with_stable_reason_code() -> None:
    """未知命令结果不能静默推进，fallback reason code 必须稳定。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=203, contract_version="rough_sorter.v1"),
        workline=SimpleNamespace(contract_version="rough_sorter.v1", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-203",
                "command_type": "PICK_AND_PUT",
                "result": "UNKNOWN_VENDOR_RESULT",
                "device_code": "ARM-01",
            },
            trace_id="trace-command-unknown",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-command-unknown",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.block_scope == BlockScope.COMMAND
    assert intent.reason_code == "SYSTEM_FAILURE"


@pytest.mark.asyncio
async def test_orchestrator_routes_rough_sorter_scan_completed_device_event_to_target_state_intents() -> None:
    """普通 DEVICE_EVENT 入口事件应有目标态 intent handler，不应进入 provider profile。"""

    def fail_if_profile_resolved(_normalized_input: object) -> object:
        raise AssertionError("DEVICE_EVENT must not resolve a runtime capability provider profile")

    orchestrator = OrchestratorService(
        lock_provider=lambda _lock_key: _noop_lock(),
        runtime_profile_resolver=fail_if_profile_resolved,
    )

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=301, contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(
            contract_version="rough_sorter.v2",
            plugin_key="rough_sorter",
            config={"pipeline_input_location": "PIPELINE-IN-T"},
            runtime_config_json={},
        ),
        inbox=SimpleNamespace(
            kind="DEVICE_EVENT",
            event_type="SCAN_COMPLETED",
            payload_json={
                "event_type": "SCAN_COMPLETED",
                "canonical_event_type": "SCAN_COMPLETED",
                "device_code": "SCAN-01",
                "data": {
                    "HHPN": "MAT-A",
                    "MfrPN": "VENDOR-A",
                    "Qty": "10",
                    "DateCode": "20260707",
                    "LotCode": "LOT-A",
                    "PkgID": "PKG-SCAN-001",
                },
            },
            trace_id="trace-scan",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-scan",
    )

    assert result.success is True
    assert [intent.kind for intent in result.intents or []] == [
        RuntimeIntentKind.CREATE_MATERIAL_UNIT,
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    command_intent = (result.intents or [])[-1]
    assert command_intent.action == "PICK_AND_PUT"
    assert command_intent.device_role == "ROUGH_SORTER_INPUT_ARM"
    assert command_intent.payload_json["params"]["target_location"] == "PIPELINE-IN-T"


@pytest.mark.asyncio
async def test_orchestrator_routes_internal_source_pick_requested_to_target_state_command() -> None:
    """普通 INTERNAL_EVENT 也应走目标态事件 handler，而不是 provider profile。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=302, contract_version="2026-06-21.p1"),
        workline=SimpleNamespace(contract_version="2026-06-21.p1", plugin_key="SMT_SORTING_INBOUND"),
        inbox=SimpleNamespace(
            id=901,
            kind="INTERNAL_EVENT",
            event_type="SORTING_SOURCE_PICK_REQUESTED",
            payload_json={
                "event_type": "SORTING_SOURCE_PICK_REQUESTED",
                "canonical_event_type": "SORTING_SOURCE_PICK_REQUESTED",
                "event_id": "evt-source-pick-001",
                "causation_id": "handoff-source-item:11",
                "trace_id": "trace-source-pick",
                "data": {
                    "handoff_demand_id": 7,
                    "handoff_source_item_id": 11,
                    "claim_attempt_no": 2,
                    "rack_release_id": "REL-1",
                    "single_layer_rack_code": "RACK-A",
                    "bin_code": "BIN-A",
                    "bin_cell_index": 3,
                    "bin_cell_code": "CELL-A-03",
                    "material_identity_key": "MAT:A:V:20260707:LOT-A",
                    "pkg_code": "PKG-SMT-001",
                    "reel_thickness_mm": "15.5",
                    "route_evidence": {"route": "handoff"},
                },
            },
            trace_id="trace-source-pick",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-source-pick",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.action == "SORTING_SOURCE_PICK"
    assert intent.device_role == "SORTING_SOURCE_ARM"
    assert intent.payload_json["handoff_demand_id"] == 7
    assert intent.payload_json["source_pick_inbox_id"] == 901


@pytest.mark.asyncio
async def test_orchestrator_routes_manual_resume_without_runtime_capability_profile() -> None:
    """人工恢复 inbox 应走目标态 manual handler，并真实清理 MANUAL_HOLD 状态。"""

    def fail_if_profile_resolved(_normalized_input: object) -> object:
        raise AssertionError("MANUAL_RESUME must not resolve a runtime capability provider profile")

    session = SimpleNamespace(
        id=303,
        status="MANUAL_HOLD",
        current_wait_type="COMMAND_RESULT",
        waiting_since=object(),
        deadline_at=None,
        current_wait_timeout_seconds=300,
        awaiting_device_command_code="CMD-303",
        failure_domain="WORKLINE",
        failure_code="MANUAL_HOLD_REQUESTED",
        failure_message="人工暂停",
        ended_at=None,
        trace_id=None,
    )
    inbox = SimpleNamespace(
        id=902,
        kind="INTERNAL_EVENT",
        event_type="MANUAL_RESUME",
        payload_json={
            "message_type": "MANUAL_OPERATION",
            "operation": "RESUME",
            "operator_id": "operator-a",
            "reason": "现场确认恢复",
            "session_id": 303,
        },
        trace_id="trace-manual-resume",
    )
    orchestrator = OrchestratorService(
        lock_provider=lambda _lock_key: _noop_lock(),
        runtime_profile_resolver=fail_if_profile_resolved,
    )

    result = await orchestrator.process_inbox(
        session=session,
        workline=SimpleNamespace(contract_version="rough_sorter.v2", plugin_key="rough_sorter"),
        inbox=inbox,
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-manual-resume",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.CONTINUE_NEXT
    assert intent.payload_json["operation"] == "RESUME"

    effect_result = await RuntimeIntentEffectApplier().apply(
        {
            "session": session,
            "trace_id": "trace-manual-resume",
            "inbox": inbox,
        },
        [intent],
    )

    assert effect_result.disposition == WriteBackDisposition.PROCESSED
    assert session.status.value == "RUNNING"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.failure_code is None


@pytest.mark.asyncio
async def test_orchestrator_routes_manual_cancel_to_cancel_intent() -> None:
    """人工取消 inbox 不能被错误表达成 COMPLETE 或 MANUAL_HOLD。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=304, status="MANUAL_HOLD", contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(contract_version="rough_sorter.v2", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            id=903,
            kind="INTERNAL_EVENT",
            event_type="MANUAL_CANCEL",
            payload_json={
                "message_type": "MANUAL_OPERATION",
                "operation": "CANCEL",
                "operator_id": "operator-a",
                "reason": "现场确认取消",
                "session_id": 304,
            },
            trace_id="trace-manual-cancel",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-manual-cancel",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.CANCEL
    assert intent.reason_code == "MANUAL_CANCEL_REQUESTED"
    assert intent.payload_json["operation"] == "CANCEL"


@pytest.mark.asyncio
async def test_cancel_effect_marks_session_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """CANCEL intent 应真实进入 CANCELLED，而不是完成或挂起。"""

    from src.app.runtime.orchestration.repositories import session_repository as session_repository_module
    from src.app.sys import repositories as sys_repositories
    from src.app.workline.services import write_back_service as workline_effects

    persist_cancelled = AsyncMock()
    monkeypatch.setattr(
        session_repository_module,
        "WorklineSessionRepository",
        lambda: SimpleNamespace(persist_cancelled=persist_cancelled),
    )
    cancel_active_by_session = AsyncMock(return_value=2)
    monkeypatch.setattr(
        sys_repositories,
        "SystemOutboxRepository",
        lambda: SimpleNamespace(cancel_active_by_session=cancel_active_by_session),
    )
    emit_timeline = AsyncMock()
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    session = SimpleNamespace(
        id=305,
        status="MANUAL_HOLD",
        current_wait_type="COMMAND_RESULT",
        waiting_since=object(),
        deadline_at=None,
        current_wait_timeout_seconds=300,
        awaiting_device_command_code="CMD-305",
        failure_domain="WORKLINE",
        failure_code="MANUAL_HOLD_REQUESTED",
        failure_message="人工暂停",
        ended_at=None,
        trace_id=None,
    )
    inbox = SimpleNamespace(id=904, payload_json={})

    effect_result = await RuntimeIntentEffectApplier().apply(
        {
            "db": SimpleNamespace(),
            "session": session,
            "trace_id": "trace-manual-cancel",
            "inbox": inbox,
            "current_status": "MANUAL_HOLD",
            "now": object(),
        },
        [
            RuntimeIntent.cancel(
                reason_code="MANUAL_CANCEL_REQUESTED",
                message="现场确认取消",
                payload={"operator_id": "operator-a"},
            )
        ],
    )

    assert effect_result.disposition == WriteBackDisposition.PROCESSED
    assert session.status.value == "CANCELLED"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    persist_cancelled.assert_awaited_once()
    cancel_active_by_session.assert_awaited_once_with(
        SimpleNamespace(),
        session_id=305,
        reason="MANUAL_CANCEL_REQUESTED",
    )
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_blocks_unregistered_device_event_with_stable_diagnostic() -> None:
    """未注册普通 DEVICE_EVENT 应明确 block，避免回流 legacy plugin 或 null fallback。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=306, contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(contract_version="rough_sorter.v2", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            id=905,
            kind="DEVICE_EVENT",
            event_type="UNREGISTERED_EVENT",
            payload_json={
                "event_type": "UNREGISTERED_EVENT",
                "canonical_event_type": "UNREGISTERED_EVENT",
                "device_code": "SCAN-01",
                "data": {},
            },
            trace_id="trace-unregistered-event",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-unregistered-event",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "TARGET_STATE_DEVICE_EVENT_HANDLER_MISSING"
    assert intent.payload_json["canonical_event_type"] == "UNREGISTERED_EVENT"


@pytest.mark.asyncio
async def test_device_event_operation_cancel_does_not_impersonate_manual_cancel() -> None:
    """普通设备事件里的 operation 字段不能冒充人工取消控制面。"""

    orchestrator = OrchestratorService(lock_provider=lambda _lock_key: _noop_lock())

    result = await orchestrator.process_inbox(
        session=SimpleNamespace(id=307, contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(contract_version="rough_sorter.v2", plugin_key="rough_sorter"),
        inbox=SimpleNamespace(
            id=906,
            kind="DEVICE_EVENT",
            event_type="UNREGISTERED_EVENT",
            payload_json={
                "event_type": "UNREGISTERED_EVENT",
                "canonical_event_type": "UNREGISTERED_EVENT",
                "device_code": "SCAN-01",
                "data": {"operation": "CANCEL"},
            },
            trace_id="trace-device-operation-cancel",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id="trace-device-operation-cancel",
    )

    assert result.success is True
    [intent] = result.intents or []
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "TARGET_STATE_DEVICE_EVENT_HANDLER_MISSING"


@pytest.mark.asyncio
async def test_continue_next_effect_clears_command_result_wait_anchor() -> None:
    """COMMAND_RESULT 成功生成的 CONTINUE_NEXT 必须真实清理等待锚点。"""

    session = SimpleNamespace(
        id=401,
        status="WAITING_DEVICE_RESULT",
        current_wait_type="COMMAND_RESULT",
        waiting_since=object(),
        deadline_at=None,
        current_wait_timeout_seconds=300,
        awaiting_device_command_code="CMD-401",
        failure_domain="COMMAND",
        failure_code="DEVICE_BUSY",
        failure_message="设备忙",
        ended_at=None,
        trace_id=None,
    )

    effect_result = await RuntimeIntentEffectApplier().apply(
        {
            "session": session,
            "trace_id": "trace-continue-next",
            "inbox": SimpleNamespace(id=777, payload_json={}),
        },
        [RuntimeIntent.continue_next()],
    )

    assert effect_result.disposition == WriteBackDisposition.PROCESSED
    assert session.status.value == "RUNNING"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.failure_code is None
    assert session.last_inbox_id == 777


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
