"""粗分机 DeviceCommand/Outbox 与 logical result 回流证据。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import func, update
from sqlmodel import select

from src.app.callback.models import CallbackLog
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
from src.app.runtime.orchestration.workline_runtime_status_projection import WorklineRuntimeStatusProjection
from src.app.sys.models import SystemOutbox
from src.app.sys.services import AuditLogService
from src.app.wms_integration.adapters import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)


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
            "headers": [(b"content-type", b"application/json"), (b"user-agent", b"workline-pg-test")],
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


async def _process_seeded_scan(db, service: RuntimeInboxService, seeded) -> None:  # type: ignore[no-untyped-def]
    claimed = await claim(db, service, token="outbox-scan-owner")
    result = await processor(service).process_claimed(db, claim=claimed)
    if result["resource_wait"]:
        await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == seeded.inbox_id).values(next_retry_at=0))
        await db.commit()
        claimed = await claim(db, service, token="outbox-scan-retry-owner")
        result = await processor(service).process_claimed(db, claim=claimed)
    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}


def test_outbox_acceptance_is_not_remote_completion_and_callback_is_runtime_inbox(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """accepted/queued/dispatched 均非完成；callback processor 才推进 typed state。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0
        provider_material_codes: list[str] = []

        async def query_inventory(_adapter, request):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            provider_material_codes.append(request.material_code)
            return QuerySuccess(
                InventoryQueryOperationResult(
                    items=(
                        InventoryAuthorityItem(
                            material_code=request.material_code,
                            warehouse_code=request.warehouse_code or "WH-IT",
                            owner_code=request.owner_code,
                            storage_location_code="A-01",
                            available_quantity=10,
                            lot_no="LOT-IT-001",
                        ),
                    ),
                    source_version="WMS-IT-1",
                )
            )

        monkeypatch.setattr(InventoryQueryOperationAdapter, "execute", query_inventory)
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            session = await db.get(WorklineSession, seeded.session_id)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert session is not None and command is not None
            assert session.status == "WAITING_DEVICE_RESULT"
            assert session.awaiting_device_command_code == command.command_code
            assert command.correlation_id == "workline-session:IT-RUNTIME-INBOX-SESSION"
            material_unit = await db.get(MaterialUnit, session.current_material_unit_id)
            assert material_unit is not None
            material_unit.six_in_one = {
                **material_unit.six_in_one,
                "PkgID": "PKG-PERSISTED-SIX-IN-ONE-CONFLICT",
            }
            db.add(material_unit)
            await db.commit()
            command_code = command.command_code

        async with session_factory() as db:
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            assert command is not None and command.status == CommandStatus.PENDING
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            callback_payload = {
                "command_code": command_code,
                "device_code": "IT-ARM-01",
                "result": "SUCCESS",
                "finish_time": int(time.time() * 1000),
                "source_event_id": "it-device-result-1",
                "trace_id": seeded.trace_id,
                "data": {
                    "PkgID": "PKG-CALLBACK-CONFLICT",
                    "HHPN": "MAT-CALLBACK-CONFLICT",
                    "LotCode": "LOT-CALLBACK-CONFLICT",
                    "reel_diameter": "100",
                    "reel_thickness": "10",
                },
            }
            response = await CallbackIngressService().handle_result(
                _callback_request(callback_payload),
                db,
                request_id="it-device-result-request-1",
                start_time=time.time(),
                enqueue_processing=lambda: None,
            )
            assert response["code"] == "1000"
            callback = await db.scalar(
                select(RuntimeInbox).where(
                    RuntimeInbox.provider_code == "ECS",
                    RuntimeInbox.source_event_id == "it-device-result-1",
                )
            )
            assert callback is not None and callback.kind == "COMMAND_RESULT"
            callback_id = int(callback.id)
            assert callback.status == "RECEIVED"
            assert callback.execution_session_id is not None
            assert callback.correlation_id == "workline-session:IT-RUNTIME-INBOX-SESSION"
            await db.refresh(command)
            assert command.status == CommandStatus.COMPLETED
            callback_log = await db.scalar(
                select(CallbackLog).where(CallbackLog.request_id == "it-device-result-request-1")
            )
            assert callback_log is not None and callback_log.ingress_outcome == "ACCEPTED"
            callback_claim = await claim(db, service, token="outbox-callback-owner")
            callback_result = await processor(service).process_claimed(db, claim=callback_claim)
            if callback_result["resource_wait"]:
                await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == callback_id).values(next_retry_at=0))
                await db.commit()
                callback_claim = await claim(db, service, token="outbox-callback-retry-owner")
                callback_result = await processor(service).process_claimed(db, claim=callback_claim)
            persisted_callback = await db.get(RuntimeInbox, callback_id, populate_existing=True)
            assert callback_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }, (
                persisted_callback.status,
                persisted_callback.last_error_code,
                persisted_callback.last_error_message,
            )
            await db.refresh(callback)
            await db.refresh(command)
            await db.refresh(session := await db.get(WorklineSession, seeded.session_id))
            assert callback.status == "PROCESSED"
            assert command.status == CommandStatus.COMPLETED
            assert session.plugin_state_json["phase"] == "MOVING_FORWARD"
            assert session.plugin_state_version == 2
            assert session.status == "WAITING_DEVICE_RESULT"
            assert session.current_wait_type == "COMMAND_RESULT"
            assert provider_calls == 1
            assert provider_material_codes == ["MAT-IT-001"]
            material_unit = await db.get(MaterialUnit, session.current_material_unit_id)
            assert material_unit is not None
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(WorklineTimeline)
                    .where(WorklineTimeline.related_inbox_id == callback.id)
                )
                == 3
            )

            conveyor_command = await db.scalar(
                select(DeviceCommand).where(
                    DeviceCommand.workline_id == seeded.workline_id,
                    DeviceCommand.task_type == "MOVE_FORWARD",
                )
            )
            assert conveyor_command is not None and conveyor_command.status == CommandStatus.PENDING
            assert session.awaiting_device_command_code == conveyor_command.command_code
            assert conveyor_command.params["params"]["business_key"] == material_unit.material_identity_key
            fire_callback_payload = {
                "command_code": conveyor_command.command_code,
                "device_code": "IT-CONVEYOR-01",
                "result": "SUCCESS",
                "finish_time": int(time.time() * 1000),
                "source_event_id": "it-move-forward-result-1",
                "trace_id": seeded.trace_id,
                "data": {},
            }
            fire_response = await CallbackIngressService().handle_result(
                _callback_request(fire_callback_payload),
                db,
                request_id="it-move-forward-request-1",
                start_time=time.time(),
                enqueue_processing=lambda: None,
            )
            assert fire_response["code"] == "1000"
            fire_callback = await db.scalar(
                select(RuntimeInbox).where(RuntimeInbox.source_event_id == "it-move-forward-result-1")
            )
            await db.refresh(conveyor_command)
            await db.refresh(session)
            assert fire_callback is not None and fire_callback.status == "RECEIVED"
            assert conveyor_command.status == CommandStatus.COMPLETED
            fire_claim = await claim(db, service, token="move-forward-callback-owner")
            fire_result = await processor(service).process_claimed(db, claim=fire_claim)
            await db.refresh(fire_callback)
            assert fire_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }, (fire_callback.last_error_code, fire_callback.last_error_message)
            await db.refresh(session)
            assert fire_callback.status == "PROCESSED"
            assert session.plugin_state_json["phase"] == "MOVING_FORWARD"
            fire_decision = await db.scalar(
                select(WorklineTimeline)
                .where(WorklineTimeline.related_inbox_id == fire_callback.id)
                .order_by(WorklineTimeline.id.desc())
            )
            assert session.status == "RUNNING", (
                fire_decision.payload_json.get("decision") if fire_decision is not None else None,
                fire_callback.command_id,
                fire_callback.payload_json,
            )
            assert session.current_wait_type is None
            assert session.awaiting_device_command_code is None
            assert await db.scalar(select(func.count()).select_from(RuntimeHold)) == 0

            # 已消费成功结果后的迟到 timeout 只能归档，不得重新进入 Hold。
            fire_timeout = await service.accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=fire_callback.execution_session_id,
                workline_id=seeded.workline_id,
                deadline_at=timezone.now_for_db(),
                trace_id=seeded.trace_id,
                wait_token=conveyor_command.command_code,
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=conveyor_command.command_code,
                command_code=conveyor_command.command_code,
                device_id=seeded.conveyor_id,
                command_id=conveyor_command.id,
                command_status=CommandStatus.COMPLETED.value,
            )
            await db.commit()
            timeout_claim = await claim(db, service, token="late-completed-command-timeout-owner")
            timeout_result = await processor(service).process_claimed(db, claim=timeout_claim)
            assert timeout_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }
            await db.refresh(session)
            timeout_row = await db.get(RuntimeInbox, fire_timeout.record.id, populate_existing=True)
            assert timeout_row is not None and timeout_row.status == "PROCESSED"
            assert session.plugin_state_json["phase"] == "MOVING_FORWARD"
            assert session.status == "RUNNING"
            assert session.current_wait_type is None
            assert await db.scalar(select(func.count()).select_from(RuntimeHold)) == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_missing_callback_becomes_visible_timeout_without_fake_success() -> None:
    """callback 丢失经真实 TIMER reconciliation 进入唯一 Hold，且不生成插件 RuntimeIntent。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService(audit_service=AuditLogService())
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            await _process_seeded_scan(db, service, seeded)
            session = await db.get(WorklineSession, seeded.session_id)
            source_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id))
            assert command is not None and session is not None
            assert source_inbox is not None and session.status == "WAITING_DEVICE_RESULT"
            baseline_intents = tuple(
                (await db.execute(select(RuntimeIntentLog).order_by(RuntimeIntentLog.id))).scalars()
            )
            assert tuple(intent.capability_key for intent in baseline_intents) == (
                "material_flow.material_unit_write",
                "device.device_command_write",
            )

            now = timezone.now_for_db()
            command.status = CommandStatus.ACK_RECEIVED
            command.ack_received_at = now - timedelta(seconds=60)
            session.deadline_at = now - timedelta(seconds=1)
            await db.commit()

            timeout = await service.accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=source_inbox.execution_session_id,
                workline_id=seeded.workline_id,
                deadline_at=session.deadline_at,
                trace_id=seeded.trace_id,
                wait_token=command.command_code,
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=command.command_code,
                command_code=command.command_code,
                device_id=seeded.arm_id,
                command_id=command.id,
                command_status=CommandStatus.ACK_RECEIVED.value,
                ack_received_at=command.ack_received_at,
            )
            await db.commit()
            duplicate = await service.accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=source_inbox.execution_session_id,
                workline_id=seeded.workline_id,
                deadline_at=session.deadline_at,
                trace_id=seeded.trace_id,
                wait_token=command.command_code,
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=command.command_code,
                command_code=command.command_code,
                device_id=seeded.arm_id,
                command_id=command.id,
                command_status=CommandStatus.ACK_RECEIVED.value,
                ack_received_at=command.ack_received_at,
            )
            assert duplicate.created is False and duplicate.record.id == timeout.record.id

            timeout_claim = await claim(db, service, token="outbox-timeout-owner")
            result = await processor(service).process_claimed(db, claim=timeout_claim)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}

        async with session_factory() as verify_db:
            timer = await verify_db.get(RuntimeInbox, timeout.record.id)
            session = await verify_db.get(WorklineSession, seeded.session_id)
            command = await verify_db.get(DeviceCommand, command.id)
            hold = await verify_db.scalar(select(RuntimeHold).where(RuntimeHold.session_id == seeded.session_id))
            assert timer is not None and timer.kind == "TIMER_TIMEOUT" and timer.status == "PROCESSED"
            assert session is not None and session.status == "MANUAL_HOLD"
            assert session.reconciliation_reason == "CALLBACK_DEADLINE_EXPIRED"
            assert hold is not None and hold.source_reason == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"
            assert command is not None and command.status != CommandStatus.COMPLETED
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeHold)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == len(baseline_intents)

            await verify_db.execute(
                update(RuntimeInbox)
                .where(RuntimeInbox.id == timer.id)
                .values(status="DEAD_LETTER", last_error_code="IT_TIMEOUT_REPLAY", processor_token=None)
            )
            await verify_db.execute(
                update(WorklineRuntimeStatusProjection)
                .where(WorklineRuntimeStatusProjection.workline_id == seeded.workline_id)
                .values(runtime_status="READY", stopped_reason=None)
            )
            await verify_db.commit()
            replay = await service.replay_from_dead_letter(
                verify_db,
                source_inbox_id=int(timer.id),
                request_id="it-timeout-recorded-replay",
                actor="integration",
                reason="verify timeout recorded replay has zero new hold",
            )
            replay_id = int(replay.replay_record.id)
            await verify_db.commit()
            replay_claim = await claim(verify_db, service, token="timeout-recorded-replay-owner")
            replay_result = await processor(service).process_claimed(verify_db, claim=replay_claim)
            assert replay_result["processed"] == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeHold)) == 1
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == len(baseline_intents)
            replay_row = await verify_db.get(RuntimeInbox, replay_id, populate_existing=True)
            assert replay_row is not None and replay_row.status == "DEAD_LETTER"
            assert replay_row.last_error_code == "RECORDED_REPLAY_RECORD_MISSING"

    asyncio.run(with_temporary_runtime_database(scenario))


@pytest.mark.parametrize(
    ("action", "terminal_result"),
    [
        ("MOVE_FORWARD", "SUCCESS"),
        ("MOVE_FORWARD", "FAILED"),
        ("MOVE_FORWARD", "TIMEOUT"),
        ("MOVE_TO_NG", "SUCCESS"),
        ("MOVE_TO_NG", "FAILED"),
        ("MOVE_TO_NG", "TIMEOUT"),
    ],
)
def test_followup_device_command_requires_terminal_result(action: str, terminal_result: str, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """后续设备命令的成功、失败、超时都经 RuntimeInbox 推进，不存在假完成。"""

    async def query_inventory(_adapter, request):  # type: ignore[no-untyped-def]
        return QuerySuccess(
            InventoryQueryOperationResult(
                items=(
                    InventoryAuthorityItem(
                        material_code=request.material_code,
                        warehouse_code=request.warehouse_code or "WH-IT",
                        owner_code=request.owner_code,
                        storage_location_code="A-01",
                        available_quantity=10,
                        lot_no="LOT-IT-001",
                    ),
                ),
                source_version="WMS-IT-1",
            )
        )

    monkeypatch.setattr(InventoryQueryOperationAdapter, "execute", query_inventory)

    async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            if action == "MOVE_TO_NG":
                scan = await db.get(RuntimeInbox, seeded.inbox_id)
                assert scan is not None
                scan.payload_json = {
                    **scan.payload_json,
                    "data": {**scan.payload_json["data"], "PkgID": "PKG-SIZENG-FOLLOWUP"},
                }
                await db.commit()
            await _process_seeded_scan(db, service, seeded)

            if action == "MOVE_FORWARD":
                pick = await db.scalar(
                    select(DeviceCommand).where(
                        DeviceCommand.workline_id == seeded.workline_id,
                        DeviceCommand.task_type == "PICK_AND_PUT",
                    )
                )
                assert pick is not None
                prepare_event_id = f"prepare-{action}-{terminal_result}"
                prepared_response = await CallbackIngressService().handle_result(
                    _callback_request(
                        {
                            "command_code": pick.command_code,
                            "device_code": "IT-ARM-01",
                            "result": "SUCCESS",
                            "data": {"reel_diameter": "100", "reel_thickness": "10"},
                            "finish_time": int(time.time() * 1000),
                            "source_event_id": prepare_event_id,
                            "trace_id": seeded.trace_id,
                        }
                    ),
                    db,
                    request_id=f"prepare-{action}-{terminal_result}-request",
                    start_time=time.time(),
                    enqueue_processing=lambda: None,
                )
                assert prepared_response["code"] == "1000"
                prepared_inbox = await db.scalar(
                    select(RuntimeInbox).where(RuntimeInbox.source_event_id == prepare_event_id)
                )
                assert prepared_inbox is not None
                prepared = await claim(db, service, token=f"prepare-{terminal_result}")
                assert (await processor(service).process_claimed(db, claim=prepared))["success"] == 1

            command = await db.scalar(
                select(DeviceCommand).where(
                    DeviceCommand.workline_id == seeded.workline_id,
                    DeviceCommand.task_type == action,
                )
            )
            session = await db.get(WorklineSession, seeded.session_id)
            source = await db.get(RuntimeInbox, seeded.inbox_id)
            assert command is not None and session is not None and source is not None
            assert session.status == "WAITING_DEVICE_RESULT"
            assert session.awaiting_device_command_code == command.command_code

            terminal_event_id = f"terminal-{action}-{terminal_result}"
            if terminal_result == "TIMEOUT":
                now = timezone.now_for_db()
                command.status = CommandStatus.ACK_RECEIVED
                command.ack_received_at = now - timedelta(seconds=60)
                session.deadline_at = now - timedelta(seconds=1)
                await db.commit()
                accepted_timeout = await service.accept_timer_timeout(
                    db,
                    session_id=seeded.session_id,
                    execution_session_id=source.execution_session_id,
                    workline_id=seeded.workline_id,
                    deadline_at=session.deadline_at,
                    trace_id=seeded.trace_id,
                    wait_token=command.command_code,
                    wait_type="COMMAND_RESULT",
                    awaiting_device_command_code=command.command_code,
                    command_code=command.command_code,
                    device_id=command.device_id,
                    command_id=command.id,
                    command_status=CommandStatus.ACK_RECEIVED.value,
                    ack_received_at=command.ack_received_at,
                )
                await db.commit()
                terminal_inbox_id = int(accepted_timeout.record.id)
            else:
                callback_response = await CallbackIngressService().handle_result(
                    _callback_request(
                        {
                            "command_code": command.command_code,
                            "device_code": "IT-CONVEYOR-01" if action == "MOVE_FORWARD" else "IT-ARM-01",
                            "result": terminal_result,
                            "error_detail": {"error_code": "DEVICE_BUSY"} if terminal_result == "FAILED" else {},
                            "data": {},
                            "finish_time": int(time.time() * 1000),
                            "source_event_id": terminal_event_id,
                            "trace_id": seeded.trace_id,
                        }
                    ),
                    db,
                    request_id=f"{terminal_event_id}-request",
                    start_time=time.time(),
                    enqueue_processing=lambda: None,
                )
                assert callback_response["code"] == "1000"
                accepted_inbox = await db.scalar(
                    select(RuntimeInbox).where(RuntimeInbox.source_event_id == terminal_event_id)
                )
                callback_log = await db.scalar(
                    select(CallbackLog).where(CallbackLog.request_id == f"{terminal_event_id}-request")
                )
                assert accepted_inbox is not None and callback_log is not None
                assert callback_log.ingress_outcome == "ACCEPTED"
                terminal_inbox_id = int(accepted_inbox.id)
                await db.refresh(command)
                expected_terminal_status = (
                    CommandStatus.COMPLETED if terminal_result == "SUCCESS" else CommandStatus.FAILED
                )
                assert command.status == expected_terminal_status

            terminal_claim = await claim(db, service, token=f"terminal-{action}-{terminal_result}")
            terminal_processed = await processor(service).process_claimed(db, claim=terminal_claim)
            assert terminal_processed["processed"] == 1
            await db.refresh(session)
            await db.refresh(command)
            hold_count = int(await db.scalar(select(func.count()).select_from(RuntimeHold)) or 0)
            timelines = list(
                (
                    await db.execute(
                        select(WorklineTimeline)
                        .where(WorklineTimeline.related_inbox_id == terminal_inbox_id)
                        .order_by(WorklineTimeline.id)
                    )
                ).scalars()
            )
            assert timelines
            if terminal_result == "SUCCESS":
                decision = next(row for row in timelines if row.payload_json.get("record_type") == "PLUGIN_DECISION")
                assert decision.payload_json["decision"]["outcome_code"] == f"{action}_COMPLETED"
                assert command.status == CommandStatus.COMPLETED
                assert session.status == "RUNNING"
                assert session.current_wait_type is None
                assert session.awaiting_device_command_code is None
                assert hold_count == 0
            elif terminal_result == "FAILED":
                decision = next(row for row in timelines if row.payload_json.get("record_type") == "PLUGIN_DECISION")
                assert decision.payload_json["decision"]["outcome_code"] == "HOLD"
                assert "DEVICE_BUSY" in json.dumps(decision.payload_json, ensure_ascii=False)
                assert command.status == CommandStatus.FAILED
                assert session.status == "MANUAL_HOLD"
                assert session.current_wait_type is None
                assert session.awaiting_device_command_code is None
                # 普通设备失败由 runtime.session_hold 只更新 Session，不创建 RuntimeHold。
                assert hold_count == 0
            else:
                timeout_timeline = next(
                    row for row in timelines if getattr(row.action_type, "value", row.action_type) == "WAIT_TIMEOUT"
                )
                assert timeout_timeline.payload_json["reason"] == "CALLBACK_DEADLINE_EXPIRED"
                assert command.status == CommandStatus.ACK_RECEIVED
                assert session.status == "MANUAL_HOLD"
                assert session.current_wait_type is None
                assert session.awaiting_device_command_code is None
                hold = await db.scalar(select(RuntimeHold).where(RuntimeHold.session_id == seeded.session_id))
                assert hold is not None and hold.source_reason == "CALLBACK_DEADLINE_EXPIRED"
                assert hold_count == 1

    asyncio.run(with_temporary_runtime_database(scenario))
