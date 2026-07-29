"""粗分机 13-case approved fixture 的真实 PostgreSQL 切片证据。"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from sqlalchemy import func, update
from sqlmodel import select

from src.app.callback.services.callback_ingress_service import CallbackIngressService
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.models.material_unit import MaterialUnit
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.ports.inventory_operations import (
    InventoryRecord,
    InventorySnapshotQueryResult,
)
from src.app.wms_integration.ports.query_outcome import QueryBusinessReject, QuerySuccess, QueryTechnicalFailure
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)
from tests.support.wms_query_runtime import bind_stub_wms_query_runtime

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "workline_contract" / "rough_sorter" / "scan_decision_cases.json"
)
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


@dataclass(slots=True)
class CaseEvidence:
    initial_session_status: str
    initial_phase: str
    initial_material_status: str
    initial_command_action: str
    initial_command_status: str
    session_status: str
    phase: str
    material_status: str
    command_action: str
    command_status: str
    outcome_code: str
    reason_code: str | None
    effect_identities: tuple[str, ...]
    effect_count_for_attempt: int
    effect_ledger_identities: tuple[str | None, ...]
    timeline_count: int
    session_failure_code: str | None
    runtime_hold_reason: str | None
    provider_calls: int
    effect_count: int
    runtime_hold_count: int
    replay_effect_delta: int = 0
    replay_outbox_delta: int = 0


def _callback_request(payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/callback/result",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


async def _process_one(db, service: RuntimeInboxService, *, token: str) -> dict[str, int]:  # type: ignore[no-untyped-def]
    claimed = await claim(db, service, token=token)
    result = await processor(service).process_claimed(db, claim=claimed)
    if result["resource_wait"]:
        await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == claimed["id"]).values(next_retry_at=0))
        await db.commit()
        claimed = await claim(db, service, token=f"{token}-retry")
        result = await processor(service).process_claimed(db, claim=claimed)
    return result


def _decision_reason(payload: dict[str, Any]) -> str | None:
    def find_reason(value: object) -> str | None:
        if isinstance(value, dict):
            if isinstance(value.get("reason_code"), str):
                return value["reason_code"]
            for nested in value.values():
                found = find_reason(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = find_reason(nested)
                if found is not None:
                    return found
        return None

    for intent in payload.get("decision", {}).get("intents", []):
        if not isinstance(intent, dict):
            continue
        found = find_reason(intent)
        if found is not None:
            return found
    return None


async def _latest_command(db, *, workline_id: int) -> DeviceCommand | None:  # type: ignore[no-untyped-def]
    return await db.scalar(
        select(DeviceCommand).where(DeviceCommand.workline_id == workline_id).order_by(DeviceCommand.id.desc())
    )


async def _state_tuple(db, *, seeded) -> tuple[str, str, str, str, str]:  # type: ignore[no-untyped-def]
    db.expire_all()
    session = await db.get(WorklineSession, seeded.session_id)
    material = await db.scalar(select(MaterialUnit).where(MaterialUnit.current_session_id == seeded.session_id))
    command = await _latest_command(db, workline_id=seeded.workline_id)
    assert session is not None
    return (
        session.status.value,
        # 粗分机插件未持久化 phase 时的权威初态即 READY；首次 Runner 装载会显式保存同一状态。
        session.plugin_state_json.get("phase", "READY"),
        material.status.value if material is not None else "NOT_CREATED",
        command.task_type if command is not None else "NOT_CREATED",
        getattr(getattr(command, "status", None), "value", getattr(command, "status", "NOT_CREATED")),
    )


async def _collect(
    db,  # type: ignore[no-untyped-def]
    *,
    seeded,
    related_inbox_id: int,
    provider_calls: int,
    initial_state: tuple[str, str, str, str, str],
    replay_effect_delta: int = 0,
    replay_outbox_delta: int = 0,
) -> CaseEvidence:
    db.expire_all()
    session = await db.get(WorklineSession, seeded.session_id)
    material = await db.scalar(select(MaterialUnit).where(MaterialUnit.current_session_id == seeded.session_id))
    command = await _latest_command(db, workline_id=seeded.workline_id)
    timelines = list(
        (
            await db.execute(
                select(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == related_inbox_id)
                .order_by(WorklineTimeline.id)
            )
        ).scalars()
    )
    decision = next(
        (item for item in reversed(timelines) if item.payload_json.get("record_type") == "PLUGIN_DECISION"), None
    )
    decision_payload = decision.payload_json if decision is not None else {}
    decision_data = decision_payload.get("decision", {})
    archive = next((item for item in timelines if item.message == "LATE_COMMAND_RESULT_ARCHIVED"), None)
    timeout = next(
        (item for item in timelines if getattr(item.action_type, "value", item.action_type) == "WAIT_TIMEOUT"), None
    )
    if decision is not None:
        outcome_code = decision_data.get("outcome_code")
        reason_code = _decision_reason(decision_payload)
    elif archive is not None:
        outcome_code = "ARCHIVED_EVIDENCE"
        reason_code = archive.payload_json.get("reason")
    elif timeout is not None:
        outcome_code = "HOLD"
        reason_code = timeout.payload_json.get("reason")
    else:
        raise AssertionError(f"inbox {related_inbox_id} has no authoritative outcome evidence")
    assert isinstance(outcome_code, str)
    intent_effect_identities = tuple(
        str(intent["capability_key"])
        for intent in decision_data.get("intents", [])
        if isinstance(intent, dict) and isinstance(intent.get("capability_key"), str)
    )
    attempt_effects = list(
        (
            await db.execute(
                select(RuntimeIntentLog).where(RuntimeIntentLog.idempotency_key.contains(f":inbox:{related_inbox_id}:"))
            )
        ).scalars()
    )
    hold = await db.scalar(select(RuntimeHold).where(RuntimeHold.session_id == seeded.session_id))
    return CaseEvidence(
        initial_session_status=initial_state[0],
        initial_phase=initial_state[1],
        initial_material_status=initial_state[2],
        initial_command_action=initial_state[3],
        initial_command_status=initial_state[4],
        session_status=session.status.value if session is not None else "NOT_CREATED",
        phase=(session.plugin_state_json.get("phase", "READY") if session is not None else "NOT_CREATED"),
        material_status=material.status.value if material is not None else "NOT_CREATED",
        command_action=command.task_type if command is not None else "NOT_CREATED",
        command_status=getattr(getattr(command, "status", None), "value", getattr(command, "status", "NOT_CREATED")),
        outcome_code=outcome_code,
        reason_code=reason_code,
        effect_identities=intent_effect_identities,
        effect_count_for_attempt=len(attempt_effects),
        effect_ledger_identities=tuple(item.capability_key for item in attempt_effects),
        timeline_count=len(timelines),
        session_failure_code=getattr(session, "failure_code", None),
        runtime_hold_reason=getattr(hold, "source_reason", None),
        provider_calls=provider_calls,
        effect_count=int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0),
        runtime_hold_count=int(await db.scalar(select(func.count()).select_from(RuntimeHold)) or 0),
        replay_effect_delta=replay_effect_delta,
        replay_outbox_delta=replay_outbox_delta,
    )


async def _run_case(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> CaseEvidence:
    case_id = case["case_id"]
    provider_calls = 0
    provider_mode = case.get("trigger", {}).get("decision_discriminator", {}).get("wms_admission")

    async def query_inventory(request):  # type: ignore[no-untyped-def]
        nonlocal provider_calls
        provider_calls += 1
        if provider_mode == "TIMEOUT":
            return QueryTechnicalFailure("WMS_PROVIDER_TIMEOUT", "fixture timeout", True)
        if provider_mode == "REJECT":
            return QueryBusinessReject("WMS_REJECTED", "fixture rejected")
        return QuerySuccess(
            InventorySnapshotQueryResult(
                items=(
                    InventoryRecord(
                        material_code=request.material_code,
                        available_quantity=10,
                        total_quantity=10,
                        reserved_quantity=0,
                        location_code="A-01",
                        lot_no="LOT-IT-001",
                    ),
                ),
                source_version="WMS-IT-1",
            )
        )

    bind_stub_wms_query_runtime(monkeypatch, query_inventory)

    async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)
    captured: CaseEvidence | None = None

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        nonlocal captured
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            service = RuntimeInboxService()
            trigger = case["trigger"]
            event_type = trigger["event_type"]
            related_inbox_id: int
            replay_effect_delta = 0
            replay_outbox_delta = 0
            initial_state = await _state_tuple(db, seeded=seeded)

            if event_type == "SCAN_COMPLETED":
                inbox = await db.get(RuntimeInbox, seeded.inbox_id)
                assert inbox is not None
                inbox.payload_json = {
                    **inbox.payload_json,
                    "data": {
                        **trigger["payload"]["data"],
                        "session_id": seeded.session_id,
                    },
                }
                db.add(inbox)
                await db.commit()
                assert (await _process_one(db, service, token=f"e2e-{case_id}-scan"))["success"] == 1
                related_inbox_id = seeded.inbox_id
            else:
                assert (await _process_one(db, service, token=f"e2e-{case_id}-seed"))["success"] == 1
                pick = await _latest_command(db, workline_id=seeded.workline_id)
                assert pick is not None
                initial_state = await _state_tuple(db, seeded=seeded)
                baseline_effects = int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                baseline_outbox = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)

                if event_type == "COMMAND_RESULT":
                    if case_id == "RS-SD-013":
                        session = await db.get(WorklineSession, seeded.session_id)
                        assert session is not None
                        current = DeviceCommand(
                            command_code="RS-SD-013-CURRENT-WAIT",
                            device_id=seeded.arm_id,
                            task_type="PICK_AND_PUT",
                            priority=5,
                            timeout_ms=30_000,
                            params={},
                            trace_id=seeded.trace_id,
                            correlation_id="rs-sd-013-current",
                            workline_id=seeded.workline_id,
                            plugin_key="rough_sorter",
                            contract_version="rough_sorter.v2",
                            status=CommandStatus.PENDING,
                        )
                        db.add(current)
                        await db.flush()
                        session.awaiting_device_command_code = current.command_code
                        current_command_code = current.command_code
                        await db.commit()
                        callback_payload = {
                            "command_code": pick.command_code,
                            "device_code": "IT-ARM-01",
                            "result": trigger["payload"]["result"],
                            "finish_time": int(time.time() * 1000),
                            "source_event_id": trigger["source_event_id"],
                            "trace_id": seeded.trace_id,
                            "data": {},
                        }
                        response = await CallbackIngressService().handle_result(
                            _callback_request(callback_payload),
                            db,
                            request_id="e2e-RS-SD-013-callback",
                            start_time=time.time(),
                            enqueue_processing=lambda: None,
                        )
                        assert response["code"] == "1000"
                        accepted = await db.scalar(
                            select(RuntimeInbox).where(RuntimeInbox.source_event_id == trigger["source_event_id"])
                        )
                        assert accepted is not None
                        assert accepted.command_id == pick.id
                        assert accepted.status == "RECEIVED", (
                            accepted.status,
                            accepted.last_error_code,
                            accepted.claim_bucket_key,
                            accepted.next_retry_at,
                        )
                        related_inbox_id = int(accepted.id)
                    else:
                        trigger_payload = trigger["payload"]
                        callback_payload = {
                            "command_code": pick.command_code,
                            "device_code": "IT-ARM-01",
                            "result": trigger_payload["result"],
                            "data": {
                                **trigger_payload.get("data", {}),
                                "command_type": trigger_payload.get("command_type"),
                            },
                            "finish_time": int(time.time() * 1000),
                            "source_event_id": trigger["source_event_id"],
                            "trace_id": seeded.trace_id,
                        }
                        if "error_detail" in trigger_payload:
                            callback_payload["error_detail"] = trigger_payload["error_detail"]
                        response = await CallbackIngressService().handle_result(
                            _callback_request(callback_payload),
                            db,
                            request_id=f"e2e-{case_id}-callback",
                            start_time=time.time(),
                            enqueue_processing=lambda: None,
                        )
                        assert response["code"] == "1000"
                        accepted_row = await db.scalar(
                            select(RuntimeInbox).where(RuntimeInbox.source_event_id == trigger["source_event_id"])
                        )
                        assert accepted_row is not None
                        related_inbox_id = int(accepted_row.id)
                    result = await _process_one(db, service, token=f"e2e-{case_id}-result")
                    assert result["processed"] == 1
                    if case_id == "RS-SD-013":
                        accepted = await db.get(RuntimeInbox, related_inbox_id, populate_existing=True)
                        assert accepted is not None and accepted.workline_session_id == seeded.session_id
                        archived = await db.scalar(
                            select(WorklineTimeline).where(
                                WorklineTimeline.related_inbox_id == related_inbox_id,
                                WorklineTimeline.message == "LATE_COMMAND_RESULT_ARCHIVED",
                            )
                        )
                        assert archived is not None
                        session = await db.get(WorklineSession, seeded.session_id, populate_existing=True)
                        assert session is not None and session.awaiting_device_command_code == current_command_code
                        assert archived.payload_json.get("reason") == "COMMAND_RESULT_CORRELATION_MISMATCH"
                        assert (
                            int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                            == baseline_effects
                        )
                elif event_type == "TIMER_TIMEOUT":
                    source = await db.get(RuntimeInbox, seeded.inbox_id)
                    session = await db.get(WorklineSession, seeded.session_id)
                    assert source is not None and session is not None
                    now = timezone.now_for_db()
                    pick.status = CommandStatus.ACK_RECEIVED
                    pick.ack_received_at = now - timedelta(seconds=60)
                    session.deadline_at = now - timedelta(seconds=1)
                    await db.commit()
                    timeout = await service.accept_timer_timeout(
                        db,
                        session_id=seeded.session_id,
                        execution_session_id=source.execution_session_id,
                        workline_id=seeded.workline_id,
                        deadline_at=session.deadline_at,
                        trace_id=seeded.trace_id,
                        wait_token=pick.command_code,
                        wait_type="COMMAND_RESULT",
                        awaiting_device_command_code=pick.command_code,
                        command_code=pick.command_code,
                        device_id=seeded.arm_id,
                        command_id=pick.id,
                        command_status=CommandStatus.ACK_RECEIVED.value,
                        ack_received_at=pick.ack_received_at,
                    )
                    await db.commit()
                    related_inbox_id = int(timeout.record.id)
                    assert (await _process_one(db, service, token=f"e2e-{case_id}-timeout"))["success"] == 1
                elif event_type == "REPLAY_REQUEST":
                    # replay source 必须先走真实 Callback -> Plugin -> QUERY -> EFFECT 生产链，
                    # 不能把 seed scan 或手工 timeline 当作“首次 attempt”。
                    source_event_id = f"e2e-{case_id}-first-result"
                    first_response = await CallbackIngressService().handle_result(
                        _callback_request(
                            {
                                "command_code": pick.command_code,
                                "device_code": "IT-ARM-01",
                                "result": "SUCCESS",
                                "finish_time": 1_700_000_000_000,
                                "source_event_id": source_event_id,
                                "trace_id": seeded.trace_id,
                                "data": {
                                    "command_type": "PICK_AND_PUT",
                                    "measurement_result": "OK",
                                    "reel_diameter": 180,
                                    "reel_thickness": 16,
                                },
                            }
                        ),
                        db,
                        request_id=f"e2e-{case_id}-first-callback",
                        start_time=time.time(),
                        enqueue_processing=lambda: None,
                    )
                    assert first_response["code"] == "1000"
                    source = await db.scalar(
                        select(RuntimeInbox).where(RuntimeInbox.source_event_id == source_event_id)
                    )
                    assert source is not None
                    assert (await _process_one(db, service, token=f"e2e-{case_id}-first-decision"))["success"] == 1
                    source = await db.get(RuntimeInbox, source.id, populate_existing=True)
                    assert source is not None
                    source_id = int(source.id)
                    source_hash = str(source.payload_hash)
                    source_execution_session_id = source.execution_session_id
                    source_correlation_id = source.correlation_id
                    source_timelines = list(
                        (
                            await db.execute(
                                select(WorklineTimeline).where(WorklineTimeline.related_inbox_id == source.id)
                            )
                        ).scalars()
                    )
                    assert any(item.payload_json.get("record_type") == "PLUGIN_DECISION" for item in source_timelines)
                    source_decision = next(
                        item for item in source_timelines if item.payload_json.get("record_type") == "PLUGIN_DECISION"
                    )
                    source_recorded_payloads = tuple(
                        item.payload_json
                        for item in source_timelines
                        if item.payload_json.get("record_type") in {"SYSTEM_CAPABILITY_EVIDENCE", "PLUGIN_DECISION"}
                    )
                    assert source_decision.payload_json.get("evidence_keys")
                    assert (
                        source_decision.payload_json["attempt_anchor"]["logical_idempotency_key"]
                        == trigger["payload"]["idempotency_key"]
                    )
                    assert provider_calls == 1
                    source_effects = tuple(
                        (
                            await db.execute(
                                select(RuntimeIntentLog).where(
                                    RuntimeIntentLog.idempotency_key.contains(f":inbox:{source.id}:")
                                )
                            )
                        ).scalars()
                    )
                    assert source_effects
                    source_effect_ids = tuple(item.idempotency_key for item in source_effects)
                    initial_state = await _state_tuple(db, seeded=seeded)
                    replay_payload = dict(trigger["payload"])
                    if case_id == "RS-SD-011":
                        replay_payload["payload_digest"] = source_hash
                    accepted = await service.accept_internal_event(
                        db,
                        event_type="REPLAY_REQUEST",
                        payload_json={"logical_route": "REPLAY_REQUEST", **replay_payload},
                        trace_id=seeded.trace_id,
                        event_id=trigger["source_event_id"],
                        causation_id=f"inbox:{source_id}",
                        workline_id=seeded.workline_id,
                        execution_session_id=source_execution_session_id,
                        correlation_id=source_correlation_id,
                    )
                    accepted.record.workline_session_id = seeded.session_id
                    await db.commit()
                    related_inbox_id = int(accepted.record.id)
                    baseline_effects = int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                    baseline_outbox = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)
                    result = await _process_one(db, service, token=f"e2e-{case_id}-logical-replay")
                    persisted = await db.get(RuntimeInbox, related_inbox_id, populate_existing=True)
                    assert result["success"] == 1, (
                        result,
                        persisted.status if persisted is not None else None,
                        persisted.last_error_code if persisted is not None else None,
                        persisted.last_error_message if persisted is not None else None,
                    )
                    replay_effect_delta = int(
                        (await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0) - baseline_effects
                    )
                    replay_outbox_delta = int(
                        (await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0) - baseline_outbox
                    )
                    if case_id == "RS-SD-011":
                        captured = await _collect(
                            db,
                            seeded=seeded,
                            related_inbox_id=related_inbox_id,
                            provider_calls=provider_calls,
                            initial_state=initial_state,
                            replay_effect_delta=replay_effect_delta,
                            replay_outbox_delta=replay_outbox_delta,
                        )
                        await db.execute(
                            update(RuntimeInbox)
                            .where(RuntimeInbox.id == source_id)
                            .values(status="DEAD_LETTER", last_error_code="E2E_RECORDED_REPLAY", processor_token=None)
                        )
                        await db.commit()
                        recorded = await service.replay_from_dead_letter(
                            db,
                            source_inbox_id=source_id,
                            request_id="e2e-rs-sd-011-recorded-replay",
                            actor="e2e",
                            reason="verify recorded decision replay",
                        )
                        await db.commit()
                        assert (await _process_one(db, service, token="e2e-rs-sd-011-recorded"))["success"] == 1
                        replay_row = await db.get(RuntimeInbox, recorded.replay_record.id, populate_existing=True)
                        assert replay_row is not None and replay_row.status == "PROCESSED"
                        replay_recorded_payloads = tuple(
                            item.payload_json
                            for item in (
                                await db.execute(
                                    select(WorklineTimeline)
                                    .where(WorklineTimeline.related_inbox_id == recorded.replay_record.id)
                                    .order_by(WorklineTimeline.seq_no)
                                )
                            ).scalars()
                            if item.payload_json.get("record_type") in {"SYSTEM_CAPABILITY_EVIDENCE", "PLUGIN_DECISION"}
                        )
                        assert replay_recorded_payloads == source_recorded_payloads
                        assert provider_calls == 1
                        persisted_effect_ids = tuple(
                            (
                                await db.execute(
                                    select(RuntimeIntentLog.idempotency_key).where(
                                        RuntimeIntentLog.idempotency_key.in_(source_effect_ids)
                                    )
                                )
                            ).scalars()
                        )
                        assert set(persisted_effect_ids) == set(source_effect_ids)
                        assert (
                            int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                            == baseline_effects
                        )
                        return
                    conflict_effects = int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                    conflict_outbox = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)
                    conflict_recorded_payloads = tuple(
                        item.payload_json
                        for item in (
                            await db.execute(
                                select(WorklineTimeline)
                                .where(WorklineTimeline.related_inbox_id == related_inbox_id)
                                .order_by(WorklineTimeline.seq_no)
                            )
                        ).scalars()
                        if item.payload_json.get("record_type") in {"SYSTEM_CAPABILITY_EVIDENCE", "PLUGIN_DECISION"}
                    )
                    conflict_effect_identities = tuple(
                        (
                            await db.execute(
                                select(
                                    RuntimeIntentLog.idempotency_key,
                                    RuntimeIntentLog.capability_key,
                                    RuntimeIntentLog.request_hash,
                                    RuntimeIntentLog.outcome_kind,
                                ).where(RuntimeIntentLog.idempotency_key.contains(f":inbox:{related_inbox_id}:"))
                            )
                        ).all()
                    )
                    assert conflict_recorded_payloads
                    conflict_hold_intent = conflict_recorded_payloads[-1]["decision"]["intents"][0]
                    assert conflict_hold_intent["kind"] == "SYSTEM_CAPABILITY"
                    assert conflict_hold_intent["capability_key"] == "runtime.session_hold"
                    assert _decision_reason(conflict_recorded_payloads[-1]) == "IDEMPOTENCY_CONFLICT"
                    assert conflict_effect_identities
                    await db.execute(
                        update(RuntimeInbox)
                        .where(RuntimeInbox.id == related_inbox_id)
                        .values(status="DEAD_LETTER", last_error_code="E2E_CONFLICT_REPLAY", processor_token=None)
                    )
                    await db.commit()
                    recorded = await service.replay_from_dead_letter(
                        db,
                        source_inbox_id=related_inbox_id,
                        request_id="e2e-rs-sd-012-replay",
                        actor="e2e",
                        reason="fixture conflict replay",
                    )
                    await db.commit()
                    replay_result = await _process_one(db, service, token="e2e-rs-sd-012-recorded-replay")
                    assert replay_result["processed"] == 1
                    replay_row = await db.get(RuntimeInbox, recorded.replay_record.id, populate_existing=True)
                    assert replay_row is not None and replay_row.status == "PROCESSED"
                    replay_recorded_payloads = tuple(
                        item.payload_json
                        for item in (
                            await db.execute(
                                select(WorklineTimeline)
                                .where(WorklineTimeline.related_inbox_id == recorded.replay_record.id)
                                .order_by(WorklineTimeline.seq_no)
                            )
                        ).scalars()
                        if item.payload_json.get("record_type") in {"SYSTEM_CAPABILITY_EVIDENCE", "PLUGIN_DECISION"}
                    )
                    assert replay_recorded_payloads == conflict_recorded_payloads
                    persisted_conflict_effects = tuple(
                        (
                            await db.execute(
                                select(
                                    RuntimeIntentLog.idempotency_key,
                                    RuntimeIntentLog.capability_key,
                                    RuntimeIntentLog.request_hash,
                                    RuntimeIntentLog.outcome_kind,
                                ).where(
                                    RuntimeIntentLog.idempotency_key.in_(
                                        tuple(identity[0] for identity in conflict_effect_identities)
                                    )
                                )
                            )
                        ).all()
                    )
                    assert persisted_conflict_effects == conflict_effect_identities
                    replay_effect_delta = int(
                        (await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0) - conflict_effects
                    )
                    replay_outbox_delta = int(
                        (await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0) - conflict_outbox
                    )

            captured = await _collect(
                db,
                seeded=seeded,
                related_inbox_id=related_inbox_id,
                provider_calls=provider_calls,
                initial_state=initial_state,
                replay_effect_delta=replay_effect_delta,
                replay_outbox_delta=replay_outbox_delta,
            )

    await with_temporary_runtime_database(scenario)
    assert captured is not None
    return captured


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_approved_rough_sorter_scan_decision_case(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """每个 fixture case 均由 production RuntimeInbox/Plugin/QUERY/EFFECT 驱动。"""

    evidence = asyncio.run(_run_case(case, monkeypatch))
    expected_state = case["expected_state"]
    expected_outcome = case["expected_outcome"]
    case_id = case["case_id"]

    expected_session = expected_state["session"]
    expected_phase = expected_state.get("context_phase")
    expected_material = expected_state.get("material")
    assert evidence.session_status == (
        evidence.initial_session_status if expected_session == "UNCHANGED" else expected_session
    ), evidence
    assert evidence.phase == (evidence.initial_phase if expected_phase == "UNCHANGED" else expected_phase), evidence
    assert evidence.material_status == (
        evidence.initial_material_status if expected_material == "UNCHANGED" else expected_material
    ), evidence
    expected_effect_identities: list[str] = []
    expected_intents = case["expected_intents"]
    if any(item["kind"] in {"CREATE_MATERIAL_UNIT", "MARK_NG"} for item in expected_intents):
        expected_effect_identities.append("material_flow.material_unit_write")
    expected_effect_identities.extend(
        "device.device_command_write" for item in expected_intents if item["kind"] == "COMMAND"
    )
    expected_effect_identities.extend("runtime.session_hold" for item in expected_intents if item["kind"] == "BLOCK")
    assert evidence.effect_identities == tuple(expected_effect_identities), evidence
    # RuntimeIntentLog 只保存语义账本；transport 账本由 SystemOutbox 以 dispatch_key 1:1 对应。
    expected_ledger_identities = tuple(expected_effect_identities)
    assert Counter(evidence.effect_ledger_identities) == Counter(expected_ledger_identities), (
        evidence.effect_ledger_identities
    )
    assert evidence.effect_count_for_attempt == len(expected_ledger_identities), evidence
    assert evidence.timeline_count >= 1
    command_intents = [item for item in expected_intents if item["kind"] == "COMMAND"]
    if command_intents:
        assert evidence.command_action == command_intents[-1]["action"]
        assert evidence.command_status == CommandStatus.PENDING.value
    if expected_state.get("command") == "NOT_CREATED":
        assert evidence.command_action == "NOT_CREATED"
    elif expected_state.get("command") == "UNCHANGED":
        assert (evidence.command_action, evidence.command_status) == (
            evidence.initial_command_action,
            evidence.initial_command_status,
        )
    elif expected_state.get("command") is not None:
        assert evidence.command_status == expected_state["command"]
    assert evidence.outcome_code == expected_outcome["result"]
    assert evidence.reason_code == expected_outcome["reason_code"]
    assert evidence.provider_calls == (
        1 if case_id in {"RS-SD-004", "RS-SD-006", "RS-SD-010", "RS-SD-011", "RS-SD-012"} else 0
    )
    if case_id == "RS-SD-009":
        assert evidence.runtime_hold_count == 1
        assert evidence.runtime_hold_reason == expected_outcome["reason_code"]
    elif expected_outcome["result"] == "HOLD":
        assert evidence.session_failure_code == expected_outcome["reason_code"]
    if case_id in {"RS-SD-011", "RS-SD-012", "RS-SD-013"}:
        assert evidence.replay_effect_delta == 0
        assert evidence.replay_outbox_delta == 0
    assert case["implementation_status"] == "covered"
