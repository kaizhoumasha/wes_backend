from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.material_unit import MaterialUnitStatus
from src.app.workline.models.session import SessionStatus
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.ng_return_item_service import NgMaterialConflictError, ng_return_item_service
from src.app.workline.services.runtime_hold_creation_service import runtime_hold_creation_service
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    SORTING_CONTEXT_SCHEMA_VERSION,
)
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
)
from src.workline_runtime.trace_context import TraceContext
from tests.workline_runtime.support.runtime_intent_effects import (
    FakeTerminalRepository,
    MaterialUnitDb,
    RecordingDb,
    RecordingResourceProjectionService,
    _ctx,
    _session,
)


def _handoff_sorting_context(*, include_source_pick_request: bool = True) -> dict[str, Any]:
    sorting: dict[str, Any] = {
        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
        "current_material": {
            "source_bin_code": "SRC-BIN-A",
            "source_cell_code": "A01",
            "material_identity_key": "material:PKG-001",
            "reel_thickness_mm": "7.125",
            "evidence": {"pkg_code": "PKG-001", "source_event_id": "source-pick-requested:11:22:1"},
        },
        "pending_target_placement": {
            "target_bin_code": "TARGET-BIN-A",
            "target_cell_code": "1",
            "material_identity_key": "material:PKG-001",
            "reel_thickness_mm": "7.125",
            "allocation_snapshot_version": 3,
            "capacity_evidence": {"capacity_depth_mm": "30"},
        },
        "stations": {"scan_platform": "OCCUPIED"},
    }
    if include_source_pick_request:
        sorting["source_pick_request"] = {
            "handoff_demand_id": 11,
            "handoff_source_item_id": 22,
            "claim_attempt_no": 1,
            "event_id": "source-pick-requested:11:22:1",
            "target_workline_code": "SMT_SORTER_01",
            "manifest_contract_version": "v1",
            "source_rack_position_code": "SINGLE_LAYER_A",
            "target_rack_position_code": "TARGET_STATION",
            "route_evidence": {},
        }
    return sorting


@pytest.mark.asyncio
async def test_empty_intents_complete_new_event_session_as_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_device_command_code=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["current_status"] = "NEW"
    ctx["inbox"].payload_json = {
        "message_type": "DEVICE_EVENT",
        "device_code": "PIPELINE01",
        "canonical_event_type": "MATERIAL_ARRIVED",
    }

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(ctx, [])

    assert session.status == "COMPLETED"
    assert session.ended_at == ctx["now"]
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert captured[0]["from_status"] == "NEW"
    assert captured[0]["to_status"] == "COMPLETED"
    assert captured[0]["payload"]["completion_reason"] == "NO_RUNTIME_INTENT"


