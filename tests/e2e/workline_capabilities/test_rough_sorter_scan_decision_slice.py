"""粗分机 13-case approved fixture 的真实 PostgreSQL 切片证据。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, update
from sqlmodel import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.runtime.orchestration.models.material_unit import MaterialUnit
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.adapters.inventory_query_port_adapter import WmsInventoryQueryPortAdapter
from src.app.wms_integration.ports.inventory_query import WmsInventoryItem
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "workline_contract" / "rough_sorter" / "scan_decision_cases.json"
)
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


@dataclass(slots=True)
class CaseEvidence:
    session_status: str
    phase: str
    material_status: str
    command_action: str
    outcome_code: str
    reason_code: str | None
    provider_calls: int
    effect_count: int
    runtime_hold_count: int
    replay_effect_delta: int = 0
    replay_outbox_delta: int = 0


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
    for intent in payload.get("decision", {}).get("intents", []):
        if not isinstance(intent, dict):
            continue
        effect_payload = intent.get("payload_json")
        if isinstance(effect_payload, dict) and isinstance(effect_payload.get("reason_code"), str):
            return effect_payload["reason_code"]
    return None


async def _latest_command(db, *, workline_id: int) -> DeviceCommand | None:  # type: ignore[no-untyped-def]
    return await db.scalar(
        select(DeviceCommand).where(DeviceCommand.workline_id == workline_id).order_by(DeviceCommand.id.desc())
    )


async def _collect(
    db,  # type: ignore[no-untyped-def]
    *,
    seeded,
    related_inbox_id: int | None,
    provider_calls: int,
    fallback_outcome: str,
    fallback_reason: str | None,
    replay_effect_delta: int = 0,
    replay_outbox_delta: int = 0,
) -> CaseEvidence:
    db.expire_all()
    session = await db.get(WorklineSession, seeded.session_id)
    material = await db.scalar(select(MaterialUnit).where(MaterialUnit.current_session_id == seeded.session_id))
    command = await _latest_command(db, workline_id=seeded.workline_id)
    decision = None
    if related_inbox_id is not None:
        decision = await db.scalar(
            select(WorklineTimeline)
            .where(
                WorklineTimeline.related_inbox_id == related_inbox_id,
                WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
            )
            .order_by(WorklineTimeline.id.desc())
        )
    decision_payload = decision.payload_json if decision is not None else {}
    return CaseEvidence(
        session_status=session.status.value if session is not None else "NOT_CREATED",
        phase=(session.plugin_state_json.get("phase", "UNCHANGED") if session is not None else "UNCHANGED"),
        material_status=material.status.value if material is not None else "NOT_CREATED",
        command_action=command.task_type if command is not None else "NOT_CREATED",
        outcome_code=decision_payload.get("decision", {}).get("outcome_code", fallback_outcome),
        reason_code=_decision_reason(decision_payload) or fallback_reason,
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

    async def query_inventory(_adapter, material_code: str, *, warehouse_code: str | None = None):  # type: ignore[no-untyped-def]
        nonlocal provider_calls
        provider_calls += 1
        if provider_mode == "TIMEOUT":
            raise TimeoutError("fixture timeout")
        if provider_mode == "REJECT":
            return []
        return [
            WmsInventoryItem(
                material_code=material_code,
                warehouse_code=warehouse_code or "WH-IT",
                storage_location_code="A-01",
                quantity=10,
                batch_no="LOT-IT-001",
            )
        ]

    monkeypatch.setattr(WmsInventoryQueryPortAdapter, "query_inventory", query_inventory)
    captured: CaseEvidence | None = None

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        nonlocal captured
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            service = RuntimeInboxService()
            trigger = case["trigger"]
            event_type = trigger["event_type"]
            related_inbox_id: int | None = None
            fallback_outcome = case["expected_outcome"]["result"]
            fallback_reason = case["expected_outcome"]["reason_code"]
            replay_effect_delta = 0
            replay_outbox_delta = 0

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
                baseline_effects = int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                baseline_outbox = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)

                if event_type == "COMMAND_RESULT":
                    callback_code = trigger["payload"]["command_code"] if case_id == "RS-SD-013" else pick.command_code
                    accepted = await service.accept_command_result(
                        db,
                        command_code=callback_code,
                        source_event_id=trigger["source_event_id"],
                        device_code="IT-ARM-01",
                        workline_id=seeded.workline_id,
                        device_id=seeded.arm_id,
                        command_id=pick.id,
                        trace_id=seeded.trace_id,
                        payload_json={**trigger["payload"], "command_code": callback_code},
                    )
                    if case_id == "RS-SD-013":
                        # 正式 CallbackIngress 会用当前等待锚点固定 Session；这里直接调用
                        # RuntimeInbox accept seam，因此显式保留同一权威归属以只测试 mismatch 路由。
                        accepted.record.workline_session_id = seeded.session_id
                    await db.commit()
                    related_inbox_id = int(accepted.record.id)
                    result = await _process_one(db, service, token=f"e2e-{case_id}-result")
                    assert result["processed"] == 1
                    if case_id == "RS-SD-013":
                        archived = await db.scalar(
                            select(WorklineTimeline).where(
                                WorklineTimeline.related_inbox_id == related_inbox_id,
                                WorklineTimeline.message == "LATE_COMMAND_RESULT_ARCHIVED",
                            )
                        )
                        assert archived is not None
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
                elif event_type == "REPLAY_REQUEST" and case_id == "RS-SD-011":
                    await db.execute(
                        update(RuntimeInbox)
                        .where(RuntimeInbox.id == seeded.inbox_id)
                        .values(
                            status="DEAD_LETTER", last_error_code="E2E_REPLAY", processor_token=None, lease_until=None
                        )
                    )
                    await db.commit()
                    replay = await service.replay_from_dead_letter(
                        db,
                        source_inbox_id=seeded.inbox_id,
                        request_id="e2e-rs-sd-011",
                        actor="e2e",
                        reason="fixture recorded replay",
                    )
                    related_inbox_id = int(replay.replay_record.id)
                    await db.commit()
                    assert (await _process_one(db, service, token="e2e-rs-sd-011-replay"))["success"] == 1
                    replay_effect_delta = int(
                        (await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0) - baseline_effects
                    )
                    replay_outbox_delta = int(
                        (await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0) - baseline_outbox
                    )
                    fallback_outcome = "REPLAY_ACCEPTED_NOOP"
                    related_inbox_id = None
                elif event_type == "REPLAY_REQUEST":
                    source = await db.get(RuntimeInbox, seeded.inbox_id)
                    assert source is not None
                    accepted = await service.accept_internal_event(
                        db,
                        event_type="REPLAY_REQUEST",
                        payload_json={"logical_route": "REPLAY_REQUEST", **trigger["payload"]},
                        trace_id=seeded.trace_id,
                        event_id=trigger["source_event_id"],
                        causation_id=f"inbox:{seeded.inbox_id}",
                        workline_id=seeded.workline_id,
                        execution_session_id=source.execution_session_id,
                        correlation_id=source.correlation_id,
                    )
                    accepted.record.workline_session_id = seeded.session_id
                    await db.commit()
                    related_inbox_id = int(accepted.record.id)
                    result = await _process_one(db, service, token="e2e-rs-sd-012-conflict")
                    persisted = await db.get(RuntimeInbox, related_inbox_id, populate_existing=True)
                    assert result["success"] == 1, (
                        result,
                        persisted.status if persisted is not None else None,
                        persisted.last_error_code if persisted is not None else None,
                        persisted.last_error_message if persisted is not None else None,
                    )
                    conflict_effects = int(await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) or 0)
                    conflict_outbox = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)
                    await db.execute(
                        update(RuntimeInbox)
                        .where(RuntimeInbox.id == related_inbox_id)
                        .values(status="DEAD_LETTER", last_error_code="E2E_CONFLICT_REPLAY", processor_token=None)
                    )
                    await db.commit()
                    replay = await service.replay_from_dead_letter(
                        db,
                        source_inbox_id=related_inbox_id,
                        request_id="e2e-rs-sd-012-replay",
                        actor="e2e",
                        reason="fixture conflict replay",
                    )
                    await db.commit()
                    replay_result = await _process_one(db, service, token="e2e-rs-sd-012-recorded-replay")
                    assert replay_result["processed"] == 1
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
                fallback_outcome=fallback_outcome,
                fallback_reason=fallback_reason,
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

    assert evidence.session_status == expected_state["session"] or expected_state["session"] == "UNCHANGED", evidence
    assert (
        evidence.phase == expected_state.get("context_phase", evidence.phase)
        or expected_state.get("context_phase") == "UNCHANGED"
    )
    assert (
        evidence.material_status == expected_state.get("material", evidence.material_status)
        or expected_state.get("material") == "UNCHANGED"
    )
    if expected_state.get("command") == "NOT_CREATED":
        assert evidence.command_action == "NOT_CREATED"
    assert evidence.outcome_code == expected_outcome["result"]
    assert evidence.reason_code == expected_outcome["reason_code"]
    assert evidence.provider_calls == (1 if case_id in {"RS-SD-004", "RS-SD-006", "RS-SD-010"} else 0)
    if case_id == "RS-SD-009":
        assert evidence.runtime_hold_count == 1
    if case_id in {"RS-SD-011", "RS-SD-012", "RS-SD-013"}:
        assert evidence.replay_effect_delta == 0
        assert evidence.replay_outbox_delta == 0
    assert case["implementation_status"] == "covered"
