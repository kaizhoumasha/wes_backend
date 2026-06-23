import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.session import SessionStatus
from src.workline_runtime.diagnostics import ErrorCode
from src.workline_runtime.effect_result import WriteBackDisposition
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.plugin_manifest import (
    DeviceRequirement,
    PipelineQueue,
    RackPosition,
    RackPositionCarrierCapability,
    ResourceBoundary,
    SessionSubject,
    StateMachine,
    StateMachineOwner,
    StateMachineSubject,
    StateMachineTransition,
    TopologySpec,
    WorklinePluginManifest,
)
from src.workline_runtime.resource_wait_evidence import ResourceWaitEvidence
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
from src.workline_runtime.trace_context import TraceContext


def _session(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 123,
        "workline_id": 1,
        "status": SessionStatus.WAITING_DEVICE_RESULT,
        "context_json": {},
        "trace_id": None,
        "last_inbox_id": None,
        "plugin_key": "SMT_SORTING_INBOUND",
        "contract_version": None,
        "current_wait_type": "COMMAND_RESULT",
        "waiting_since": datetime(2026, 1, 1, 0, 0, 0),
        "deadline_at": datetime(2026, 1, 1, 0, 1, 0),
        "current_wait_timeout_seconds": 60,
        "awaiting_command_id": 99,
        "ended_at": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(
    orch_result: OrchestratorResult,
    *,
    session: Any | None = None,
    db: Any | None = None,
    workline: Any | None = None,
) -> dict[str, Any]:
    resolved_session = session or _session()
    return {
        "db": db or SimpleNamespace(add=AsyncMock(), execute=AsyncMock()),
        "session": resolved_session,
        "workline": workline or SimpleNamespace(id=1, plugin_key="SMT_SORTING_INBOUND", contract_version="1.0"),
        "inbox": SimpleNamespace(id=10, trace_id="trace-runtime", payload_json={"canonical_event_type": "SCAN"}),
        "devices_by_role": {},
        "source_device": None,
        "orch_result": orch_result,
        "current_status": SessionStatus.WAITING_DEVICE_RESULT,
        "trace_id": "trace-runtime",
        "trace": TraceContext.from_runtime(session=resolved_session, trace_id="trace-runtime"),
        "session_ctx": dict(getattr(resolved_session, "context_json", {}) or {}),
        "now": datetime(2026, 1, 1, 0, 2, 0),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


def test_resource_wait_new_write_surface_requires_subject_contract() -> None:
    signature = inspect.signature(RuntimeIntent.resource_wait)

    assert "subject_type" in signature.parameters
    assert "subject_key" in signature.parameters
    assert "projection_type" in signature.parameters
    assert "resource_kind" not in signature.parameters
    assert "resource_key" not in signature.parameters

    intent = RuntimeIntent.resource_wait(
        subject_type="RACK_POSITION",
        subject_key="station:TARGET_STATION",
        projection_type="RACK_POSITION_LEASE",
        reason_code="STATION_BUSY",
        message="目标工位正在处理其它物料",
        payload={"resource_kind": "STATION", "resource_key": "station:TARGET_STATION"},
    )

    assert intent.kind == RuntimeIntentKind.RESOURCE_WAIT
    assert intent.payload_json["subject_type"] == "RACK_POSITION"
    assert intent.payload_json["subject_key"] == "station:TARGET_STATION"
    assert intent.payload_json["projection_type"] == "RACK_POSITION_LEASE"
    assert intent.payload_json["resource_kind"] == "STATION"
    assert intent.payload_json["resource_key"] == "station:TARGET_STATION"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"subject_key": "S1", "projection_type": "RACK_POSITION_LEASE"}, "subject_type"),
        ({"subject_type": "RACK_POSITION", "projection_type": "RACK_POSITION_LEASE"}, "subject_key"),
        ({"subject_type": "RACK_POSITION", "subject_key": "S1"}, "projection_type"),
    ],
)
def test_resource_wait_direct_contract_requires_subject_fields(payload: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=f"RESOURCE_WAIT intent requires {message}"):
        RuntimeIntent(
            kind=RuntimeIntentKind.RESOURCE_WAIT,
            reason_code="STATION_BUSY",
            message="目标工位正在处理其它物料",
            payload_json=payload,
        )