@pytest.mark.asyncio
async def test_update_context_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_context({"scan_ok": True}),
            RuntimeIntent.complete({"bin_code": "BIN-001"}),
        ],
    )

    assert session.context_json == {"pkg_id": "PKG-001", "scan_ok": True, "bin_code": "BIN-001"}
    assert session.status == "COMPLETED"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.ended_at == ctx["now"]
    record_ng_flow.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_persists_session_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == "COMPLETED"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_turns_ng_material_conflict_into_runtime_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    created_holds: list[dict[str, Any]] = []
    long_identity = f"test-material:{'X' * 300}"

    async def raise_conflict(*_args: Any, **_kwargs: Any) -> None:
        raise NgMaterialConflictError(
            material_identity_key=long_identity,
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": long_identity,
                "new_source_command_id": 8802,
            },
        )

    async def create_hold(_db: Any, **kwargs: Any) -> SimpleNamespace:
        created_holds.append(kwargs)
        return SimpleNamespace(id=7701, **kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", raise_conflict)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == "MANUAL_HOLD"
    assert session.context_json["ng_material_conflict"]["reason_code"] == "NG_MATERIAL_CONFLICT"
    assert created_holds[0]["source_reason"] == "NG_MATERIAL_CONFLICT"
    assert created_holds[0]["evidence"]["material_identity_key"] == long_identity
    assert created_holds[0]["source_event_id"].startswith("ng-material-conflict:123:8802:")
    assert len(created_holds[0]["source_event_id"]) < 80
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_skips_ng_flow_for_already_completed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status=SessionStatus.COMPLETED.value, context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock(
        side_effect=NgMaterialConflictError(
            material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": "MAT:HH-001:MFR-001:260528:LOT-A",
            },
        )
    )
    create_hold = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == SessionStatus.COMPLETED.value
    record_ng_flow.assert_awaited_once()
    create_hold.assert_not_awaited()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_records_ng_flow_for_already_completed_ng_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status=SessionStatus.COMPLETED.value,
        context_json={
            "ng_reason": "LOCAL_SORTING_NG",
            "source_payload": {
                "current_material": {"pkg_code": "PKG-001", "material_identity_key": "MAT:PKG-001"},
                "ng_command_payload": {"command_code": "CMD-NG-001", "result": "SUCCESS"},
            },
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock(return_value=SimpleNamespace(id=8801))
    create_hold = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == SessionStatus.COMPLETED.value
    record_ng_flow.assert_awaited_once()
    create_hold.assert_not_awaited()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_fact_intent_is_applied_before_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    resource_projection = RecordingResourceProjectionService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert resource_projection.calls[0]["db"] is ctx["db"]
    assert resource_projection.calls[0]["session"] is session
    assert resource_projection.calls[0]["idempotency_key"] == "MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4"
    assert session.status == "COMPLETED"
    assert session.context_json["material_mounted"] is True
    record_ng_flow.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_pick_complete_does_not_record_handoff_success_without_source_pick_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    calls: list[tuple[str, Any]] = []

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(("source_pick_success", session.status, kwargs))
        assert _db is db
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", AsyncMock())
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_mounted": True})])

    assert calls == []


@pytest.mark.asyncio
async def test_source_pick_resource_fact_records_handoff_success_after_context_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    calls: list[tuple[str, Any]] = []
    resource_projection = RecordingResourceProjectionService()

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        current_material = session.context_json["sorting"].get("current_material")
        calls.append(("source_pick_success", current_material))
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["trace_id"] == "trace-runtime"
        assert current_material["material_identity_key"] == "material:PKG-001"
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "source_pick_request": {
                            "handoff_demand_id": 11,
                            "handoff_source_item_id": 22,
                            "claim_attempt_no": 1,
                            "event_id": "source-pick-requested:11:22:1",
                            "target_workline_code": "SMT_SORTER_01",
                            "manifest_contract_version": "v1",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                            "target_rack_position_code": "TARGET_STATION",
                            "route_evidence": {},
                        },
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert session.context_json["sorting"]["current_material"]["pkg_code"] == "PKG-001"
    assert calls == [
        (
            "source_pick_success",
            {
                "material_identity_key": "material:PKG-001",
                "pkg_code": "PKG-001",
                "source_rack_position_code": "SINGLE_LAYER_A",
            },
        )
    ]


@pytest.mark.asyncio
async def test_source_pick_resource_fact_records_handoff_success_with_command_evidence_when_request_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        awaiting_device_command_code=88,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
            }
        },
    )
    inbox = SimpleNamespace(
        id=10,
        command_id=88,
        trace_id="trace-runtime",
        payload_json={
            "command_code": "SOURCE-CMD-001",
            "command_type": COMMAND_SOURCE_PICK,
            "result": "SUCCESS",
            "data": {},
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"] = inbox
    ctx["trace"] = TraceContext.from_runtime(session=session, inbox=inbox, trace_id="trace-runtime")
    resource_projection = RecordingResourceProjectionService()
    calls: list[dict[str, Any]] = []

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        assert _db is db
        assert kwargs["command_id"] == 88
        assert kwargs["session"] is session
        assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "material:PKG-001"
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_pick_resource_fact_without_handoff_evidence_does_not_record_handoff_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        awaiting_device_command_code=None,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
            }
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_call = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                },
                idempotency_key="MATERIAL_UNMOUNTED:manual:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    record_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciling_source_pick_resource_fact_does_not_record_handoff_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_call = AsyncMock(return_value=SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False))
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        AsyncMock(),
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {"material_identity_key": "material:PKG-001"},
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert "current_material" not in session.context_json["sorting"]
    assert session.status == "MANUAL_HOLD"
    record_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_place_terminal_success_records_sorted_after_context_cleanup_and_claims_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"sorting": _handoff_sorting_context()})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "TARGET-CMD-001",
        "command_type": COMMAND_TARGET_PLACE,
        "result": "SUCCESS",
        "data": {},
    }
    resource_projection = RecordingResourceProjectionService()
    ledger_calls: list[dict[str, Any]] = []
    claim_calls: list[dict[str, Any]] = []

    async def record_terminal(_db: Any, **kwargs: Any) -> SimpleNamespace:
        ledger_calls.append(kwargs)
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["terminal_status"] == "SORTED"
        assert "current_material" not in session.context_json["sorting"]
        assert "pending_target_placement" not in session.context_json["sorting"]
        return SimpleNamespace(
            outcome="advanced",
            advanced=len(ledger_calls) == 1,
            already_terminal=len(ledger_calls) > 1,
            current_demand_id=11,
        )

    async def claim_next(_db: Any, **kwargs: Any) -> SimpleNamespace:
        claim_calls.append(kwargs)
        assert _db is db
        assert kwargs["demand_id"] == 11
        return SimpleNamespace(kind="EMPTY")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", claim_next)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.COMPLETE]
    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert [call["terminal_status"] for call in ledger_calls] == ["SORTED"]
    assert claim_calls == [{"trace_id": "trace-runtime", "demand_id": 11}]


