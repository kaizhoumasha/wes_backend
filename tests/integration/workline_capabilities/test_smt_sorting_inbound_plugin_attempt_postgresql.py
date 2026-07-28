"""SMT generated Plugin mandatory binding 的 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import fields as dataclass_fields
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.callback.services.callback_ingress_service import CallbackIngressService
from src.app.device.models.command import CommandResult, DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.handler import (
    SmtSourcePickCommandHandler,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.sys.models import SystemOutbox
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.smt_sorting_inbound_postgresql import (
    NoopQueueGateway,
    process_smt_source_pick_claim,
    seed_smt_source_pick_claim,
    snapshot_smt_write_set,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.support.smt_sorting_inbound_postgresql import RowSnapshot, SmtWriteSetSnapshot


def _changed_snapshot_attributes(before: SmtWriteSetSnapshot, after: SmtWriteSetSnapshot) -> set[str]:
    return {
        field.name for field in dataclass_fields(before) if getattr(before, field.name) != getattr(after, field.name)
    }


def _snapshot_row_by_id(rows: RowSnapshot, row_id: int) -> dict[str, object]:
    for row in rows:
        values = dict(row)
        if values.get("id") == row_id:
            return values
    raise AssertionError(f"snapshot row not found: id={row_id}")


def _changed_row_fields(before: dict[str, object], after: dict[str, object]) -> set[str]:
    assert before.keys() == after.keys()
    return {field for field in before if before[field] != after[field]}


def _row_identity(row: dict[str, object]) -> object:
    if row.get("id") is not None:
        return row["id"]
    return (
        row.get("provider_code"),
        row.get("operation_kind"),
        row.get("idempotency_key"),
    )


def _row_delta(
    before: RowSnapshot,
    after: RowSnapshot,
) -> tuple[dict[object, dict[str, object]], dict[object, set[str]], set[object]]:
    before_by_id = {_row_identity(dict(row)): dict(row) for row in before}
    after_by_id = {_row_identity(dict(row)): dict(row) for row in after}
    added = {identity: after_by_id[identity] for identity in after_by_id.keys() - before_by_id.keys()}
    changed = {
        identity: _changed_row_fields(before_by_id[identity], after_by_id[identity])
        for identity in before_by_id.keys() & after_by_id.keys()
        if before_by_id[identity] != after_by_id[identity]
    }
    return added, changed, before_by_id.keys() - after_by_id.keys()


def _assert_row_delta(
    before: RowSnapshot,
    after: RowSnapshot,
    *,
    added_count: int = 0,
    changed: dict[object, set[str]] | None = None,
) -> list[dict[str, object]]:
    added_rows, changed_rows, removed_rows = _row_delta(before, after)
    assert len(added_rows) == added_count
    assert changed_rows == (changed or {})
    assert removed_rows == set()
    return list(added_rows.values())


async def _run_postgresql_scenario(scenario: Callable[[Any], Awaitable[None]]) -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(session_factory)
        finally:
            await engine.dispose()


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
            "headers": [(b"content-type", b"application/json"), (b"user-agent", b"smt-public-pg-test")],
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


def _callback_payload(
    *,
    processed: Any,
    result: CommandResult,
    source_event_id: str,
) -> dict[str, object]:
    return {
        "command_code": processed.command.command_code,
        "device_code": processed.seeded.device_code,
        "result": result.value,
        "finish_time": int(time.time() * 1_000),
        "source_event_id": source_event_id,
        "trace_id": processed.command.trace_id,
        "data": {"command_type": "FORGED_CALLBACK_VALUE"},
        "error_detail": {"code": "DEVICE_FAILURE"} if result is CommandResult.FAILED else None,
    }


async def _submit_public_callback(
    db: AsyncSession,
    *,
    processed: Any,
    payload: dict[str, object],
    request_id: str,
    source_event_id: str,
) -> tuple[RuntimeInbox, int]:
    enqueued = 0

    def enqueue_processing() -> None:
        nonlocal enqueued
        enqueued += 1

    response = await CallbackIngressService().handle_result(
        _callback_request(payload),
        db,
        request_id=request_id,
        start_time=time.time(),
        enqueue_processing=enqueue_processing,
    )
    assert response["code"] == "1000", response
    callback_inbox = await db.scalar(
        select(RuntimeInbox).where(
            RuntimeInbox.kind == "COMMAND_RESULT",
            RuntimeInbox.command_id == processed.command.id,
            RuntimeInbox.source_event_id == source_event_id,
        )
    )
    assert callback_inbox is not None
    assert callback_inbox.command_id == processed.command.id
    assert callback_inbox.correlation_id == processed.command.correlation_id
    assert callback_inbox.execution_session_id == processed.source_inbox.execution_session_id
    return callback_inbox, enqueued


async def _process_public_callback(
    db: AsyncSession,
    *,
    callback_inbox: RuntimeInbox,
    processor_token: str,
) -> None:
    assert callback_inbox.status == "RECEIVED"
    [claim] = await RuntimeInboxService().claim_for_processing(
        db,
        limit=1,
        processor_token=processor_token,
        stale_after_seconds=60,
    )
    assert claim["id"] == callback_inbox.id
    await db.commit()
    result = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
        db,
        claim=claim,
    )
    assert result["success"] == 1, result
    persisted = await db.get(RuntimeInbox, callback_inbox.id, populate_existing=True)
    assert persisted is not None
    assert persisted.status == "PROCESSED", (persisted.last_error_code, persisted.last_error_message)


async def _clear_source_pick_correlation_for_recovery(
    db: AsyncSession,
    *,
    source_item_id: int,
    command_code: str,
) -> None:
    source_item = await db.get(SmtInboundHandoffSourceItem, source_item_id)
    assert source_item is not None
    assert source_item.sorting_session_id is not None
    await db.execute(
        update(SmtInboundHandoffSourceItem)
        .where(SmtInboundHandoffSourceItem.id == source_item_id)
        .values(
            status=SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING,
            source_pick_command_id=None,
            source_pick_command_code=None,
            source_pick_dispatch_key=None,
            failure_code=None,
            failure_message=None,
            completed_at=None,
            updated_at=timezone.now_for_db() - timedelta(minutes=10),
        )
    )
    await db.execute(
        update(WorklineSession)
        .where(WorklineSession.id == source_item.sorting_session_id)
        .values(
            status=SessionStatus.WAITING_DEVICE_RESULT,
            current_wait_type="COMMAND_RESULT",
            awaiting_device_command_code=command_code,
        )
    )
    await db.commit()


@pytest.mark.parametrize(
    ("result", "expected_generated_outcome"),
    [
        (
            CommandResult.SUCCESS,
            "SOURCE_PICK_COMPLETED",
        ),
        (
            CommandResult.FAILED,
            "SOURCE_PICK_FAILED",
        ),
    ],
    ids=["success", "device-failure"],
)
def test_smt_public_callback_applies_exact_generated_effects(
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    expected_generated_outcome: str,
) -> None:
    async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
        return None

    # PostgreSQL closure 覆盖真实 public ingress 与持久化链；Redis cache 不属于写集，
    # 且参数化 scenario 各自使用独立 event loop，故只隔离 cache invalidation I/O。
    monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)

    async def scenario(session_factory: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_smt_source_pick_claim(db, suffix=f"closure-{result.value.lower()}")
            processed = await process_smt_source_pick_claim(db, seeded)
            assert processed.source_item.source_pick_command_id == processed.command.id
            assert processed.source_inbox.status == "PROCESSED"
            assert processed.outbox.dispatch_key == f"device-command:{processed.command.command_code}"

            callback_payload = _callback_payload(
                processed=processed,
                result=result,
                source_event_id=f"smt-pg-callback-{result.value.lower()}",
            )
            before_callback = await snapshot_smt_write_set(db)
            assert isinstance(before_callback.idempotency_keys, tuple)
            assert isinstance(before_callback.diagnostics, tuple)
            assert isinstance(before_callback.runtime_status_projections, tuple)
            callback_inbox, first_enqueue_count = await _submit_public_callback(
                db,
                processed=processed,
                payload=callback_payload,
                request_id=f"request-smt-pg-{result.value.lower()}",
                source_event_id=f"smt-pg-callback-{result.value.lower()}",
            )
            assert first_enqueue_count == 1
            await _process_public_callback(
                db,
                callback_inbox=callback_inbox,
                processor_token=f"lease-public-callback-{result.value.lower()}",
            )
            # Public processor 返回后立即提交并关闭当前 Session；所有 typed effect
            # 断言必须来自新 Session 的数据库事实，不能依赖 identity map。
            await db.commit()
            callback_inbox_id = int(callback_inbox.id)

        async with session_factory() as db:
            after_callback = await snapshot_smt_write_set(db)
            assert len(after_callback.commands) == len(before_callback.commands)
            assert len(after_callback.outboxes) == len(before_callback.outboxes)
            callback_inbox = await db.get(RuntimeInbox, callback_inbox_id)
            assert callback_inbox is not None
            assert callback_inbox.status == "PROCESSED"
            assert callback_inbox.command_id == processed.command.id
            assert callback_inbox.correlation_id == processed.command.correlation_id
            assert callback_inbox.execution_session_id == processed.source_inbox.execution_session_id
            decision = await db.scalar(
                select(WorklineTimeline)
                .where(
                    WorklineTimeline.related_inbox_id == callback_inbox.id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
                )
                .order_by(WorklineTimeline.id.desc())
            )
            assert decision is not None
            assert decision.payload_json["decision"]["outcome_code"] == expected_generated_outcome
            expected_command_fields = {
                "status",
                "result",
                "result_data",
                "completed_at",
                "updated_at",
            }
            if result is CommandResult.FAILED:
                expected_command_fields.add("error_detail")
            _assert_row_delta(
                before_callback.commands,
                after_callback.commands,
                changed={processed.command.id: expected_command_fields},
            )
            [callback_inbox_row] = _assert_row_delta(
                before_callback.runtime_inboxes,
                after_callback.runtime_inboxes,
                added_count=1,
            )
            assert callback_inbox_row["id"] == callback_inbox.id
            assert callback_inbox_row["status"] == "PROCESSED"
            assert callback_inbox_row["command_id"] == processed.command.id
            assert callback_inbox_row["correlation_id"] == processed.command.correlation_id
            assert callback_inbox_row["execution_session_id"] == processed.source_inbox.execution_session_id

            timeline_rows = _assert_row_delta(
                before_callback.timelines,
                after_callback.timelines,
                added_count=2,
            )
            assert {getattr(row["action_type"], "value", row["action_type"]) for row in timeline_rows} == {
                "COMMAND_ACKED",
                "DECISION_MADE",
            }
            assert {row["related_inbox_id"] for row in timeline_rows} == {callback_inbox.id}
            assert sum(row["related_command_id"] == processed.command.id for row in timeline_rows) == 1

            expected_session_fields = {
                "status",
                "current_wait_type",
                "awaiting_device_command_code",
                "waiting_since",
                "current_wait_timeout_seconds",
                "last_inbox_id",
                "plugin_state_version",
                "updated_at",
                "version",
            }
            if result is CommandResult.SUCCESS:
                expected_session_fields.add("plugin_state_json")
            else:
                expected_session_fields.update(
                    {
                        "failure_code",
                        "failure_message",
                        "failure_domain",
                    }
                )
            _assert_row_delta(
                before_callback.sessions,
                after_callback.sessions,
                changed={processed.source_item.sorting_session_id: expected_session_fields},
            )
            [callback_log] = _assert_row_delta(
                before_callback.callback_logs,
                after_callback.callback_logs,
                added_count=1,
            )
            assert callback_log["request_id"] == f"request-smt-pg-{result.value.lower()}"
            assert callback_log["ingress_outcome"] == "ACCEPTED"
            assert callback_log["response_status"] == 200
            persisted_after_callback = await db.get(
                SmtInboundHandoffSourceItem,
                seeded.source_item_id,
                populate_existing=True,
            )
            assert persisted_after_callback is not None
            if result is CommandResult.SUCCESS:
                assert persisted_after_callback.status == SmtInboundHandoffSourceItemStatus.PICKED
                ledger_intent = await db.scalar(
                    select(RuntimeIntentLog).where(
                        RuntimeIntentLog.capability_key == "material_flow.smt_source_pick_ledger"
                    )
                )
                assert ledger_intent is not None
                assert ledger_intent.effect_status == RuntimeIntentStatus.COMPLETED
                assert ledger_intent.outcome_kind == "success"
                assert ledger_intent.outcome_code == "SUCCESS"
                assert ledger_intent.outcome_json["outcome"]["payload"]["status"] == "PICKED"
                assert ledger_intent.outcome_json["outcome"]["payload"]["advanced"] is True
                assert len(after_callback.runtime_intent_logs) == len(before_callback.runtime_intent_logs) + 1
                assert len(after_callback.idempotency_keys) == len(before_callback.idempotency_keys) + 1
                assert len(after_callback.audit_logs) == len(before_callback.audit_logs) + 2
                previous_idempotency_identities = {
                    (
                        dict(row)["provider_code"],
                        dict(row)["operation_kind"],
                        dict(row)["idempotency_key"],
                    )
                    for row in before_callback.idempotency_keys
                }
                [ledger_idempotency] = [
                    dict(row)
                    for row in after_callback.idempotency_keys
                    if (
                        dict(row)["provider_code"],
                        dict(row)["operation_kind"],
                        dict(row)["idempotency_key"],
                    )
                    not in previous_idempotency_identities
                ]
                assert ledger_idempotency["execution_correlation_id"] == processed.command.correlation_id
                assert ledger_idempotency["request_hash"]
                _assert_row_delta(
                    before_callback.source_items,
                    after_callback.source_items,
                    changed={seeded.source_item_id: {"status", "updated_at"}},
                )
                _assert_row_delta(
                    before_callback.demands,
                    after_callback.demands,
                    changed={seeded.demand_id: {"status", "updated_at"}},
                )
                assert after_callback.attempt_evidence == (
                    (
                        seeded.source_item_id,
                        processed.source_item.claim_attempt_no,
                        seeded.source_inbox_id,
                        processed.command.id,
                        processed.command.command_code,
                        processed.outbox.dispatch_key,
                        SmtInboundHandoffSourceItemStatus.PICKED,
                        None,
                        None,
                    ),
                )
                expected_callback_changes = {
                    "commands",
                    "timelines",
                    "sessions",
                    "runtime_inboxes",
                    "runtime_intent_logs",
                    "idempotency_keys",
                    "source_items",
                    "demands",
                    "attempt_evidence",
                    "callback_logs",
                    "audit_logs",
                }
            else:
                assert persisted_after_callback.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(RuntimeIntentLog)
                        .where(RuntimeIntentLog.capability_key == "material_flow.smt_source_pick_ledger")
                    )
                    == 0
                )
                assert len(after_callback.runtime_intent_logs) == len(before_callback.runtime_intent_logs) + 1
                assert len(after_callback.idempotency_keys) == len(before_callback.idempotency_keys) + 1
                previous_intent_ids = {dict(row)["id"] for row in before_callback.runtime_intent_logs}
                [failure_intent] = [
                    dict(row)
                    for row in after_callback.runtime_intent_logs
                    if dict(row)["id"] not in previous_intent_ids
                ]
                assert failure_intent["correlation_id"] == processed.command.correlation_id
                assert failure_intent["target_action"] == f"inbox:{callback_inbox.id}:0:block"
                assert failure_intent["effect_status"] == RuntimeIntentStatus.COMPLETED
                assert failure_intent["outcome_kind"] == "success"
                assert failure_intent["outcome_code"] == "SUCCESS"
                previous_idempotency_identities = {
                    (
                        dict(row)["provider_code"],
                        dict(row)["operation_kind"],
                        dict(row)["idempotency_key"],
                    )
                    for row in before_callback.idempotency_keys
                }
                [failure_idempotency] = [
                    dict(row)
                    for row in after_callback.idempotency_keys
                    if (
                        dict(row)["provider_code"],
                        dict(row)["operation_kind"],
                        dict(row)["idempotency_key"],
                    )
                    not in previous_idempotency_identities
                ]
                assert failure_idempotency["execution_correlation_id"] == processed.command.correlation_id
                assert failure_idempotency["request_hash"]
                assert len(after_callback.device_runtime_projections) == (
                    len(before_callback.device_runtime_projections) + 1
                )
                assert len(after_callback.audit_logs) == len(before_callback.audit_logs) + 3
                device_before = _snapshot_row_by_id(before_callback.devices, seeded.device_id)
                device_after = _snapshot_row_by_id(after_callback.devices, seeded.device_id)
                assert _changed_row_fields(device_before, device_after) == {
                    "device_status",
                    "error_code",
                    "updated_at",
                    "version",
                }
                [device_projection] = [
                    dict(row)
                    for row in after_callback.device_runtime_projections
                    if dict(row).get("device_id") == seeded.device_id
                ]
                assert device_projection["device_code"] == seeded.device_code
                assert device_projection["runtime_status"] == "ERROR"
                assert device_projection["current_command_id"] is None
                assert device_projection["error_code"] == "DEVICE_FAILURE"
                assert device_projection["evidence_json"] == {
                    "source": "device_service_runtime_update",
                    "changed_fields": ["device_status", "error_code"],
                }
                _assert_row_delta(before_callback.source_items, after_callback.source_items)
                _assert_row_delta(before_callback.demands, after_callback.demands)
                assert after_callback.attempt_evidence == before_callback.attempt_evidence
                expected_callback_changes = {
                    "commands",
                    "timelines",
                    "sessions",
                    "runtime_inboxes",
                    "runtime_intent_logs",
                    "idempotency_keys",
                    "devices",
                    "device_runtime_projections",
                    "callback_logs",
                    "audit_logs",
                }
            assert _changed_snapshot_attributes(before_callback, after_callback) == expected_callback_changes
            [added_intent] = _assert_row_delta(
                before_callback.runtime_intent_logs,
                after_callback.runtime_intent_logs,
                added_count=1,
            )
            assert added_intent["correlation_id"] == processed.command.correlation_id
            [added_idempotency] = _assert_row_delta(
                before_callback.idempotency_keys,
                after_callback.idempotency_keys,
                added_count=1,
            )
            assert added_idempotency["execution_correlation_id"] == processed.command.correlation_id
            audit_rows = _assert_row_delta(
                before_callback.audit_logs,
                after_callback.audit_logs,
                added_count=2 if result is CommandResult.SUCCESS else 3,
            )
            expected_audit_titles = {
                f"UPDATE DeviceCommand (ID: {processed.command.id})",
                "设备回调结果",
            }
            if result is CommandResult.FAILED:
                expected_audit_titles.add(f"UPDATE Device (ID: {seeded.device_id})")
            assert {row["title"] for row in audit_rows} == expected_audit_titles

            # 重复 ingress 允许新增一条 DUPLICATE callback diagnostic，不得产生新的
            # RuntimeInbox、effect 或任何 domain/runtime state advance。
            before_duplicate = after_callback
            duplicate_inbox, duplicate_enqueue_count = await _submit_public_callback(
                db,
                processed=processed,
                payload=callback_payload,
                request_id=f"request-smt-pg-{result.value.lower()}-duplicate",
                source_event_id=f"smt-pg-callback-{result.value.lower()}",
            )
            assert duplicate_inbox.id == callback_inbox.id
            assert duplicate_enqueue_count == 0
            after_duplicate = await snapshot_smt_write_set(db)
            assert after_duplicate.state_advance() == before_duplicate.state_advance()
            [duplicate_callback_log] = _assert_row_delta(
                before_duplicate.callback_logs,
                after_duplicate.callback_logs,
                added_count=1,
            )
            assert duplicate_callback_log["ingress_outcome"] == "DUPLICATE"
            assert duplicate_callback_log["request_id"] == f"request-smt-pg-{result.value.lower()}-duplicate"
            assert after_duplicate.audit_logs == before_duplicate.audit_logs

    asyncio.run(_run_postgresql_scenario(scenario))


def test_smt_success_recovery_uses_only_fresh_database_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)

    async def scenario(session_factory: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_smt_source_pick_claim(db, suffix="recovery-success-fresh")
            processed = await process_smt_source_pick_claim(db, seeded)
            callback_inbox, enqueue_count = await _submit_public_callback(
                db,
                processed=processed,
                payload=_callback_payload(
                    processed=processed,
                    result=CommandResult.SUCCESS,
                    source_event_id="smt-pg-recovery-success-fresh",
                ),
                request_id="request-smt-pg-recovery-success-fresh",
                source_event_id="smt-pg-recovery-success-fresh",
            )
            assert enqueue_count == 1
            assert callback_inbox.status == "RECEIVED"
            await _clear_source_pick_correlation_for_recovery(
                db,
                source_item_id=seeded.source_item_id,
                command_code=processed.command.command_code,
            )

        async with session_factory() as db:
            candidates = await seeded.service.repository.list_stuck_source_items_for_recovery(
                db,
                now=timezone.now_for_db(),
                limit=100,
                stale_after_seconds=1,
            )
            assert [candidate.id for candidate in candidates] == [seeded.source_item_id]
            before_recovery = await snapshot_smt_write_set(db)
            summary = await seeded.service.scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=100,
                claim_limit=0,
                stale_after_seconds=1,
            )
            assert summary["advanced"] == 1
            assert summary["manual_hold"] == 0
            await db.commit()

        async with session_factory() as db:
            after_recovery = await snapshot_smt_write_set(db)
            assert _changed_snapshot_attributes(before_recovery, after_recovery) == {
                "source_items",
                "demands",
                "attempt_evidence",
            }
            _assert_row_delta(
                before_recovery.source_items,
                after_recovery.source_items,
                changed={
                    seeded.source_item_id: {
                        "status",
                        "source_pick_command_id",
                        "source_pick_command_code",
                        "source_pick_dispatch_key",
                        "updated_at",
                    }
                },
            )
            recovered_source = _snapshot_row_by_id(after_recovery.source_items, seeded.source_item_id)
            assert recovered_source["status"] == SmtInboundHandoffSourceItemStatus.PICKED
            assert recovered_source["source_pick_command_id"] == processed.command.id
            assert recovered_source["source_pick_command_code"] == processed.command.command_code
            assert recovered_source["source_pick_dispatch_key"] == processed.outbox.dispatch_key
            assert recovered_source["failure_code"] is None
            assert recovered_source["failure_message"] is None
            assert recovered_source["completed_at"] is None
            assert recovered_source["next_attempt_at"] is None
            _assert_row_delta(
                before_recovery.demands,
                after_recovery.demands,
                changed={seeded.demand_id: {"status", "updated_at"}},
            )
            recovered_demand = _snapshot_row_by_id(after_recovery.demands, seeded.demand_id)
            assert recovered_demand["status"] == SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS
            assert recovered_demand["failure_code"] is None
            assert recovered_demand["failure_message"] is None
            assert recovered_demand["next_attempt_at"] is None
            assert after_recovery.attempt_evidence == (
                (
                    seeded.source_item_id,
                    processed.source_item.claim_attempt_no,
                    seeded.source_inbox_id,
                    processed.command.id,
                    processed.command.command_code,
                    processed.outbox.dispatch_key,
                    SmtInboundHandoffSourceItemStatus.PICKED,
                    None,
                    None,
                ),
            )

            repeated = await seeded.service.scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=100,
                claim_limit=0,
                stale_after_seconds=1,
            )
            await db.commit()
            assert repeated["advanced"] == 0
            assert repeated["manual_hold"] == 0
            assert await snapshot_smt_write_set(db) == after_recovery

    asyncio.run(_run_postgresql_scenario(scenario))


def test_smt_generated_attempt_transaction_retry_has_one_command_and_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(session_factory: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_smt_source_pick_claim(db, suffix="transaction-retry")
            original_call = SmtSourcePickCommandHandler.__call__
            provisional: dict[str, object] = {}

            async def fail_after_generated_decision(
                self: SmtSourcePickCommandHandler,
                request: object,
                *,
                execution: object,
            ) -> object:
                _ = await original_call(self, request, execution=execution)  # type: ignore[arg-type]
                execution_db = execution.ctx["db"]  # type: ignore[attr-defined]
                await execution_db.flush()
                provisional_command = await execution_db.scalar(select(DeviceCommand))
                provisional_outbox = await execution_db.scalar(select(SystemOutbox))
                assert provisional_command is not None
                assert provisional_outbox is not None
                provisional["command_id"] = provisional_command.id
                provisional["command_code"] = provisional_command.command_code
                provisional["dispatch_key"] = provisional_outbox.dispatch_key
                provisional_item = await execution_db.get(
                    SmtInboundHandoffSourceItem,
                    seeded.source_item_id,
                    populate_existing=True,
                )
                assert provisional_item is not None
                provisional["source_pick_command_id"] = provisional_item.source_pick_command_id
                provisional["source_pick_command_code"] = provisional_item.source_pick_command_code
                provisional["source_pick_dispatch_key"] = provisional_item.source_pick_dispatch_key
                raise RuntimeError("forced-effect-transaction-retry")

            before_failure = await snapshot_smt_write_set(db)
            monkeypatch.setattr(SmtSourcePickCommandHandler, "__call__", fail_after_generated_decision)
            failed = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                db,
                claim=seeded.claim,
            )
            assert failed["failed"] == 1, failed
            assert provisional["source_pick_command_id"] == provisional["command_id"]
            assert provisional["source_pick_command_code"] == provisional["command_code"]
            assert provisional["source_pick_dispatch_key"] == provisional["dispatch_key"]

        async with session_factory() as db:
            after_failure = await snapshot_smt_write_set(db)
            assert after_failure.durable_effects(
                allowed_runtime_inbox_id=seeded.source_inbox_id
            ) == before_failure.durable_effects(allowed_runtime_inbox_id=seeded.source_inbox_id)
            _assert_row_delta(
                before_failure.runtime_inboxes,
                after_failure.runtime_inboxes,
                changed={
                    seeded.source_inbox_id: {
                        "status",
                        "processor_token",
                        "next_retry_at",
                        "lease_until",
                        "last_error_code",
                        "last_error_message",
                        "failed_at",
                    }
                },
            )
            [failure_diagnostic] = _assert_row_delta(
                before_failure.diagnostics,
                after_failure.diagnostics,
                added_count=1,
            )
            assert failure_diagnostic["inbox_id"] == seeded.source_inbox_id
            assert failure_diagnostic["diagnostic_code"] == "UNKNOWN"
            assert failure_diagnostic["session_id"] == seeded.claim["workline_session_id"]
            assert failure_diagnostic["workline_id"] == seeded.workline_id
            assert failure_diagnostic["plugin_key"] is None
            assert failure_diagnostic["status"].value == "ACTIVE"
            assert after_failure.source_items == before_failure.source_items
            assert after_failure.callback_logs == before_failure.callback_logs
            assert after_failure.audit_logs == before_failure.audit_logs

            monkeypatch.setattr(SmtSourcePickCommandHandler, "__call__", original_call)
            source_inbox = await db.get(RuntimeInbox, seeded.source_inbox_id)
            assert source_inbox is not None
            assert source_inbox.status == "FAILED"
            source_inbox.next_retry_at = 0
            db.add(source_inbox)
            await db.commit()
            [retry_claim] = await RuntimeInboxService().claim_for_processing(
                db,
                limit=1,
                processor_token="lease-smt-pg-transaction-retry-second",
                stale_after_seconds=60,
            )
            await db.commit()
            retried = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                db,
                claim=retry_claim,
            )
            assert retried["success"] == 1, retried
            after_retry = await snapshot_smt_write_set(db)
            assert len(after_retry.commands) == 1
            assert len(after_retry.outboxes) == 1
            retried_item = await db.get(SmtInboundHandoffSourceItem, seeded.source_item_id, populate_existing=True)
            assert retried_item is not None
            assert retried_item.source_pick_command_id == dict(after_retry.commands[0])["id"]
            assert retried_item.source_pick_command_code == dict(after_retry.commands[0])["command_code"]
            assert retried_item.source_pick_dispatch_key == dict(after_retry.outboxes[0])["dispatch_key"]
            assert retried_item.source_pick_command_code == provisional["command_code"]
            assert retried_item.source_pick_dispatch_key == provisional["dispatch_key"]

    asyncio.run(_run_postgresql_scenario(scenario))
