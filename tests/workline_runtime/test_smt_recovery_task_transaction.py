"""SMT recovery Celery 事务边界回归。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.device.models.command import CommandResult, CommandStatus, DeviceCommand
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
    SmtInboundHandoffE11EvaluationError,
    SmtInboundHandoffE11ScanResult,
    SmtInboundHandoffService,
)
from src.celery_app.tasks.workline import _scan_smt_inbound_handoff_demands_in_transaction
from src.core.task_queue_gateway import OutboxDispatchTarget
from src.utils.timezone import timezone


class _E11ScanDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_e11_scan_commits_before_only_fulfillment_enqueue_and_keeps_outbox_when_enqueue_fails() -> None:
    db = _E11ScanDb()

    class _Service:
        async def evaluate_next_due_e11_demand(self, _db: object, **_kwargs: object) -> SmtInboundHandoffE11ScanResult:
            return SmtInboundHandoffE11ScanResult(
                scanned=True,
                advanced=True,
                demand_id=16,
                outbox_dispatch_targets=frozenset({OutboxDispatchTarget.WMS_FULFILLMENT}),
            )

        async def scan_smt_inbound_handoff_demands_batch(self, _db: object, **_kwargs: object) -> dict[str, int]:
            return {
                "scanned": 0,
                "claimed": 0,
                "advanced": 0,
                "retry_scheduled": 0,
                "manual_hold": 0,
                "recovery_errors": 0,
            }

    class _FailingGateway:
        def enqueue_outbox(self, *, targets: object, limit: int = 50) -> None:
            assert targets == frozenset({OutboxDispatchTarget.WMS_FULFILLMENT})
            assert db.commits == 1
            assert db.rollbacks == 0
            raise RuntimeError("queue unavailable")

    summary = await _scan_smt_inbound_handoff_demands_in_transaction(
        db,
        service=_Service(),
        scan_limit=1,
        recovery_limit=0,
        claim_limit=0,
        stale_after_seconds=1,
        legacy_limit=None,
        queue_gateway=_FailingGateway(),
    )

    assert summary["advanced"] == 1
    assert db.commits == 2
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_e11_scan_excludes_failed_due_demand_and_advances_next_healthy_demand() -> None:
    db = _E11ScanDb()

    class _Service:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate_next_due_e11_demand(self, _db: object, **kwargs: object) -> SmtInboundHandoffE11ScanResult:
            self.calls += 1
            if self.calls == 1:
                assert kwargs["excluded_demand_ids"] == frozenset()
                raise SmtInboundHandoffE11EvaluationError(17)
            if self.calls == 2:
                assert kwargs["excluded_demand_ids"] == frozenset({17})
                return SmtInboundHandoffE11ScanResult(scanned=True, advanced=True, demand_id=18)
            return SmtInboundHandoffE11ScanResult(scanned=False, advanced=False)

        async def scan_smt_inbound_handoff_demands_batch(self, _db: object, **_kwargs: object) -> dict[str, int]:
            return {
                "scanned": 0,
                "claimed": 0,
                "advanced": 0,
                "retry_scheduled": 0,
                "manual_hold": 0,
                "recovery_errors": 0,
            }

    service = _Service()
    summary = await _scan_smt_inbound_handoff_demands_in_transaction(
        db,
        service=service,
        scan_limit=10,
        recovery_limit=0,
        claim_limit=0,
        stale_after_seconds=1,
        legacy_limit=None,
    )

    assert service.calls == 3
    assert summary["advanced"] == 1
    assert summary["recovery_errors"] == 1
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_e11_scan_excludes_successful_unreserved_demand_before_advancing_next_healthy_demand() -> None:
    db = _E11ScanDb()

    class _Service:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate_next_due_e11_demand(self, _db: object, **kwargs: object) -> SmtInboundHandoffE11ScanResult:
            self.calls += 1
            if self.calls == 1:
                assert kwargs["excluded_demand_ids"] == frozenset()
                return SmtInboundHandoffE11ScanResult(scanned=True, advanced=False, demand_id=17)
            if self.calls == 2:
                assert kwargs["excluded_demand_ids"] == frozenset({17})
                return SmtInboundHandoffE11ScanResult(scanned=True, advanced=True, demand_id=18)
            assert kwargs["excluded_demand_ids"] == frozenset({17, 18})
            return SmtInboundHandoffE11ScanResult(scanned=False, advanced=False)

        async def scan_smt_inbound_handoff_demands_batch(self, _db: object, **_kwargs: object) -> dict[str, int]:
            return {
                "scanned": 0,
                "claimed": 0,
                "advanced": 0,
                "retry_scheduled": 0,
                "manual_hold": 0,
                "recovery_errors": 0,
            }

    service = _Service()
    summary = await _scan_smt_inbound_handoff_demands_in_transaction(
        db,
        service=service,
        scan_limit=10,
        recovery_limit=0,
        claim_limit=0,
        stale_after_seconds=1,
        legacy_limit=None,
    )

    assert service.calls == 3
    assert summary["scanned"] == 2
    assert summary["advanced"] == 1
    assert db.commits == 3


@pytest.mark.parametrize("execution_anchor_case", ["owned", "split_execution", "complete_replacement"])
@pytest.mark.asyncio
async def test_recovery_task_commit_persists_correlation_and_picked_after_session_exit(
    db_engine: object,
    execution_anchor_case: str,
) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    correlation_id = "workline-session:SMT-TASK-21"
    command_code = "SC-SOURCE-PICK-TASK-31"
    request_event_id = "source-pick-task-event-31"

    async with session_factory() as seed_db:
        demand = SmtInboundHandoffDemand(
            demand_key="task-recovery-demand",
            rack_release_id="task-recovery-release",
            single_layer_rack_code="RACK-TASK",
            source_workline_id=1,
            source_workline_code="SOURCE",
            target_workline_id=7,
            target_workline_code="SORTING",
            status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        )
        session = WorklineSession(
            session_code="SMT-TASK-21",
            workline_id=7,
            plugin_key="smt_sorting",
            run_mode=RunMode.AUTO,
            status=SessionStatus.WAITING_DEVICE_RESULT,
            context_json={},
            contract_version="1.0",
            plugin_binding_id=1,
            plugin_binding_version=1,
            plugin_config_hash="a" * 64,
            plugin_index_digest="b" * 64,
            plugin_state_json={},
            trace_id="trace-task-recovery",
            business_key="business-task-recovery",
            current_wait_type="DEVICE_CALLBACK",
            awaiting_device_command_code=command_code,
        )
        seed_db.add_all([demand, session])
        await seed_db.flush()

        inbox_execution_session = ExecutionSession(
            workline_id=7,
            plugin_key=session.plugin_key,
            manifest_version=session.contract_version,
            plugin_binding_id=session.plugin_binding_id,
            plugin_binding_version=session.plugin_binding_version,
            plugin_config_hash=session.plugin_config_hash,
            plugin_index_digest=session.plugin_index_digest,
            state="RUNNING",
        )
        seed_db.add(inbox_execution_session)
        await seed_db.flush()
        owned_correlation = ExecutionCorrelation(
            correlation_id=correlation_id,
            execution_session_id=inbox_execution_session.id,
            trace_id=session.trace_id,
            source_event_id=request_event_id,
            business_owner_key=session.business_key,
        )
        owned_work_item = ExecutionWorkItem(
            execution_session_id=inbox_execution_session.id,
            correlation_id=correlation_id,
            plugin_key=session.plugin_key,
            manifest_version=session.contract_version,
            plugin_binding_id=session.plugin_binding_id,
            plugin_binding_version=session.plugin_binding_version,
            plugin_config_hash=session.plugin_config_hash,
            plugin_index_digest=session.plugin_index_digest,
            object_type="bin",
            object_key="task-source-item",
            current_step="SORTING_SOURCE_PICK",
        )
        seed_db.add_all([owned_correlation, owned_work_item])
        await seed_db.flush()

        active_execution_session = inbox_execution_session
        active_correlation_id = correlation_id
        active_trace_id = session.trace_id
        if execution_anchor_case != "owned":
            replacement_execution_session = ExecutionSession(
                workline_id=7,
                plugin_key=session.plugin_key,
                manifest_version=session.contract_version,
                plugin_binding_id=session.plugin_binding_id,
                plugin_binding_version=session.plugin_binding_version,
                plugin_config_hash=session.plugin_config_hash,
                plugin_index_digest=session.plugin_index_digest,
                state="RUNNING",
            )
            seed_db.add(replacement_execution_session)
            await seed_db.flush()
            active_correlation_id = "workline-session:SMT-TASK-OTHER"
            active_trace_id = "trace-other"
            replacement_correlation = ExecutionCorrelation(
                correlation_id=active_correlation_id,
                execution_session_id=replacement_execution_session.id,
                trace_id="trace-other",
                source_event_id="source-pick-task-event-other",
                business_owner_key="business-other",
            )
            replacement_work_item = ExecutionWorkItem(
                execution_session_id=replacement_execution_session.id,
                correlation_id=active_correlation_id,
                plugin_key=session.plugin_key,
                manifest_version=session.contract_version,
                plugin_binding_id=session.plugin_binding_id,
                plugin_binding_version=session.plugin_binding_version,
                plugin_config_hash=session.plugin_config_hash,
                plugin_index_digest=session.plugin_index_digest,
                object_type="bin",
                object_key="task-source-item-other",
                current_step="SORTING_SOURCE_PICK",
            )
            seed_db.add_all([replacement_correlation, replacement_work_item])
            await seed_db.flush()
            if execution_anchor_case == "complete_replacement":
                active_execution_session = replacement_execution_session

        inbox = RuntimeInbox(
            workline_session_id=session.id,
            execution_session_id=active_execution_session.id,
            correlation_id=active_correlation_id,
            trace_id=active_trace_id,
            kind="INTERNAL_EVENT",
            workline_id=7,
            event_id=request_event_id,
            provider_code="WES_INTERNAL",
            event_type="SORTING_SOURCE_PICK_REQUESTED",
            source_event_id=request_event_id,
            payload_hash="hash-task-31",
            payload_json={"event_id": request_event_id},
            payload_schema_version=1,
            status="PROCESSED",
            claim_bucket_key=active_correlation_id,
            received_at=1,
            processed_at=2,
        )
        seed_db.add(inbox)
        await seed_db.flush()

        source_item = SmtInboundHandoffSourceItem(
            handoff_demand_id=demand.id,
            item_key="task-source-item",
            status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
            target_workline_id=7,
            target_workline_code="SORTING",
            sorting_session_id=session.id,
            claim_attempt_no=3,
            source_pick_inbox_id=inbox.id,
            updated_at=timezone.now_for_db() - timedelta(minutes=10),
        )
        seed_db.add(source_item)
        await seed_db.flush()
        session.context_json = {
            "sorting": {
                "context_schema_version": 1,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": source_item.id,
                    "claim_attempt_no": 3,
                    "event_id": request_event_id,
                },
            }
        }
        command = DeviceCommand(
            device_id=1,
            task_type="SORTING_SOURCE_PICK",
            command_code=command_code,
            correlation_id=active_correlation_id,
            trace_id=active_trace_id,
            workline_id=7,
            plugin_key=session.plugin_key,
            contract_version=session.contract_version,
            params={
                "handoff_demand_id": demand.id,
                "handoff_source_item_id": source_item.id,
                "claim_attempt_no": 3,
                "source_pick_inbox_id": inbox.id,
                "source_pick_request_event_id": request_event_id,
            },
            status=CommandStatus.COMPLETED,
            result=CommandResult.SUCCESS,
        )
        seed_db.add_all([session, command])
        await seed_db.commit()
        source_item_id = int(source_item.id)
        command_id = int(command.id)

    async with session_factory() as scan_db:
        summary = await _scan_smt_inbound_handoff_demands_in_transaction(
            scan_db,
            service=SmtInboundHandoffService(),
            scan_limit=0,
            recovery_limit=10,
            claim_limit=0,
            stale_after_seconds=1,
            legacy_limit=None,
        )

    execution_anchor_mismatch = execution_anchor_case != "owned"
    assert summary["advanced"] == (0 if execution_anchor_mismatch else 1)
    assert summary["manual_hold"] == (1 if execution_anchor_mismatch else 0)
    async with session_factory() as verify_db:
        persisted = await verify_db.get(SmtInboundHandoffSourceItem, source_item_id)

    assert persisted is not None
    if execution_anchor_mismatch:
        assert persisted.source_pick_command_id is None
        assert persisted.source_pick_command_code is None
        assert persisted.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    else:
        assert persisted.source_pick_command_id == command_id
        assert persisted.source_pick_command_code == command_code
        assert persisted.status == SmtInboundHandoffSourceItemStatus.PICKED
