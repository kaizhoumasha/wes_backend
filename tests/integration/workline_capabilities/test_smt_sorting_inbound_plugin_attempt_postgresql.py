"""SMT generated Plugin mandatory binding 的 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import fields as dataclass_fields
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.callback.services.callback_ingress_service import CallbackIngressService
from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_SANDBOX_PROFILE
from src.app.device.models.command import CommandResult, DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.handler import (
    SmtSourcePickCommandHandler,
)
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.sys.models import SystemOutbox
from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.app.workline.services.workline_service import workline_service
from src.core.conf import settings
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


async def _seed_binding(db: AsyncSession) -> tuple[WorkLine, WorklinePluginBinding, SmtSortingInboundConfig]:
    config = SmtSortingInboundConfig(provider_profile=WMS_MATERIAL_FLOW_SANDBOX_PROFILE.identity)
    workline = WorkLine(
        line_code="IT-SMT-GENERATED-BINDING",
        line_name="SMT Generated Binding",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config.model_dump(mode="json"),
        is_active=False,
    )
    db.add(workline)
    await db.flush()
    device = Device(
        device_code="IT-SMT-GENERATED-BINDING-ARM",
        device_name="SMT Generated Binding Arm",
        work_line_id=workline.id,
        device_role="SORTING_SOURCE_ARM",
        vendor_type="ECS",
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
        host="127.0.0.1",
        port=1,
    )
    db.add(device)
    await db.flush()
    activated = await workline_service.activate(
        db,
        int(workline.id),
        version=workline.version,
        actor="integration-test",
        reason="mandatory-binding",
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
    )
    assert activated is not None
    assert activated.active_plugin_binding_id is not None
    binding = await db.get(WorklinePluginBinding, activated.active_plugin_binding_id)
    assert binding is not None
    assert activated.active_plugin_binding_version == binding.binding_version
    assert activated.active_plugin_config_hash == binding.typed_config_hash
    assert activated.active_plugin_index_digest == binding.generated_index_digest
    return activated, binding, config


def test_smt_claim_binding_is_atomic_and_fresh_generated_dispatch_succeeds() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    workline, binding, config = await _seed_binding(db)

                class FailingAfterAggregateService(SmtInboundHandoffService):
                    async def _link_claim_session_material_unit(
                        self,
                        _db: AsyncSession,
                        *,
                        session: WorklineSession,
                        item: object,
                    ) -> None:
                        _ = (session, item)
                        raise RuntimeError("forced-after-runtime-aggregate")

                demand = SimpleNamespace(id=11, demand_key="smt-demand-11", trace_id="trace-11")
                item = SimpleNamespace(id=12, claim_attempt_no=1, pkg_code=None)
                async with session_factory() as db:
                    persisted_workline = await db.get(WorkLine, workline.id)
                    persisted_binding = await db.get(WorklinePluginBinding, binding.id)
                    assert persisted_workline is not None and persisted_binding is not None
                    try:
                        await FailingAfterAggregateService()._create_sorting_claim_session(
                            db,
                            workline=persisted_workline,
                            binding=persisted_binding,
                            workline_code=persisted_workline.line_code,
                            demand=demand,
                            item=item,
                            trace_id="trace-rollback",
                            route_evidence={
                                "source_rack_position_code": "SOURCE_STATION_A",
                                "target_rack_position_code": "TARGET_STATION",
                            },
                        )
                    except RuntimeError as exc:
                        assert str(exc) == "forced-after-runtime-aggregate"
                        await db.rollback()
                    else:
                        raise AssertionError("创建中途失败必须传播并触发外层事务回滚")

                async with session_factory() as db:
                    for model in (WorklineSession, ExecutionSession, ExecutionWorkItem):
                        assert await db.scalar(select(func.count()).select_from(model)) == 0

                    persisted_workline = await db.get(WorkLine, workline.id)
                    persisted_binding = await db.get(WorklinePluginBinding, binding.id)
                    assert persisted_workline is not None and persisted_binding is not None
                    claim_runtime = await SmtInboundHandoffService()._create_sorting_claim_session(
                        db,
                        workline=persisted_workline,
                        binding=persisted_binding,
                        workline_code=persisted_workline.line_code,
                        demand=demand,
                        item=item,
                        trace_id="trace-success",
                        route_evidence={
                            "source_rack_position_code": "SOURCE_STATION_A",
                            "target_rack_position_code": "TARGET_STATION",
                        },
                    )
                    session = claim_runtime.session
                    await db.commit()
                    assert session.plugin_binding_id == persisted_binding.id
                    execution_session = await db.scalar(select(ExecutionSession))
                    work_item = await db.scalar(select(ExecutionWorkItem))
                    assert execution_session is not None and work_item is not None
                    assert claim_runtime.execution_session_id == execution_session.id
                    assert claim_runtime.correlation_id == work_item.correlation_id
                    assert execution_session.plugin_binding_id == session.plugin_binding_id
                    assert work_item.plugin_binding_id == session.plugin_binding_id
                    assert work_item.manifest_version == DEFINITION.contract_version

                snapshot = PinnedPluginSnapshot(
                    plugin_key=DEFINITION.plugin_key,
                    contract_version=DEFINITION.contract_version,
                    binding_identity=f"binding:{binding.id}:{binding.binding_version}",
                    binding_id=binding.id,
                    binding_version=binding.binding_version,
                    config_hash=binding.typed_config_hash,
                    index_digest=binding.generated_index_digest,
                    profile_identity=config.provider_profile,
                )
                decision = await WorklinePluginDispatcher().dispatch(
                    request=PluginDispatchRequest(
                        plugin_key=DEFINITION.plugin_key,
                        contract_version=DEFINITION.contract_version,
                        logical_route="SOURCE_PICK_REQUESTED",
                        raw_config=config.model_dump(mode="json"),
                        raw_state={},
                        context_state={},
                        raw_input={
                            "route": "SOURCE_PICK_REQUESTED",
                            "handoff_demand_id": demand.id,
                            "handoff_source_item_id": item.id,
                            "claim_attempt_no": item.claim_attempt_no,
                            "source_pick_request_event_id": "smt-source-pick-requested-13",
                        },
                        fact_source=PluginAttemptFactSource(
                            snapshot=snapshot,
                            device_fact_versions=(("SORTING_SOURCE_ARM", 31, 0),),
                        ),
                        snapshot=snapshot,
                    ),
                    gateway=object(),
                )
                assert getattr(decision, "kind", None) != "contract_violation", decision
                assert decision.outcome_code == "SOURCE_PICK_REQUESTED"
            finally:
                await engine.dispose()

    asyncio.run(scenario())


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
    ("result", "expected_status", "expected_summary_key", "expected_generated_outcome"),
    [
        (
            CommandResult.SUCCESS,
            SmtInboundHandoffSourceItemStatus.PICKED,
            "advanced",
            "SOURCE_PICK_COMPLETED",
        ),
        (
            CommandResult.FAILED,
            SmtInboundHandoffSourceItemStatus.MANUAL_HOLD,
            "manual_hold",
            "SOURCE_PICK_FAILED",
        ),
    ],
    ids=["success", "device-failure"],
)
def test_smt_request_callback_recovery_closes_once_without_extra_effects(
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    expected_status: SmtInboundHandoffSourceItemStatus,
    expected_summary_key: str,
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
            assert len(after_duplicate.callback_logs) == len(before_duplicate.callback_logs) + 1
            assert after_duplicate.audit_logs == before_duplicate.audit_logs

            # Recovery 使用独立 aggregate：先只让正式 callback ingress 持久化权威 command terminal，
            # 再制造“command 已完成、source correlation 尚未提交”的最小 crash window。
            recovery_seeded = await seed_smt_source_pick_claim(
                db,
                suffix=f"recovery-{result.value.lower()}",
            )
            recovery_processed = await process_smt_source_pick_claim(db, recovery_seeded)
            recovery_callback_payload = _callback_payload(
                processed=recovery_processed,
                result=result,
                source_event_id=f"smt-pg-recovery-callback-{result.value.lower()}",
            )
            recovery_callback_inbox, recovery_enqueue_count = await _submit_public_callback(
                db,
                processed=recovery_processed,
                payload=recovery_callback_payload,
                request_id=f"request-smt-pg-recovery-{result.value.lower()}",
                source_event_id=f"smt-pg-recovery-callback-{result.value.lower()}",
            )
            assert recovery_enqueue_count == 1
            assert recovery_callback_inbox.status == "RECEIVED"
            assert recovery_seeded.source_item_id != seeded.source_item_id
            await _clear_source_pick_correlation_for_recovery(
                db,
                source_item_id=recovery_seeded.source_item_id,
                command_code=recovery_processed.command.command_code,
            )
            before_recovery = await snapshot_smt_write_set(db)
            summary = await recovery_seeded.service.scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=100,
                claim_limit=0,
                stale_after_seconds=1,
            )
            await db.commit()
            recovered = await db.get(SmtInboundHandoffSourceItem, recovery_seeded.source_item_id)
            assert recovered is not None
            assert summary[expected_summary_key] == 1, (
                summary,
                recovered.failure_code,
                recovered.failure_message,
            )
            assert recovered.status == expected_status
            assert recovered.source_pick_command_id == recovery_processed.command.id
            after_recovery = await snapshot_smt_write_set(db)
            recovery_changed_attributes = _changed_snapshot_attributes(before_recovery, after_recovery)
            assert recovery_changed_attributes <= {"source_items", "demands", "attempt_evidence"}
            assert "source_items" in recovery_changed_attributes
            source_before = _snapshot_row_by_id(before_recovery.source_items, recovery_seeded.source_item_id)
            source_after = _snapshot_row_by_id(after_recovery.source_items, recovery_seeded.source_item_id)
            source_changed_fields = _changed_row_fields(source_before, source_after)
            assert {
                "status",
                "source_pick_command_id",
                "source_pick_command_code",
                "source_pick_dispatch_key",
            } <= source_changed_fields
            assert source_changed_fields <= {
                "status",
                "source_pick_command_id",
                "source_pick_command_code",
                "source_pick_dispatch_key",
                "failure_code",
                "failure_message",
                "next_attempt_at",
                "completed_at",
                "updated_at",
            }
            if "demands" in recovery_changed_attributes:
                demand_before = _snapshot_row_by_id(before_recovery.demands, recovery_seeded.demand_id)
                demand_after = _snapshot_row_by_id(after_recovery.demands, recovery_seeded.demand_id)
                assert _changed_row_fields(demand_before, demand_after) <= {
                    "status",
                    "failure_code",
                    "failure_message",
                    "next_attempt_at",
                    "updated_at",
                }

            repeated = await recovery_seeded.service.scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=100,
                claim_limit=0,
                stale_after_seconds=1,
            )
            await db.commit()
            assert repeated["advanced"] == 0
            assert repeated["manual_hold"] == 0
            after_repeated_scan = await snapshot_smt_write_set(db)
            assert after_repeated_scan == after_recovery

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
                provisional["commands"] = await execution_db.scalar(select(func.count()).select_from(DeviceCommand))
                provisional["outboxes"] = await execution_db.scalar(select(func.count()).select_from(SystemOutbox))
                provisional_item = await execution_db.get(
                    SmtInboundHandoffSourceItem,
                    seeded.source_item_id,
                    populate_existing=True,
                )
                assert provisional_item is not None
                provisional["source_pick_command_id"] = provisional_item.source_pick_command_id
                raise RuntimeError("forced-effect-transaction-retry")

            before_failure = await snapshot_smt_write_set(db)
            monkeypatch.setattr(SmtSourcePickCommandHandler, "__call__", fail_after_generated_decision)
            failed = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                db,
                claim=seeded.claim,
            )
            assert failed["failed"] == 1, failed
            assert provisional["commands"] == 1
            assert provisional["outboxes"] == 1
            assert isinstance(provisional["source_pick_command_id"], int)

        async with session_factory() as db:
            after_failure = await snapshot_smt_write_set(db)
            assert after_failure.durable_effects() == before_failure.durable_effects()
            assert len(after_failure.runtime_inboxes) == len(before_failure.runtime_inboxes)
            assert len(after_failure.diagnostics) == len(before_failure.diagnostics) + 1
            failure_diagnostic = dict(after_failure.diagnostics[-1])
            assert failure_diagnostic["inbox_id"] == seeded.source_inbox_id
            assert failure_diagnostic["diagnostic_code"] == "UNKNOWN"

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
            assert retried_item.source_pick_dispatch_key == dict(after_retry.outboxes[0])["dispatch_key"]

    asyncio.run(_run_postgresql_scenario(scenario))