@pytest.mark.asyncio
async def test_target_place_terminal_success_does_not_complete_session_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.PICKED,
        sorting_session_id=123,
        completed_at=None,
        failure_code="OLD_FAILURE",
        failure_message="旧失败",
        next_attempt_at=datetime(2026, 1, 1, 0, 3, 0),
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        context_json={"sorting": _handoff_sorting_context()},
    )
    db = RecordingDb(demand)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "TARGET-CMD-001",
        "command_type": COMMAND_TARGET_PLACE,
        "result": "SUCCESS",
        "data": {},
    }
    resource_projection = RecordingResourceProjectionService()
    handoff_service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    persisted_completions: list[dict[str, Any]] = []
    emitted_timelines: list[dict[str, Any]] = []

    async def persist_completed(_repo: Any, _db: Any, **kwargs: Any) -> None:
        persisted_completions.append(kwargs)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        emitted_timelines.append(kwargs)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        handoff_service.record_source_item_terminal_result,
        raising=False,
    )
    monkeypatch.setattr(
        smt_inbound_handoff_service, "claim_next_source_item", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_completed",
        persist_completed,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert item.status == SmtInboundHandoffSourceItemStatus.SORTED
    assert item.completed_at is not None
    assert item.failure_code is None
    assert demand.failure_code is None
    assert len(persisted_completions) == 1
    assert [timeline["action_type"].value for timeline in emitted_timelines] == ["SESSION_COMPLETED"]


@pytest.mark.asyncio
async def test_ng_place_terminal_success_records_skipped_after_context_cleanup_and_claims_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sorting = _handoff_sorting_context()
    sorting.pop("pending_target_placement", None)
    sorting["current_material"]["ng_status"] = "MOVING_TO_NG"
    session = _session(context_json={"sorting": sorting}, current_material_unit_id=1001)
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "NG-CMD-001",
        "command_type": COMMAND_NG_PLACE,
        "result": "SUCCESS",
        "data": {},
        "ng_location": "NG-01",
        "ng_reason_code": "LOCAL_SORTING_NG",
    }
    ledger_calls: list[dict[str, Any]] = []
    claim_calls: list[dict[str, Any]] = []

    async def record_terminal(_db: Any, **kwargs: Any) -> SimpleNamespace:
        ledger_calls.append(kwargs)
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["terminal_status"] == "SKIPPED"
        assert kwargs["terminal_evidence"]["ng_command_payload"]["ng_location"] == "NG-01"
        assert "current_material" not in session.context_json["sorting"]
        return SimpleNamespace(
            outcome="advanced",
            advanced=len(ledger_calls) == 1,
            already_terminal=len(ledger_calls) > 1,
            current_demand_id=11,
        )

    async def claim_next(_db: Any, **kwargs: Any) -> SimpleNamespace:
        claim_calls.append(kwargs)
        return SimpleNamespace(kind="EMPTY")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", claim_next)
    record_ng_flow = AsyncMock()
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_ng_place_success(
        SimpleNamespace(session=session), ctx["inbox"]
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
        RuntimeIntentKind.COMPLETE,
    ]
    await RuntimeIntentEffectApplier().apply(ctx, intents)

    assert material_unit.status == MaterialUnitStatus.NG
    assert material_unit not in db.deleted
    assert material_unit.current_session_id is None
    assert session.current_material_unit_id is None
    assert session.status == "COMPLETED"
    record_ng_flow.assert_awaited_once()
    assert [call["terminal_status"] for call in ledger_calls] == ["SKIPPED"]
    assert claim_calls == [{"trace_id": "trace-runtime", "demand_id": 11}]


