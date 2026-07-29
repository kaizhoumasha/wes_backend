"""SMT source-pick generated runtime 生命周期集成回归。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.device.models.command import CommandCallbackResult, CommandResult, CommandStatus, DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_service import device_service
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.contracts import (
    SmtSourcePickLedgerAdmission,
    SmtSourcePickLedgerInput,
    SmtSourcePickLedgerPrecondition,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.handler import (
    SmtSourcePickLedgerHandler,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.sys.models.outbox import SystemOutbox
from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.core.conf import settings
from src.utils.timezone import timezone


class _NoopQueueGateway:
    """事务后即时唤醒不属于本集成用例的持久化断言范围。"""

    def enqueue_runtime_inbox(self, *, limit: int) -> None:
        _ = limit

    def enqueue_outbox(self, *, targets: object, limit: int) -> None:
        _ = targets, limit


class _SelectedRouteService:
    def __init__(self, *, workline_id: int, workline_code: str) -> None:
        self.workline_id = workline_id
        self.workline_code = workline_code

    async def resolve_route(self, _db: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            kind="SELECTED",
            selected_workline_id=self.workline_id,
            selected_workline_code=self.workline_code,
            route_evidence={
                "source_rack_position_code": "SOURCE_STATION_A",
                "target_rack_position_code": "TARGET_STATION",
                "manifest_contract_version": DEFINITION.contract_version,
            },
        )


def _provider_profile() -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code="RUNTIME",
        contract_version="v1",
        environment="sandbox",
        timeout_retry_query_timeout_seconds=1,
        timeout_retry_retry_backoff_seconds=[1],
        fixture_set_path="tests/fixtures/external_contracts/runtime/default",
        fixture_set_required_cases=["success"],
    )


async def _seed_source_pick(
    db_session: object,
    *,
    trace_id: str | None = "trace-generated-source-pick",
) -> tuple[WorkLine, Device, SmtInboundHandoffDemand, SmtInboundHandoffSourceItem]:
    profile = _provider_profile()
    config = SmtSortingInboundConfig(provider_profile=profile.identity)
    config_json = config.model_dump(mode="json")
    workline = WorkLine(
        line_code="SMT-GENERATED-LIFECYCLE",
        line_name="SMT Generated Lifecycle",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config_json,
        is_active=True,
    )
    db_session.add(workline)
    await db_session.flush()

    binding = WorklinePluginBinding(
        workline_id=workline.id,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        binding_version=1,
        typed_config_json=config_json,
        typed_config_hash=sha256_digest(config_json),
        provider_profile_snapshot_json=[profile.model_dump(mode="json")],
        device_snapshot_json=[],
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        activated_at=timezone.now_for_db(),
        activated_by="pytest",
        activated_reason="generated source-pick lifecycle",
    )
    db_session.add(binding)
    await db_session.flush()
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = binding.typed_config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest

    device = Device(
        device_code="SMT-SOURCE-ARM-1",
        device_name="SMT Source Arm",
        work_line_id=workline.id,
        device_role="SORTING_SOURCE_ARM",
        vendor_type="ECS",
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
    )
    projection = WorklineRuntimeStatusProjection(
        workline_id=workline.id,
        runtime_status=WorkLineRuntimeStatus.READY.value,
    )
    demand = SmtInboundHandoffDemand(
        demand_key="generated-source-pick-demand",
        rack_release_id="generated-source-pick-release",
        single_layer_rack_code="RACK-GENERATED-1",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=trace_id,
    )
    db_session.add_all([workline, device, projection, demand])
    await db_session.flush()
    source_item = SmtInboundHandoffSourceItem(
        handoff_demand_id=demand.id,
        item_key="generated-source-pick-item",
        bin_code="BIN-1",
        bin_cell_index=1,
        bin_cell_code="CELL-1",
        material_identity_key="MAT-1",
        status=SmtInboundHandoffSourceItemStatus.READY,
    )
    db_session.add(source_item)
    await db_session.commit()
    return workline, device, demand, source_item


async def _claim_and_process_source_pick(
    db_session: object,
    *,
    trace_id: str | None = "trace-generated-source-pick",
) -> tuple[SmtInboundHandoffService, SmtInboundHandoffSourceItem, RuntimeInbox, DeviceCommand, SystemOutbox]:
    workline, _device, demand, source_item = await _seed_source_pick(db_session, trace_id=trace_id)
    service = SmtInboundHandoffService(
        route_service=_SelectedRouteService(workline_id=workline.id, workline_code=workline.line_code)
    )
    claim = await service.claim_next_source_item(
        db_session,
        demand_id=demand.id,
        trace_id=demand.trace_id,
    )
    assert claim.kind == "CLAIMED"
    await db_session.commit()

    source_inbox = await db_session.get(RuntimeInbox, claim.inbox.id)
    assert source_inbox is not None
    claimed_inboxes = await RuntimeInboxService().claim_for_processing(
        db_session,
        limit=1,
        processor_token="lease-smt-source-pick",
        stale_after_seconds=60,
    )
    await db_session.commit()
    assert [row["id"] for row in claimed_inboxes] == [source_inbox.id]

    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
        db_session,
        claim=claimed_inboxes[0],
    )
    assert processed["success"] == 1

    command = await db_session.scalar(select(DeviceCommand).where(DeviceCommand.task_type == "SORTING_SOURCE_PICK"))
    outbox = await db_session.scalar(select(SystemOutbox))
    await db_session.refresh(source_item)
    assert command is not None
    assert outbox is not None
    return service, source_item, source_inbox, command, outbox


@pytest.mark.asyncio
async def test_smt_claim_without_trace_persists_one_canonical_trace_through_callback(
    db_session: object,
) -> None:
    _service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(
        db_session,
        trace_id=None,
    )
    session = await db_session.get(WorklineSession, source_item.sorting_session_id)
    correlation = await db_session.scalar(
        select(ExecutionCorrelation).where(ExecutionCorrelation.correlation_id == source_inbox.correlation_id)
    )
    assert session is not None
    assert correlation is not None
    assert session.trace_id == source_inbox.event_id
    assert correlation.trace_id == source_inbox.event_id
    assert source_inbox.trace_id == source_inbox.event_id
    assert command.trace_id == source_inbox.event_id

    callback_command_service = DeviceCommandService()
    callback_command_service.enable_cache = False
    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="SMT-SOURCE-ARM-1",
        result=CommandResult.SUCCESS,
        finish_time=int(timezone.now_utc().timestamp() * 1000),
        source_event_id="smt-source-pick-no-trace-callback",
        trace_id=None,
    )
    outcome = await CallbackOrchestrationService(queue_gateway=_NoopQueueGateway()).process_result(
        db_session,
        callback=callback,
        existing_command=command,
        request_id="request-smt-source-pick-no-trace-callback",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=callback_command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    assert outcome.is_duplicate is False
    callback_inbox = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.kind == "COMMAND_RESULT"))
    assert callback_inbox is not None
    assert callback_inbox.trace_id == source_inbox.event_id
    [claim] = await RuntimeInboxService().claim_for_processing(
        db_session,
        limit=1,
        processor_token="lease-smt-source-pick-no-trace-callback",
        stale_after_seconds=60,
    )
    await db_session.commit()
    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
        db_session,
        claim=claim,
    )
    assert processed["success"] == 1
    await db_session.refresh(source_item)
    assert source_item.status == SmtInboundHandoffSourceItemStatus.PICKED


@pytest.mark.asyncio
async def test_smt_claim_without_trace_recovery_advances_completed_command_to_picked(
    db_session: object,
) -> None:
    service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(
        db_session,
        trace_id=None,
    )
    command.status = CommandStatus.COMPLETED
    command.result = CommandResult.SUCCESS
    db_session.add_all([command, source_item])
    await db_session.commit()
    await db_session.execute(
        update(SmtInboundHandoffSourceItem)
        .where(SmtInboundHandoffSourceItem.id == source_item.id)
        .values(updated_at=timezone.now_for_db() - timedelta(minutes=10))
    )
    await db_session.commit()
    await db_session.refresh(command)
    await db_session.refresh(source_item)
    await db_session.refresh(source_inbox)
    assert command.status == CommandStatus.COMPLETED
    assert command.result == CommandResult.SUCCESS
    assert source_item.source_pick_command_id == command.id
    assert source_inbox.status == "PROCESSED"

    summary = await service.scan_smt_inbound_handoff_demands_batch(
        db_session,
        scan_limit=0,
        recovery_limit=10,
        claim_limit=0,
        stale_after_seconds=0,
    )

    await db_session.refresh(source_item)
    assert summary["scanned"] == 1, summary
    assert summary["advanced"] == 1, summary
    assert summary["manual_hold"] == 0
    assert source_item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert command.trace_id == source_inbox.event_id


@pytest.mark.asyncio
async def test_smt_claim_processor_persists_execution_anchor_command_and_outbox(db_session: object) -> None:
    _service, source_item, source_inbox, command, outbox = await _claim_and_process_source_pick(db_session)

    assert source_inbox.execution_session_id is not None
    assert source_inbox.correlation_id == command.correlation_id
    assert source_item.status == SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING
    assert source_item.source_pick_command_id == command.id
    assert source_item.source_pick_command_code == command.command_code
    assert source_item.source_pick_dispatch_key == outbox.dispatch_key
    assert command.params["source_pick_inbox_id"] == source_inbox.id


@pytest.mark.asyncio
async def test_smt_command_correlation_rejects_cross_session_overwrite(db_session: object) -> None:
    service, source_item, source_inbox, command, outbox = await _claim_and_process_source_pick(db_session)

    with pytest.raises(ValueError, match="session"):
        await service.record_source_pick_command_correlation(
            db_session,
            handoff_demand_id=source_item.handoff_demand_id,
            source_item_id=source_item.id,
            claim_attempt_no=source_item.claim_attempt_no,
            source_pick_inbox_id=source_inbox.id,
            command_id=command.id,
            command_code=command.command_code,
            dispatch_key=outbox.dispatch_key,
            session_id=source_item.sorting_session_id + 1,
            workline_id=source_inbox.workline_id,
            execution_session_id=source_inbox.execution_session_id,
            correlation_id=source_inbox.correlation_id,
            plugin_key=command.plugin_key,
            contract_version=command.contract_version,
            trace_id=command.trace_id,
        )


@pytest.mark.asyncio
async def test_smt_fast_success_callback_marks_picked_before_first_recovery_scan(db_session: object) -> None:
    service, source_item, _source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    callback_command_service = DeviceCommandService()
    callback_command_service.enable_cache = False
    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="SMT-SOURCE-ARM-1",
        result=CommandResult.SUCCESS,
        finish_time=int(timezone.now_utc().timestamp() * 1000),
        source_event_id="smt-source-pick-result-1",
        trace_id=command.trace_id,
    )
    outcome = await CallbackOrchestrationService(queue_gateway=_NoopQueueGateway()).process_result(
        db_session,
        callback=callback,
        existing_command=command,
        request_id="request-smt-source-pick-result-1",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=callback_command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    assert outcome.is_duplicate is False

    callback_inbox = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.kind == "COMMAND_RESULT"))
    assert callback_inbox is not None
    claimed_inboxes = await RuntimeInboxService().claim_for_processing(
        db_session,
        limit=1,
        processor_token="lease-smt-source-pick-result",
        stale_after_seconds=60,
    )
    await db_session.commit()
    assert [row["id"] for row in claimed_inboxes] == [callback_inbox.id]

    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
        db_session,
        claim=claimed_inboxes[0],
    )
    assert processed["success"] == 1
    await db_session.refresh(source_item)
    assert source_item.status == SmtInboundHandoffSourceItemStatus.PICKED

    session = await db_session.get(WorklineSession, source_item.sorting_session_id)
    assert session is not None
    duplicate_outcome = await SmtSourcePickLedgerHandler()(
        SmtSourcePickLedgerInput(operation="RECORD_PICKED", command_code=command.command_code),
        execution=SimpleNamespace(
            ctx={
                "db": db_session,
                "session": session,
                "inbox": callback_inbox,
                "trace_id": command.trace_id,
            },
            admission=SmtSourcePickLedgerAdmission(
                precondition=SmtSourcePickLedgerPrecondition(expected_status="CLAIMED_BY_SORTING"),
                fact_version=f"command:{sha256_digest(command.command_code)[:32]}:SUCCESS",
            ),
        ),
    )
    assert duplicate_outcome.kind == "success"
    assert duplicate_outcome.payload.advanced is False
    await db_session.refresh(source_item)
    assert source_item.status == SmtInboundHandoffSourceItemStatus.PICKED

    summary = await service.scan_smt_inbound_handoff_demands_batch(
        db_session,
        scan_limit=0,
        recovery_limit=10,
        claim_limit=0,
        stale_after_seconds=1,
    )
    await db_session.refresh(source_item)
    assert summary["manual_hold"] == 0
    assert source_item.status == SmtInboundHandoffSourceItemStatus.PICKED


@pytest.mark.asyncio
async def test_smt_anchor_reject_persists_manual_hold_after_callback_transaction_reopens(
    db_session: object,
) -> None:
    """业务拒绝 rollback 后必须在重新加锁的事务中持久化 fail-closed Hold。"""

    _service, source_item, source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    source_item_id = int(source_item.id)
    sorting_session_id = int(source_item.sorting_session_id)
    command_code = command.command_code
    source_inbox.execution_session_id = int(source_inbox.execution_session_id) + 10_000
    db_session.add(source_inbox)
    await db_session.commit()

    callback_command_service = DeviceCommandService()
    callback_command_service.enable_cache = False
    callback = CommandCallbackResult(
        command_code=command_code,
        device_code="SMT-SOURCE-ARM-1",
        result=CommandResult.SUCCESS,
        finish_time=int(timezone.now_utc().timestamp() * 1000),
        source_event_id="smt-source-pick-result-anchor-reject",
        trace_id=command.trace_id,
    )
    outcome = await CallbackOrchestrationService(queue_gateway=_NoopQueueGateway()).process_result(
        db_session,
        callback=callback,
        existing_command=command,
        request_id="request-smt-source-pick-result-anchor-reject",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=callback_command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    assert outcome.is_duplicate is False

    callback_inbox = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.kind == "COMMAND_RESULT"))
    assert callback_inbox is not None
    [claim] = await RuntimeInboxService().claim_for_processing(
        db_session,
        limit=1,
        processor_token="lease-smt-source-pick-anchor-reject",
        stale_after_seconds=60,
    )
    await db_session.commit()
    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
        db_session,
        claim=claim,
    )
    assert processed["success"] == 1

    session_factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as verify_db:
        persisted_item = await verify_db.get(SmtInboundHandoffSourceItem, source_item_id)
        persisted_session = await verify_db.get(WorklineSession, sorting_session_id)
        feedback = await verify_db.scalar(
            select(RuntimeInbox).where(RuntimeInbox.event_type == "CAPABILITY_EFFECT_RESULT")
        )

    assert persisted_item is not None
    assert persisted_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert persisted_session is not None
    assert persisted_session.status == SessionStatus.WAITING_DEVICE_RESULT
    assert persisted_session.awaiting_device_command_code == command_code
    assert feedback is not None


@pytest.mark.asyncio
async def test_smt_late_success_after_manual_hold_does_not_continue_session(db_session: object) -> None:
    _service, source_item, _source_inbox, command, _outbox = await _claim_and_process_source_pick(db_session)
    command_code = command.command_code
    source_item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    source_item.failure_code = "SOURCE_PICK_RECOVERY_AMBIGUOUS"
    db_session.add(source_item)
    await db_session.commit()

    callback_command_service = DeviceCommandService()
    callback_command_service.enable_cache = False
    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="SMT-SOURCE-ARM-1",
        result=CommandResult.SUCCESS,
        finish_time=int(timezone.now_utc().timestamp() * 1000),
        source_event_id="smt-source-pick-result-late",
        trace_id=command.trace_id,
    )
    outcome = await CallbackOrchestrationService(queue_gateway=_NoopQueueGateway()).process_result(
        db_session,
        callback=callback,
        existing_command=command,
        request_id="request-smt-source-pick-result-late",
        resolved_contract_version=DEFINITION.contract_version,
        command_service=callback_command_service,
        device_service=device_service,
        enqueue_processing=lambda: None,
    )
    assert outcome.is_duplicate is False

    callback_inbox = await db_session.scalar(select(RuntimeInbox).where(RuntimeInbox.kind == "COMMAND_RESULT"))
    assert callback_inbox is not None
    [claim] = await RuntimeInboxService().claim_for_processing(
        db_session,
        limit=1,
        processor_token="lease-smt-source-pick-result-late",
        stale_after_seconds=60,
    )
    await db_session.commit()
    processed = await RuntimeInboxProcessorBridge(queue_gateway=_NoopQueueGateway()).process_claimed(
        db_session,
        claim=claim,
    )
    assert processed["success"] == 1

    await db_session.refresh(source_item)
    session = await db_session.get(WorklineSession, source_item.sorting_session_id)
    feedback = await db_session.scalar(
        select(RuntimeInbox).where(RuntimeInbox.event_type == "CAPABILITY_EFFECT_RESULT")
    )
    assert source_item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert session is not None
    assert session.status == SessionStatus.WAITING_DEVICE_RESULT
    assert session.awaiting_device_command_code == command_code
    assert feedback is not None
