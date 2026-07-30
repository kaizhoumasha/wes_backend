"""SYSTEM_CAPABILITY EFFECT 的 PostgreSQL 原子性与幂等证据。"""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event, func, text
from sqlmodel import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
)
from src.app.runtime.orchestration.system_capability_effect_claim import (
    SystemCapabilityClaimResult,
    SystemCapabilityIdempotencyConflict,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, Success
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.definition import (
    DEFINITION as FULL_BOX_EXCHANGE_DEFINITION,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_processing_postgresql import claim, seed_scan_flow, with_temporary_runtime_database

if TYPE_CHECKING:
    from collections.abc import Mapping


async def _effect_context(db) -> dict[str, object]:  # type: ignore[no-untyped-def]
    session = await db.scalar(select(WorklineSession))
    inbox = await db.scalar(select(RuntimeInbox))
    correlation = await db.scalar(select(ExecutionCorrelation))
    assert session is not None and inbox is not None and correlation is not None
    binding = await db.get(WorklinePluginBinding, session.plugin_binding_id)
    workline = await db.get(WorkLine, session.workline_id)
    assert binding is not None and workline is not None
    execution_session = await db.scalar(select(ExecutionSession))
    if execution_session is None:
        execution_session = ExecutionSession(
            workline_id=session.workline_id,
            plugin_key=session.plugin_key,
            manifest_version=session.contract_version,
            plugin_binding_id=session.plugin_binding_id,
            plugin_binding_version=session.plugin_binding_version,
            plugin_config_hash=session.plugin_config_hash,
            plugin_index_digest=session.plugin_index_digest,
            state="RUNNING",
        )
        db.add(execution_session)
        await db.flush()
    correlation.execution_session_id = execution_session.id
    inbox.execution_session_id = execution_session.id
    work_item = await db.scalar(select(ExecutionWorkItem))
    if work_item is None:
        work_item = ExecutionWorkItem(
            execution_session_id=execution_session.id,
            correlation_id=correlation.correlation_id,
            plugin_key=session.plugin_key,
            manifest_version=session.contract_version,
            plugin_binding_id=session.plugin_binding_id,
            plugin_binding_version=session.plugin_binding_version,
            plugin_config_hash=session.plugin_config_hash,
            plugin_index_digest=session.plugin_index_digest,
            object_type="material",
            object_key="PKG-IT-001",
            current_step="SCAN",
        )
        db.add(work_item)
        await db.flush()
    await db.flush()
    return {
        "db": db,
        "session": session,
        "work_item": work_item,
        "plugin_binding": binding,
        "workline": workline,
        "inbox": inbox,
        "trace_id": inbox.trace_id,
    }


def _hold_intent(ctx: Mapping[str, object], *, operation: str = "hold-1", reason: str = "REVIEW") -> RuntimeIntent:
    session = ctx["session"]
    return RuntimeIntent.system_capability(
        capability_key="runtime.session_hold",
        contract_version="v1",
        operation_key=operation,
        dispatch_key=f"system-capability:runtime.session_hold:{operation}",
        payload={"failure_domain": "PLUGIN", "reason_code": reason, "message": "integration review"},
        precondition={"expected_status": SessionStatus.RUNNING.value},
        fact_version=f"session:{session.version}",  # type: ignore[attr-defined]
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={
            "binding_id": session.plugin_binding_id,  # type: ignore[attr-defined]
            "binding_version": session.plugin_binding_version,  # type: ignore[attr-defined]
        },
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


def _domain_full_box_exchange_intent(*, occupancy_id: str = "OCC-1") -> RuntimeIntent:
    operation_key = "wms-e11:handoff-17:box-23"
    return RuntimeIntent.system_capability(
        capability_key=FULL_BOX_EXCHANGE_DEFINITION.capability_key,
        contract_version=FULL_BOX_EXCHANGE_DEFINITION.contract_version,
        operation_key=operation_key,
        dispatch_key=operation_key,
        payload={
            "dispatch_key": operation_key,
            "exchange_request_key": operation_key,
            "station_code": "SMT-EXCHANGE",
            "rack_id": "RACK-1",
            "rack_face": "A",
            "full_box_id": "BOX-23",
            "source_slot_id": "SLOT-1",
            "occupancies": [
                {
                    "occupancy_id": occupancy_id,
                    "pkg_id": "PKG-1",
                    "material_code": "MAT-1",
                    "quantity": "1",
                }
            ],
        },
        precondition={"handoff_demand_id": 17},
        fact_version="handoff-demand:17:v1",
        timeout_seconds=FULL_BOX_EXCHANGE_DEFINITION.timeout_seconds,
        creator_authority="RUNTIME_DOMAIN_SERVICE",
        authorization_policy="DOMAIN_CAPABILITY_ALLOWLIST",
        binding_snapshot={"producer": "SMT_INBOUND_HANDOFF"},
        provider_snapshot={
            "provider_code": "RUNTIME",
            "profile": FULL_BOX_EXCHANGE_DEFINITION.admission,
        },
    )


def test_domain_effect_nullable_fk_and_postgresql_claim_replay_conflict() -> None:
    """持久 domain correlation 的 NULL session claim、MATCH 与异 hash conflict 共用唯一 ledger。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = SystemCapabilityIntentService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            workline = await db.get(WorkLine, seeded.workline_id)
            assert workline is not None
            correlation = ExecutionCorrelation(
                correlation_id="smt-inbound-handoff:17",
                execution_session_id=None,
                trace_id="trace-rack-release-11",
                source_event_id="rack-release-11",
                business_owner_key="smt-inbound-handoff-demand:17",
            )
            db.add(correlation)
            await db.flush()
            assert correlation.id is not None

            prepared = await service.prepare_and_claim(
                {
                    "db": db,
                    "execution_correlation": correlation,
                    "workline": workline,
                },
                _domain_full_box_exchange_intent(),
            )
            assert prepared.claim_result is SystemCapabilityClaimResult.NEW
            await db.commit()

        async with session_factory() as db:
            nullable = await db.scalar(
                text(
                    """
                    SELECT is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'wes_runtime'
                      AND table_name = 'runtime_intent_logs'
                      AND column_name = 'execution_session_id'
                    """
                )
            )
            foreign_key_count = await db.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.constraint_schema = kcu.constraint_schema
                    WHERE tc.table_schema = 'wes_runtime'
                      AND tc.table_name = 'runtime_intent_logs'
                      AND tc.constraint_type = 'FOREIGN KEY'
                      AND kcu.column_name = 'execution_session_id'
                    """
                )
            )
            correlation = await db.scalar(
                select(ExecutionCorrelation).where(ExecutionCorrelation.correlation_id == "smt-inbound-handoff:17")
            )
            workline = await db.scalar(select(WorkLine).where(WorkLine.id == seeded.workline_id))
            assert nullable == "YES"
            assert foreign_key_count == 1
            assert correlation is not None and correlation.execution_session_id is None
            assert correlation.business_owner_key == "smt-inbound-handoff-demand:17"
            assert workline is not None

            replay = await service.prepare_and_claim(
                {
                    "db": db,
                    "execution_correlation": correlation,
                    "workline": workline,
                },
                _domain_full_box_exchange_intent(),
            )
            assert replay.claim_result is SystemCapabilityClaimResult.MATCH
            with pytest.raises(SystemCapabilityIdempotencyConflict):
                await service.prepare_and_claim(
                    {
                        "db": db,
                        "execution_correlation": correlation,
                        "workline": workline,
                    },
                    _domain_full_box_exchange_intent(occupancy_id="OCC-DIFFERENT"),
                )

            ledgers = list((await db.execute(select(RuntimeIntentLog))).scalars())
            assert len(ledgers) == 1
            [ledger] = ledgers
            assert ledger.execution_session_id is None
            assert ledger.execution_work_item_id is None
            assert ledger.plugin_key is None
            assert ledger.plugin_contract_version is None
            assert ledger.correlation_id == correlation.correlation_id
            assert ledger.binding_snapshot_json == {"producer": "SMT_INBOUND_HANDOFF"}
            assert ledger.operation_identity == "wms-e11:handoff-17:box-23"
            assert "None" not in ledger.idempotency_key

    asyncio.run(with_temporary_runtime_database(scenario))


def test_local_effect_and_ledger_commit_atomically_without_handler_transaction_ownership() -> None:
    """handler 仅 flush；Hold 与唯一 ledger 由外层 PostgreSQL 事务一起提交。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            await seed_scan_flow(db)
            ctx = await _effect_context(db)
            transaction_events: list[str] = []
            event.listen(db.sync_session, "after_commit", lambda _session: transaction_events.append("commit"))
            event.listen(db.sync_session, "after_rollback", lambda _session: transaction_events.append("rollback"))
            result = await SystemCapabilityEffectService().apply(ctx, _hold_intent(ctx))
            assert isinstance(result.outcome, Success)
            assert result.remote_completed is True and result.durably_accepted is False
            assert transaction_events == []
            assert ctx["session"].status == SessionStatus.MANUAL_HOLD  # type: ignore[attr-defined]
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            await db.commit()
            assert transaction_events == ["commit"]

        async with session_factory() as verify_db:
            session = await verify_db.scalar(select(WorklineSession))
            ledger = await verify_db.scalar(select(RuntimeIntentLog))
            assert session is not None and session.status == SessionStatus.MANUAL_HOLD
            assert ledger is not None and ledger.effect_status is RuntimeIntentStatus.COMPLETED

    asyncio.run(with_temporary_runtime_database(scenario))


def _snapshot(
    claimed: dict[str, object],
    session: WorklineSession,
    *,
    device_fact_versions: tuple[tuple[str, int, int], ...],
) -> AttemptSnapshot:
    return AttemptSnapshot(
        processor_token=str(claimed["processor_token"]),
        session_version=session.version,
        plugin_state_version=session.plugin_state_version,
        session_status=session.status.value,
        definition_identity=DEFINITION.identity,
        binding_id=session.plugin_binding_id,
        binding_version=session.plugin_binding_version,
        plugin_config_hash=session.plugin_config_hash,
        index_digest=session.plugin_index_digest,
        device_fact_versions=device_fact_versions,
    )


def test_domain_write_then_exception_rolls_back_entire_plugin_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 write-back 在领域写入后异常时，领域、ledger、state、timeline 必须整体回滚。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            claimed = await claim(db, service, token="effect-exception-owner")
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            snapshot = _snapshot(claimed, session, device_fact_versions=seeded.device_fact_versions)
            ctx = await _effect_context(db)
            intent = _hold_intent(ctx, operation="exception-after-domain-write")

            transaction_events: list[str] = []
            event.listen(db.sync_session, "after_commit", lambda _session: transaction_events.append("commit"))
            event.listen(db.sync_session, "after_rollback", lambda _session: transaction_events.append("rollback"))

            mutation_module = import_module("src.app.runtime.orchestration.services.session_hold_mutation_service")
            real_mutation_service = mutation_module.session_hold_mutation_service
            domain_writes: list[str] = []

            class FailingAfterDomainWrite:
                async def hold(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                    await real_mutation_service.hold(*args, **kwargs)
                    domain_writes.append("written")
                    assert transaction_events == []
                    raise RuntimeError("injected after domain write")

            monkeypatch.setattr(mutation_module, "session_hold_mutation_service", FailingAfterDomainWrite())

            try:
                disposition = await RuntimeInboxWriteBackService(inbox_service=service).commit_plugin_attempt(
                    db,
                    expected_snapshot=snapshot,
                    inbox_id=seeded.inbox_id,
                    session_id=seeded.session_id,
                    workline_id=seeded.workline_id,
                    trace_id=seeded.trace_id,
                    write_set=AttemptWriteSet(
                        evidence=(),
                        next_state={"phase": "READY"},
                        intents=(intent,),
                        outcome_code="HOLD",
                    ),
                    workline=ctx["workline"],
                )
            except RuntimeError as exc:
                assert "system capability retryable failure" in str(exc)
            else:
                pytest.fail(
                    f"effect pipeline did not raise: disposition={disposition}, domain_writes={domain_writes}, "
                    f"session_version={session.version}, snapshot_version={snapshot.session_version}"
                )
            assert domain_writes == ["written"]
            assert transaction_events == ["rollback"]

        async with session_factory() as verify_db:
            session = await verify_db.get(WorklineSession, seeded.session_id)
            inbox = await verify_db.get(RuntimeInbox, seeded.inbox_id)
            assert session is not None and session.status == SessionStatus.RUNNING
            assert session.plugin_state_version == 0 and session.plugin_state_json == {}
            assert inbox is not None and inbox.status == "PROCESSING"
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(WorklineTimeline)
                    .where(WorklineTimeline.related_inbox_id == seeded.inbox_id)
                )
                == 0
            )

    asyncio.run(with_temporary_runtime_database(scenario))


def test_stale_precondition_is_business_reject_without_partial_write() -> None:
    """stale fact 以业务拒绝证据收敛而非伪成功。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            await seed_scan_flow(db)
            ctx = await _effect_context(db)
            stale = _hold_intent(ctx, operation="stale-hold")
            stale.precondition_json["expected_status"] = SessionStatus.COMPLETED.value
            result = await SystemCapabilityEffectService().apply(ctx, stale)
            assert isinstance(result.outcome, BusinessReject)
            assert result.outcome.reason_code == "STALE_PRECONDITION"
            await db.rollback()

        async with session_factory() as verify_db:
            session = await verify_db.scalar(select(WorklineSession))
            assert session is not None and session.status == SessionStatus.RUNNING
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_same_operation_hash_replays_success_and_different_hash_conflicts() -> None:
    """同 operation/payload 是零新 effect；同 operation/不同 payload 明确冲突。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = SystemCapabilityEffectService()
        async with session_factory() as db:
            await seed_scan_flow(db)
            ctx = await _effect_context(db)
            intent = _hold_intent(ctx)
            first = await service.apply(ctx, intent)
            assert isinstance(first.outcome, Success)
            await db.commit()

        async with session_factory() as db:
            ctx = await _effect_context(db)
            replay = await service.apply(ctx, intent)
            conflict = await service.apply(ctx, _hold_intent(ctx, reason="OTHER"))
            assert isinstance(replay.outcome, Success) and replay.idempotent_replay is True
            assert isinstance(conflict.outcome, ContractViolation)
            assert conflict.outcome.error_code == "IDEMPOTENCY_CONFLICT"
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            intent_log = await db.scalar(select(RuntimeIntentLog))
            case = await db.scalar(select(ReconciliationCase))
            assert intent_log is not None and intent_log.effect_status is RuntimeIntentStatus.COMPLETED
            assert case is not None and case.status is ReconciliationCaseStatus.OPEN
            assert case.dispatch_key == intent_log.dispatch_key
            assert case.evidence_history_json[-1]["event_type"] == "IDEMPOTENCY_CONFLICT"
            await db.commit()

    asyncio.run(with_temporary_runtime_database(scenario))