@pytest.mark.asyncio
async def test_cleanup_completed_material_unit_only_clears_current_ng_unit_session_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(id=123, status="COMPLETED", current_material_unit_id=1001)
    completed_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.COMPLETED,
        current_location="TARGET:1",
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=completed_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.COMPLETED.value,
                clear_session_reference=True,
            ),
            RuntimeIntent.complete({}),
        ],
    )

    assert completed_unit not in db.deleted
    assert completed_unit.current_session_id == 123
    assert session.current_material_unit_id == 1001


@pytest.mark.asyncio
async def test_ng_cleanup_survives_cross_batch_recovery_from_manual_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """NG 冲突进 MANUAL_HOLD 后跨 inbox 批次恢复到 COMPLETE 时清理 NG 料盘引用。

    待清理 ID 持久化在 session.context_json，不依赖 ctx 跨批次存活。
    """
    # 第一批次：NG 搬运成功，登记清理 ID 并持久化；Session 尚未 COMPLETED（将被 manual_hold）。
    session = _session(id=123, status="WAITING_DEVICE_RESULT", current_material_unit_id=1001)
    ng_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-NG-001",
        material_identity_key="MAT-NG",
        six_in_one={"PkgID": "PKG-NG-001"},
        status=MaterialUnitStatus.NG,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=ng_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.NG.value,
                clear_session_reference=True,
            ),
        ],
    )

    # 清理 ID 已持久化到 context_json（本批次未 COMPLETE，不触发清理）。
    assert session.context_json["_runtime_pending_material_unit_cleanup_ids"] == [1001]
    assert ng_unit.current_session_id == 123
    assert session.current_material_unit_id == 1001

    # 第二批次：跨 inbox 恢复，ctx 全新，Session 进入 COMPLETED。
    session.status = "COMPLETED"
    ctx2 = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx2,
        [RuntimeIntent.complete({})],
    )

    # 跨批次恢复后仍清理 NG 料盘的 Session 引用，不残留永久孤儿。
    assert ng_unit.current_session_id is None
    assert session.current_material_unit_id is None
    # 持久化登记清空，避免重复处理。
    assert session.context_json["_runtime_pending_material_unit_cleanup_ids"] == []


@pytest.mark.asyncio
async def test_clear_session_reference_does_not_take_over_other_session_material_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(id=123, current_material_unit_id=None)
    other_session_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.NG,
        current_location=None,
        current_session_id=999,
    )
    db = MaterialUnitDb(material_unit=other_session_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", AsyncMock())
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.NG.value,
                clear_session_reference=True,
            ),
            RuntimeIntent.complete({}),
        ],
    )

    assert other_session_unit.current_session_id == 999
    assert other_session_unit not in db.deleted
    assert session.current_material_unit_id is None


@pytest.mark.asyncio
async def test_ng_place_completion_conflict_keeps_material_unit_for_manual_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sorting = _handoff_sorting_context()
    sorting.pop("pending_target_placement", None)
    sorting["current_material"]["ng_status"] = "MOVING_TO_NG"
    session = _session(context_json={"sorting": sorting}, current_material_unit_id=1001)
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "NG-CMD-CONFLICT",
        "command_type": COMMAND_NG_PLACE,
        "result": "SUCCESS",
        "data": {},
        "ng_location": "NG-01",
        "ng_reason_code": "LOCAL_SORTING_NG",
    }
    long_identity = f"test-material:{'X' * 300}"

    async def raise_conflict(*_args: Any, **_kwargs: Any) -> None:
        raise NgMaterialConflictError(
            material_identity_key=long_identity,
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": long_identity,
                "new_source_command_id": 8802,
            },
        )

    async def create_hold(_db: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(id=7701, **kwargs)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        AsyncMock(),
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", AsyncMock())
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", raise_conflict)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_ng_place_success(
        SimpleNamespace(session=session), ctx["inbox"]
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
        RuntimeIntentKind.COMPLETE,
    ]
    await RuntimeIntentEffectApplier().apply(ctx, intents)

    assert session.status == "MANUAL_HOLD"
    assert session.current_material_unit_id == 1001
    assert material_unit.current_session_id == 123
    assert db.deleted == []