def test_resource_wait_evidence_uses_subject_as_primary_identity() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=11,
        subject_type="RACK_POSITION",
        subject_key="station:TARGET_STATION",
        projection_type="RACK_POSITION_LEASE",
        reason_code="STATION_BUSY",
        message="目标 Station 忙",
        occurred_at="2026-01-01T00:00:10",
        session_id=22,
        workline_id=33,
        trace_id="trace-resource-wait",
        details={"resource_kind": "STATION", "resource_key": "station:TARGET_STATION"},
    )

    assert evidence.diagnostic_key == "RESOURCE_WAIT:11:RACK_POSITION:RACK_POSITION_LEASE:station:TARGET_STATION"
    session_context = evidence.to_session_context()
    diagnostic_payload = evidence.to_diagnostic_evidence()
    for payload in (session_context, diagnostic_payload):
        assert payload["subject_type"] == "RACK_POSITION"
        assert payload["subject_key"] == "station:TARGET_STATION"
        assert payload["projection_type"] == "RACK_POSITION_LEASE"
    assert "details" not in session_context
    assert diagnostic_payload["details"] == {"resource_kind": "STATION", "resource_key": "station:TARGET_STATION"}


@pytest.mark.asyncio
async def test_apply_resource_wait_timeline_and_context_use_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(
        status=SessionStatus.RUNNING,
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={},
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()

    monkeypatch.setattr("src.app.workline.services.write_back_service._emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )

    result = await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.resource_wait(
                subject_type="TARGET_STATION",
                subject_key="station:TARGET_STATION",
                projection_type="STATION_LEASE",
                reason_code="STATION_BUSY",
                message="目标 Station 忙",
                payload={"active_session_id": 456},
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.RESOURCE_RETRY
    assert session.context_json["resource_wait"]["subject_type"] == "TARGET_STATION"
    assert session.context_json["resource_wait"]["subject_key"] == "station:TARGET_STATION"
    assert session.context_json["resource_wait"]["projection_type"] == "STATION_LEASE"
    timeline_payload = emit_timeline.await_args.kwargs["payload"]
    assert timeline_payload["wait_token"] == "station:TARGET_STATION"
    assert timeline_payload["subject_type"] == "TARGET_STATION"
    assert timeline_payload["subject_key"] == "station:TARGET_STATION"
    assert timeline_payload["projection_type"] == "STATION_LEASE"
    record_resource_wait.assert_awaited_once()


@pytest.mark.parametrize("plugin_key", [None, "UNKNOWN_PLUGIN"])
@pytest.mark.asyncio
async def test_resource_wait_with_missing_or_unknown_manifest_records_contract_diagnostic_without_wait(
    plugin_key: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status=SessionStatus.RUNNING,
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={},
        plugin_key=plugin_key,
    )
    workline = SimpleNamespace(id=1, plugin_key=plugin_key, contract_version="1.0")
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db, workline=workline)
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()
    record_event = AsyncMock(return_value=SimpleNamespace(id=99))

    monkeypatch.setattr("src.app.workline.services.write_back_service._emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event",
        record_event,
    )

    result = await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.resource_wait(
                subject_type="TARGET_STATION",
                subject_key="station:TARGET_STATION",
                projection_type="ACTIVE_TARGET_BIN_RACK",
                reason_code="STATION_BUSY",
                message="目标 Station 忙",
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.PROCESSED
    assert "resource_wait" not in session.context_json
    assert session.current_wait_type is None
    persist_external_wait.assert_not_awaited()
    record_resource_wait.assert_not_awaited()
    emit_timeline.assert_not_awaited()
    record_event.assert_awaited_once()
    assert record_event.await_args.kwargs["event"].error_code == ErrorCode.RESOURCE_WAIT
    evidence = record_event.await_args.kwargs["evidence"]
    assert evidence["reason_code"] == "RESOURCE_WAIT_SUBJECT_CONTRACT_INVALID"
    assert evidence["subject_type"] == "TARGET_STATION"
    assert evidence["subject_key"] == "station:TARGET_STATION"
    assert evidence["projection_type"] == "ACTIVE_TARGET_BIN_RACK"
    assert evidence["details"]["contract_error"] == "RESOURCE_WAIT manifest is missing or unknown"


@pytest.mark.asyncio
async def test_resource_wait_with_registered_manifest_undeclared_subject_records_contract_diagnostic_without_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status=SessionStatus.RUNNING,
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={},
        plugin_key="SMT_SORTING_INBOUND",
    )
    workline = SimpleNamespace(id=1, plugin_key="SMT_SORTING_INBOUND", contract_version="1.0")
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db, workline=workline)
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()
    record_event = AsyncMock(return_value=SimpleNamespace(id=99))

    monkeypatch.setattr("src.app.workline.services.write_back_service._emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event",
        record_event,
    )

    result = await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.resource_wait(
                subject_type="UNDECLARED_SUBJECT",
                subject_key="unknown:UNDECLARED_SUBJECT",
                projection_type="UNKNOWN_PROJECTION",
                reason_code="STATION_BUSY",
                message="目标 Station 忙",
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.PROCESSED
    assert "resource_wait" not in session.context_json
    assert session.current_wait_type is None
    persist_external_wait.assert_not_awaited()
    record_resource_wait.assert_not_awaited()
    emit_timeline.assert_not_awaited()
    record_event.assert_awaited_once()
    assert record_event.await_args.kwargs["event"].error_code == ErrorCode.RESOURCE_WAIT
    evidence = record_event.await_args.kwargs["evidence"]
    assert evidence["reason_code"] == "RESOURCE_WAIT_SUBJECT_CONTRACT_INVALID"
    assert evidence["subject_type"] == "UNDECLARED_SUBJECT"
    assert evidence["projection_type"] == "UNKNOWN_PROJECTION"
    assert "RESOURCE_WAIT subject is not declared in manifest" in evidence["details"]["contract_error"]


def test_manifest_resource_wait_subject_helper_accepts_declared_subjects_and_rejects_unknown() -> None:
    manifest = WorklinePluginManifest(
        plugin_key="test_plugin",
        contract_version="1.0.0",
        devices=[DeviceRequirement(role="ENTRY_SCANNER")],
        rack_positions=[
            RackPosition(
                code="SINGLE_LAYER_A",
                role="WORK",
                station_code="STATION_A",
                carrier_capability=RackPositionCarrierCapability(allowed_rack_kinds=("SINGLE_LAYER",)),
            )
        ],
        topology=TopologySpec(),
        resource_boundaries=[
            ResourceBoundary(
                rack_position_code="SINGLE_LAYER_A",
                rack_kind="SINGLE_LAYER",
                business_demand_type="SORTING",
                wms_operation_type="MOVE",
                snapshot_kind="CURRENT_RACK",
                lease_scope="SESSION",
            )
        ],
        session_subject=SessionSubject(
            type="MATERIAL_UNIT",
            physical_form="REEL",
            identity_sources=("pkg_code",),
        ),
        state_machines=[
            StateMachine(
                id="material_lifecycle",
                subject=StateMachineSubject(category="MATERIAL_UNIT", type="MATERIAL_UNIT", physical_form="REEL"),
                state_owner=StateMachineOwner(model="MaterialUnit", field="status"),
                granularity="MATERIAL_LIFECYCLE",
                transitions=[
                    StateMachineTransition(from_state="IN_TRANSIT", to_states=("STORED",)),
                    StateMachineTransition(from_state="STORED", to_states=("COMPLETED",)),
                    StateMachineTransition(from_state="COMPLETED", to_states=()),
                    StateMachineTransition(from_state="NG", to_states=()),
                    StateMachineTransition(from_state="RECONCILING", to_states=("STORED",)),
                ],
            )
        ],
        pipeline_queues=[PipelineQueue(code="ENTRY_SCAN_QUEUE", role="ENTRY", capacity="MANY")],
    )

    manifest.validate_resource_wait_subject(
        subject_type="MATERIAL_UNIT",
        projection_type="MATERIAL_LIFECYCLE",
    )
    manifest.validate_resource_wait_subject(
        subject_type="ENTRY_SCAN_QUEUE",
        projection_type="QUEUE_MEMBERSHIP",
    )
    manifest.validate_resource_wait_subject(
        subject_type="SINGLE_LAYER_A",
        projection_type="CURRENT_RACK",
    )
    manifest.validate_resource_wait_subject(
        subject_type="SINGLE_LAYER_A",
        projection_type="SESSION_LEASE",
    )

    with pytest.raises(ValueError, match="RESOURCE_WAIT subject is not declared in manifest"):
        manifest.validate_resource_wait_subject(subject_type="UNKNOWN_SUBJECT", projection_type="UNKNOWN")
