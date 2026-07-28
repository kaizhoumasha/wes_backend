"""SMT generated Plugin mandatory binding 的 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.device.models.command import CommandCallbackResult, CommandResult, DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_service import device_service
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
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
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.sys.models import SystemOutbox
from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.core.conf import settings
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.smt_sorting_inbound_postgresql import (
    NoopQueueGateway,
    process_smt_source_pick_claim,
    seed_smt_source_pick_claim,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def _seed_binding(db: AsyncSession) -> tuple[WorkLine, WorklinePluginBinding, SmtSortingInboundConfig]:
    config = SmtSortingInboundConfig(provider_profile="runtime")
    workline = WorkLine(
        line_code="IT-SMT-GENERATED-BINDING",
        line_name="SMT Generated Binding",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config.model_dump(mode="json"),
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    binding = WorklinePluginBinding(
        workline_id=workline.id,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        binding_version=1,
        typed_config_json=config.model_dump(mode="json"),
        typed_config_hash=sha256_digest(config.model_dump(mode="json")),
        provider_profile_snapshot_json=[],
        device_snapshot_json=[],
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        activated_at=timezone.now_for_db(),
        activated_by="integration-test",
        activated_reason="mandatory-binding",
    )
    db.add(binding)
    await db.flush()
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = binding.typed_config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest
    await db.commit()
    return workline, binding, config


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
                    profile_identity="runtime",
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


async def _persistent_effect_counts(db: AsyncSession) -> tuple[int, int, int]:
    command_count = int(await db.scalar(select(func.count()).select_from(DeviceCommand)) or 0)
    outbox_count = int(await db.scalar(select(func.count()).select_from(SystemOutbox)) or 0)
    timeline_count = int(await db.scalar(select(func.count()).select_from(WorklineTimeline)) or 0)
    return command_count, outbox_count, timeline_count


async def _record_callback(
    db: AsyncSession,
    *,
    processed: Any,
    result: CommandResult,
    source_event_id: str,
) -> Any:
    command_service = DeviceCommandService()
    command_service.enable_cache = False
    callback = CommandCallbackResult(
        command_code=processed.command.command_code,
        device_code=processed.seeded.device_code,
        result=result,
        finish_time=int(timezone.now_utc().timestamp() * 1_000),
        source_event_id=source_event_id,
        trace_id=processed.command.trace_id,
        error_detail={"code": "DEVICE_FAILURE"} if result is CommandResult.FAILED else None,
    )
    service = CallbackOrchestrationService(queue_gateway=NoopQueueGateway())
    first = await service.process_result(
        db,
        callback=callback,
        existing_command=processed.command,
        request_id=f"request-{source_event_id}",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    duplicate = await service.process_result(
        db,
        callback=callback,
        existing_command=processed.command,
        request_id=f"request-{source_event_id}-duplicate",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    assert first.is_duplicate is False
    assert duplicate.is_duplicate is True
    return first


async def _clear_source_pick_correlation_for_recovery(
    db: AsyncSession,
    *,
    source_item_id: int,
) -> None:
    await db.execute(
        update(SmtInboundHandoffSourceItem)
        .where(SmtInboundHandoffSourceItem.id == source_item_id)
        .values(
            source_pick_command_id=None,
            source_pick_command_code=None,
            source_pick_dispatch_key=None,
            updated_at=timezone.now_for_db() - timedelta(minutes=10),
        )
    )
    await db.commit()


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_summary_key"),
    [
        (CommandResult.SUCCESS, SmtInboundHandoffSourceItemStatus.PICKED, "advanced"),
        (CommandResult.FAILED, SmtInboundHandoffSourceItemStatus.MANUAL_HOLD, "manual_hold"),
    ],
    ids=["success", "device-failure"],
)
def test_smt_request_callback_recovery_closes_once_without_extra_effects(
    result: CommandResult,
    expected_status: SmtInboundHandoffSourceItemStatus,
    expected_summary_key: str,
) -> None:
    async def scenario(session_factory: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_smt_source_pick_claim(db, suffix=f"closure-{result.value.lower()}")
            processed = await process_smt_source_pick_claim(db, seeded)
            assert processed.source_item.source_pick_command_id == processed.command.id
            assert processed.source_inbox.status == "PROCESSED"
            assert processed.outbox.dispatch_key == f"device-command:{processed.command.command_code}"

            await _record_callback(
                db,
                processed=processed,
                result=result,
                source_event_id=f"smt-pg-callback-{result.value.lower()}",
            )
            callback_inboxes = list(
                (
                    await db.scalars(
                        select(RuntimeInbox).where(
                            RuntimeInbox.kind == "COMMAND_RESULT",
                            RuntimeInbox.command_id == processed.command.id,
                        )
                    )
                ).all()
            )
            assert len(callback_inboxes) == 1
            callback_inbox = callback_inboxes[0]
            assert callback_inbox.command_id == processed.command.id
            assert callback_inbox.correlation_id == processed.command.correlation_id
            assert callback_inbox.execution_session_id == processed.source_inbox.execution_session_id

            await _clear_source_pick_correlation_for_recovery(
                db,
                source_item_id=seeded.source_item_id,
            )
            counts_before_recovery = await _persistent_effect_counts(db)
            summary = await seeded.service.scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=100,
                claim_limit=0,
                stale_after_seconds=1,
            )
            await db.commit()
            recovered = await db.get(SmtInboundHandoffSourceItem, seeded.source_item_id)
            assert recovered is not None
            assert summary[expected_summary_key] == 1, summary
            assert recovered.status == expected_status
            assert recovered.source_pick_command_id == processed.command.id
            assert await _persistent_effect_counts(db) == counts_before_recovery

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
            assert await _persistent_effect_counts(db) == counts_before_recovery

    asyncio.run(_run_postgresql_scenario(scenario))


def test_smt_generated_attempt_transaction_retry_has_one_command_and_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(session_factory: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_smt_source_pick_claim(db, suffix="transaction-retry")
            original_call = SmtSourcePickCommandHandler.__call__

            async def fail_after_generated_decision(
                _self: SmtSourcePickCommandHandler,
                _request: object,
                *,
                execution: object,
            ) -> object:
                _ = execution
                raise RuntimeError("forced-effect-transaction-retry")

            monkeypatch.setattr(SmtSourcePickCommandHandler, "__call__", fail_after_generated_decision)
            failed = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                db,
                claim=seeded.claim,
            )
            assert failed["failed"] == 1, failed
            assert await _persistent_effect_counts(db) == (0, 0, 0)

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
            command_count, outbox_count, _timeline_count = await _persistent_effect_counts(db)
            assert command_count == 1
            assert outbox_count == 1

    asyncio.run(_run_postgresql_scenario(scenario))