@pytest.mark.asyncio
async def test_reconciling_target_place_resource_fact_does_not_record_sorted_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        reconciliation_from_state=None,
        current_location="SORTING:SCAN",
        current_session_id=123,
    )
    session = _session(context_json={"sorting": _handoff_sorting_context()})
    session.current_material_unit_id = 1001
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {"command_code": "TARGET-CMD-RECONCILING"}
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        AsyncMock(),
    )

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert session.status == "MANUAL_HOLD"
    assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "material:PKG-001"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_smt_material_mounted_does_not_record_smt_terminal_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"material_flow": {"context_schema_version": 1}})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={"resource_kind": "BIN_CELL", "resource_key": "generic:cell:1"},
            ),
            RuntimeIntent.update_context({"sorting": {"current_material": {"material_identity_key": "material:GEN"}}}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_smt_material_mounted_without_source_pick_request_does_not_record_terminal_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"sorting": _handoff_sorting_context(include_source_pick_request=False)})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.COMPLETE]
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))
    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_terminal_result_rejects_source_item_bound_to_other_session() -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.PICKED,
        sorting_session_id=999,
        completed_at=None,
        failure_code="OLD_FAILURE",
        failure_message="旧失败",
        next_attempt_at=datetime(2026, 1, 1, 0, 3, 0),
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": item.id,
                },
            }
        },
    )
    service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    db = RecordingDb(demand)

    with pytest.raises(ValueError, match="sorting_session"):
        await service.record_source_item_terminal_result(
            db,
            session=session,
            terminal_status="SORTED",
            trace_id="trace-session-mismatch",
            terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
        )

    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert item.completed_at is None
    assert item.failure_code == "OLD_FAILURE"
    assert "handoff_terminal_result" not in session.context_json["sorting"]


@pytest.mark.asyncio
@pytest.mark.parametrize("session_status", ["FAILED", "CANCELLED"])
async def test_terminal_conflict_keeps_terminal_session_status(
    monkeypatch: pytest.MonkeyPatch,
    session_status: str,
) -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.SORTED,
        sorting_session_id=123,
        completed_at=datetime(2026, 1, 1, 0, 3, 0),
        failure_code=None,
        failure_message=None,
        next_attempt_at=None,
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        status=session_status,
        failure_domain="EXISTING_TERMINAL",
        failure_code="EXISTING_FAILURE",
        failure_message="existing terminal failure",
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": item.id,
                },
            }
        },
    )
    service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    db = RecordingDb(demand)
    persist_manual_hold = AsyncMock()
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        persist_manual_hold,
    )

    result = await service.record_source_item_terminal_result(
        db,
        session=session,
        terminal_status="SKIPPED",
        command_id=9002,
        trace_id="trace-terminal-conflict",
        terminal_evidence={"ng_command_payload": {"command_code": "NG-CMD-001"}},
    )

    assert result.outcome == "manual_hold"
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == "PLUGIN_CONTRACT_INVALID"
    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == "PLUGIN_CONTRACT_INVALID"
    assert session.status == session_status
    assert session.failure_domain == "EXISTING_TERMINAL"
    assert session.failure_code == "EXISTING_FAILURE"
    assert session.failure_message == "existing terminal failure"
    terminal_result = session.context_json["sorting"]["handoff_terminal_result"]
    assert terminal_result["terminal_status"] == "SKIPPED"
    assert terminal_result["conflict"] is True
    assert terminal_result["evidence"]["ng_command_payload"]["command_code"] == "NG-CMD-001"
    persist_manual_hold.assert_not_awaited()
