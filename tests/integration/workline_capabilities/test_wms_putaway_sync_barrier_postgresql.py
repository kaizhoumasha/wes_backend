"""E03/E07 双义务屏障的真实 PostgreSQL 行锁与并发合同。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.effect_bridges import (
    EffectReconciliationBridge,
    EffectTransportBridge,
    EffectTransportResolution,
)
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldStatus
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service import (
    E03_CONFIRM_INBOUND,
    E07_NOTIFY_PKG_BINDING,
    WmsPutawaySyncBarrierGroup,
    WmsPutawaySyncBarrierService,
)
from src.app.runtime.orchestration.wms_sync_obligation import WmsSyncObligationResolution
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.utils.timezone import timezone
from tests.support.runtime_binding import seed_runtime_binding

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ObligationSeed = Literal["PROPOSED", "COMPLETED", "RECONCILED", "OPEN_CASE"]


async def _seed_group(
    db: AsyncSession,
    *,
    suffix: str,
    sources: tuple[ObligationSeed, ObligationSeed],
) -> WmsPutawaySyncBarrierGroup:
    workline, binding = await seed_runtime_binding(db, line_code=f"WMS-SYNC-BARRIER-{suffix}")
    execution_session = ExecutionSession(
        workline_id=workline.id,
        plugin_key=binding.plugin_key,
        manifest_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        state="RUNNING",
    )
    db.add(execution_session)
    await db.flush()
    correlation_id = f"wms-sync-barrier:{suffix}"
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=execution_session.id,
        trace_id=f"trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()
    work_item = ExecutionWorkItem(
        execution_session_id=execution_session.id,
        correlation_id=correlation_id,
        plugin_key=binding.plugin_key,
        manifest_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        object_type="material",
        object_key=f"PKG-{suffix}",
        current_step="WMS_SYNC",
    )
    db.add(work_item)
    await db.flush()
    fact_version = f"resource-event:{suffix}"
    group = WmsPutawaySyncBarrierGroup(
        execution_work_item_id=int(work_item.id),
        correlation_id=correlation_id,
        fact_version=fact_version,
    )
    now_ms = int(timezone.now_utc().timestamp() * 1000)
    for ordinal, (operation_identity, source) in enumerate(
        zip((E03_CONFIRM_INBOUND, E07_NOTIFY_PKG_BINDING), sources, strict=True),
        start=1,
    ):
        capability_key, capability_version = operation_identity.rsplit("@", maxsplit=1)
        effect_status = {
            "PROPOSED": RuntimeIntentStatus.PROPOSED,
            "COMPLETED": RuntimeIntentStatus.COMPLETED,
            "RECONCILED": RuntimeIntentStatus.RECONCILING,
            "OPEN_CASE": RuntimeIntentStatus.RECONCILING,
        }[source]
        intent = RuntimeIntentLog(
            execution_session_id=execution_session.id,
            execution_work_item_id=work_item.id,
            correlation_id=correlation_id,
            provider_code="WMS",
            operation_kind="system_capability_effect",
            target_domain="wms_integration",
            target_action=operation_identity,
            idempotency_key=f"wms-sync:{suffix}:{ordinal}",
            request_hash=f"request-{suffix}-{ordinal}",
            dispatch_key=f"wms-sync-dispatch:{suffix}:{ordinal}",
            capability_key=capability_key,
            capability_contract_version=capability_version,
            operation_identity=operation_identity,
            fact_version=fact_version,
            effect_status=effect_status,
            outcome_kind=("technical_failure" if source == "OPEN_CASE" else None),
            outcome_code=("READ_TIMEOUT" if source == "OPEN_CASE" else None),
            outcome_json=({"preserved": True} if source == "OPEN_CASE" else {}),
            outcome_history_json=(
                [{"event_type": "TRANSPORT_AMBIGUOUS", "source_event_id": f"ambiguous:{suffix}:{ordinal}"}]
                if source == "OPEN_CASE"
                else []
            ),
            effect_updated_at_ms=(now_ms - 1 if source == "OPEN_CASE" else None),
        )
        db.add(intent)
        await db.flush()
        if source in {"RECONCILED", "OPEN_CASE"}:
            decision = WmsSyncObligationResolution(
                resolved_operation_identity=operation_identity,
                resolved_fact_version=fact_version,
                resolution="OBLIGATION_SATISFIED",
                source_event_id=f"resolution:{suffix}:{ordinal}",
                evidence_reference=f"evidence:{suffix}:{ordinal}",
            )
            db.add(
                ReconciliationCase(
                    runtime_intent_log_id=int(intent.id),
                    dispatch_key=intent.dispatch_key,
                    status=(
                        ReconciliationCaseStatus.RESOLVED if source == "RECONCILED" else ReconciliationCaseStatus.OPEN
                    ),
                    reason_code="MANUAL_RESOLUTION",
                    evidence_history_json=(
                        []
                        if source == "RECONCILED"
                        else [{"event_type": "TRANSPORT_AMBIGUOUS", "source_event_id": f"case:{suffix}:{ordinal}"}]
                    ),
                    decision_json=(decision.model_dump(mode="json") if source == "RECONCILED" else {}),
                    opened_at_ms=now_ms - 1,
                    resolved_at_ms=(now_ms if source == "RECONCILED" else None),
                )
            )
    await WmsPutawaySyncBarrierService().create_hold(
        db,
        group=group,
        workline_id=int(workline.id),
        session_id=None,
        trace_id=f"trace:{suffix}",
    )
    await db.commit()
    return group


async def _dispatch_keys_by_identity(
    db: AsyncSession,
    *,
    group: WmsPutawaySyncBarrierGroup,
) -> dict[str, str]:
    rows = (
        await db.execute(
            select(RuntimeIntentLog.operation_identity, RuntimeIntentLog.dispatch_key).where(
                RuntimeIntentLog.execution_work_item_id == group.execution_work_item_id
            )
        )
    ).all()
    return {str(operation_identity): str(dispatch_key) for operation_identity, dispatch_key in rows}


async def _record_sync_completion(
    db: AsyncSession,
    *,
    dispatch_key: str,
    operation_identity: str,
    source_event_id: str,
) -> object:
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.SYNC_COMPLETED,
        dispatch_key=dispatch_key,
        occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
        source_event_id=source_event_id,
        reason_code="SUCCESS",
        evidence_json={"outcome_kind": "success", "outcome_code": "SUCCESS"},
        terminal_outcome={"kind": "success", "payload": {}},
    )
    reductions = await EffectTransportBridge().record_result(
        db,
        dispatch_key=dispatch_key,
        attempt_no=1,
        result=ExternalHttpTransportResult.accepted(
            http_status_code=200,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        ),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=operation_identity,
        resolution=EffectTransportResolution(events=(event,)),
    )
    return reductions[0]


async def _get_group_hold(
    db: AsyncSession,
    *,
    group: WmsPutawaySyncBarrierGroup,
) -> RuntimeHold:
    return (
        await db.execute(
            select(RuntimeHold).where(
                RuntimeHold.source_kind == "WMS_SYNC_OBLIGATION",
                RuntimeHold.evidence_snapshot_json["fact_version"].as_string() == group.fact_version,
            )
        )
    ).scalar_one()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sources",
    [
        ("COMPLETED", "COMPLETED"),
        ("COMPLETED", "RECONCILED"),
        ("RECONCILED", "COMPLETED"),
        ("RECONCILED", "RECONCILED"),
    ],
)
async def test_completed_or_reconciled_2x2_matrix_releases_exact_group_on_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
    sources: tuple[Literal["COMPLETED", "RECONCILED"], Literal["COMPLETED", "RECONCILED"]],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        group = await _seed_group(seed_db, suffix=suffix, sources=sources)

    service = WmsPutawaySyncBarrierService()
    wrong_group = WmsPutawaySyncBarrierGroup(
        execution_work_item_id=group.execution_work_item_id,
        correlation_id=group.correlation_id,
        fact_version=f"{group.fact_version}:wrong",
    )
    async with integration_session_factory() as db:
        with pytest.raises(RuntimeError, match="hold is missing"):
            await service.evaluate_and_release(db, group=wrong_group)
        await db.rollback()

    async with integration_session_factory() as db:
        first = await service.evaluate_and_release(db, group=group)
        await db.commit()
    async with integration_session_factory() as db:
        duplicate = await service.evaluate_and_release(db, group=group)
        await db.commit()

    assert first.satisfied is True
    assert first.released is True
    assert duplicate.satisfied is True
    assert duplicate.released is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_workers_release_same_hold_exactly_once_on_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        group = await _seed_group(seed_db, suffix=suffix, sources=("COMPLETED", "RECONCILED"))

    async def evaluate() -> object:
        async with integration_session_factory() as db:
            result = await WmsPutawaySyncBarrierService().evaluate_and_release(db, group=group)
            await db.commit()
            return result

    first, second = await asyncio.gather(evaluate(), evaluate())

    assert sum(result.released for result in (first, second)) == 1
    async with integration_session_factory() as db:
        hold = (
            await db.execute(
                select(RuntimeHold).where(
                    RuntimeHold.source_kind == "WMS_SYNC_OBLIGATION",
                    RuntimeHold.evidence_snapshot_json["fact_version"].as_string() == group.fact_version,
                )
            )
        ).scalar_one()
        assert hold.status == RuntimeHoldStatus.RESOLVED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_e03_e07_dispatches_share_work_item_mutex_without_deadlock(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        group = await _seed_group(seed_db, suffix=suffix, sources=("COMPLETED", "COMPLETED"))
        dispatch_keys = tuple(
            (
                await seed_db.execute(
                    select(RuntimeIntentLog.dispatch_key)
                    .where(
                        RuntimeIntentLog.execution_work_item_id == group.execution_work_item_id,
                        RuntimeIntentLog.operation_identity.in_((E03_CONFIRM_INBOUND, E07_NOTIFY_PKG_BINDING)),
                    )
                    .order_by(RuntimeIntentLog.operation_identity.asc())
                )
            )
            .scalars()
            .all()
        )

    async def evaluate(dispatch_key: str) -> object:
        async with integration_session_factory() as db:
            service = WmsPutawaySyncBarrierService()
            locked_group = await service.lock_group_for_dispatch(db, dispatch_key=dispatch_key)
            assert locked_group == group
            result = await service.evaluate_dispatch(
                db,
                dispatch_key=dispatch_key,
                locked_group=locked_group,
            )
            await db.commit()
            assert result is not None
            return result

    async with asyncio.timeout(5):
        first, second = await asyncio.gather(*(evaluate(dispatch_key) for dispatch_key in dispatch_keys))

    assert sum(result.released for result in (first, second)) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_bridges_serialize_two_sync_completions_and_release_on_second_fact(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        group = await _seed_group(seed_db, suffix=suffix, sources=("PROPOSED", "PROPOSED"))
        dispatches = await _dispatch_keys_by_identity(seed_db, group=group)

    async with integration_session_factory() as first_db, integration_session_factory() as second_db:
        first = await _record_sync_completion(
            first_db,
            dispatch_key=dispatches[E03_CONFIRM_INBOUND],
            operation_identity=E03_CONFIRM_INBOUND,
            source_event_id=f"completion:{suffix}:e03",
        )
        assert first.intent_status == RuntimeIntentStatus.COMPLETED
        assert (await _get_group_hold(first_db, group=group)).status == RuntimeHoldStatus.OPEN

        second_task = asyncio.create_task(
            _record_sync_completion(
                second_db,
                dispatch_key=dispatches[E07_NOTIFY_PKG_BINDING],
                operation_identity=E07_NOTIFY_PKG_BINDING,
                source_event_id=f"completion:{suffix}:e07",
            )
        )
        await asyncio.sleep(0.05)
        await first_db.commit()
        async with asyncio.timeout(5):
            second = await second_task

        assert second.intent_status == RuntimeIntentStatus.COMPLETED
        assert (await _get_group_hold(second_db, group=group)).status == RuntimeHoldStatus.RESOLVED
        await second_db.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_completion_and_typed_resolution_release_without_rewriting_resolved_intent(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        group = await _seed_group(seed_db, suffix=suffix, sources=("PROPOSED", "OPEN_CASE"))
        dispatches = await _dispatch_keys_by_identity(seed_db, group=group)
        original_e07 = (
            await seed_db.execute(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == dispatches[E07_NOTIFY_PKG_BINDING])
            )
        ).scalar_one()
        original_fields = {
            "effect_status": original_e07.effect_status,
            "outcome_kind": original_e07.outcome_kind,
            "outcome_code": original_e07.outcome_code,
            "outcome_json": deepcopy(original_e07.outcome_json),
            "outcome_history_json": deepcopy(original_e07.outcome_history_json),
            "effect_updated_at_ms": original_e07.effect_updated_at_ms,
        }

    resolution = WmsSyncObligationResolution(
        resolved_operation_identity=E07_NOTIFY_PKG_BINDING,
        resolved_fact_version=group.fact_version,
        resolution="OBLIGATION_SATISFIED",
        source_event_id=f"resolution:{suffix}:e07",
        evidence_reference=f"evidence:{suffix}:e07",
    )
    async with integration_session_factory() as first_db, integration_session_factory() as second_db:
        await _record_sync_completion(
            first_db,
            dispatch_key=dispatches[E03_CONFIRM_INBOUND],
            operation_identity=E03_CONFIRM_INBOUND,
            source_event_id=f"completion:{suffix}:e03",
        )
        assert (await _get_group_hold(first_db, group=group)).status == RuntimeHoldStatus.OPEN

        second_task = asyncio.create_task(
            EffectReconciliationBridge().resolve(
                second_db,
                dispatch_key=dispatches[E07_NOTIFY_PKG_BINDING],
                occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
                resolution=None,
                obligation_resolution=resolution,
                reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
                evidence_json={"operator_id": 88},
                source_event_id=resolution.source_event_id,
            )
        )
        await asyncio.sleep(0.05)
        await first_db.commit()
        async with asyncio.timeout(5):
            reconciled = await second_task

        assert reconciled.intent_status == RuntimeIntentStatus.RECONCILING
        assert reconciled.case_status == ReconciliationCaseStatus.RESOLVED
        assert (await _get_group_hold(second_db, group=group)).status == RuntimeHoldStatus.RESOLVED
        second_db.expire_all()
        current_e07 = (
            await second_db.execute(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == dispatches[E07_NOTIFY_PKG_BINDING])
            )
        ).scalar_one()
        assert {
            "effect_status": current_e07.effect_status,
            "outcome_kind": current_e07.outcome_kind,
            "outcome_code": current_e07.outcome_code,
            "outcome_json": current_e07.outcome_json,
            "outcome_history_json": current_e07.outcome_history_json,
            "effect_updated_at_ms": current_e07.effect_updated_at_ms,
        } == original_fields
        await second_db.commit()
