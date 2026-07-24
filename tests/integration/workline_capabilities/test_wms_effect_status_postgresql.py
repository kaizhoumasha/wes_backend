"""WMS EFFECT status migration、短事务 claim 与 lease fencing 的 PostgreSQL 验证。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer
from src.app.runtime.orchestration.services.notify_package_binding_effect_preparation_service import (
    NotifyPackageBindingEffectPreparationService,
)
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
    NotifyPackageBindingEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.sys.models.outbox import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.effect_status import (
    NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
    WmsEffectStatus,
    WmsEffectStatusSnapshot,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.runtime_inbox_processing_postgresql import with_temporary_runtime_database

REVISION = "65e212c90737"
PARENT = "6ea20f0c0d22"


async def _seed_status_pair(db, *, suffix: str) -> tuple[NotifyPackageBindingOperationRequest, RuntimeIntentLog]:
    execution_session = ExecutionSession(workline_id=1, manifest_version="v1", state="RUNNING")
    db.add(execution_session)
    await db.flush()
    assert execution_session.id is not None
    correlation = ExecutionCorrelation(
        correlation_id=f"wms-status-pg-correlation-{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"wms-status-pg-trace-{suffix}",
    )
    db.add(correlation)
    await db.flush()
    request = NotifyPackageBindingOperationRequest(
        dispatch_key=f"wms-status-pg-dispatch-{suffix}",
        provider_code="WMS",
        package_id=f"PKG-PG-{suffix}",
        pallet_id=f"PALLET-PG-{suffix}",
        station_code="ST-PG-001",
    )
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="notify_pkg_binding",
        idempotency_key=f"wms-status-pg-idempotency-{suffix}",
        request_hash="a" * 64,
        dispatch_key=request.dispatch_key,
        capability_key="wms.fulfillment.notify_pkg_binding",
        capability_contract_version="v1",
        operation_identity=f"WMS:PKG-PG-{suffix}:PALLET-PG-{suffix}",
    )
    adapter = NotifyPackageBindingEffectAdapter(
        gateway=NotifyPackageBindingDispatchGateway(
            registry=EndpointRegistry({"WMS_PACKAGE_BINDING": "https://wms.example/effects/package-binding"})
        )
    )
    outbox = await NotifyPackageBindingEffectPreparationService().prepare(
        db,
        request=request,
        intent_log=intent,
        adapter=adapter,
    )
    await db.commit()
    outbox.status = SystemOutboxStatus.SENT
    await db.commit()
    return request, intent


def _status_snapshot(
    request: NotifyPackageBindingOperationRequest,
    *,
    state: WmsEffectStatus,
    source_version: int,
    provider_reference: str,
) -> WmsEffectStatusSnapshot:
    return WmsEffectStatusSnapshot(
        operation_identity=NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
        idempotency_key=f"wms-status-pg-idempotency-{request.dispatch_key.rsplit('-', maxsplit=1)[-1]}",
        state=state,
        provider_reference=provider_reference,
        updated_at=datetime(2026, 7, 24, 12, source_version, tzinfo=UTC),
        source_version=source_version,
    )


@pytest.mark.integration
def test_wms_effect_status_migration_upgrade_downgrade_roundtrip() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", PARENT, database_url=database_url)
            run_alembic("upgrade", REVISION, database_url=database_url)
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    columns = set(
                        (
                            await connection.execute(
                                text(
                                    "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_schema = 'wes_runtime' "
                                    "AND table_name = 'runtime_intent_logs'"
                                )
                            )
                        ).scalars()
                    )
                    assert {
                        "status_check_started_at",
                        "status_check_after",
                        "status_check_count",
                        "status_resubmit_count",
                        "status_source_version",
                        "status_check_lease_token",
                        "status_check_lease_until",
                        "status_binding_snapshot_json",
                        "status_binding_snapshot_hash",
                    } <= columns
                    assert await connection.scalar(
                        text("SELECT to_regclass('wes_runtime.ix_runtime_intent_log_effect_status_check_after')")
                    )
            finally:
                await engine.dispose()

            run_alembic("downgrade", PARENT, database_url=database_url)
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT COUNT(*) FROM information_schema.columns "
                                "WHERE table_schema = 'wes_runtime' "
                                "AND table_name = 'runtime_intent_logs' "
                                "AND column_name LIKE 'status_%'"
                            )
                        )
                        == 0
                    )
            finally:
                await engine.dispose()
            run_alembic("upgrade", REVISION, database_url=database_url)

    asyncio.run(scenario())


@pytest.mark.integration
def test_status_claim_is_short_reclaimable_and_old_worker_is_fenced() -> None:
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        now = timezone.now_for_db()
        repository = WmsEffectStatusRepository()
        async with session_factory() as db:
            execution_session = ExecutionSession(workline_id=1, manifest_version="v1", state="RUNNING")
            db.add(execution_session)
            await db.flush()
            assert execution_session.id is not None
            correlation = ExecutionCorrelation(
                correlation_id="wms-status-pg-correlation",
                execution_session_id=execution_session.id,
                trace_id="wms-status-pg-trace",
            )
            db.add(correlation)
            await db.flush()
            request = NotifyPackageBindingOperationRequest(
                dispatch_key="wms-status-pg-dispatch",
                provider_code="WMS",
                package_id="PKG-PG-001",
                pallet_id="PALLET-PG-001",
                station_code="ST-PG-001",
            )
            intent = RuntimeIntentLog(
                execution_session_id=execution_session.id,
                correlation_id=correlation.correlation_id,
                provider_code="WMS",
                operation_kind="system_capability_effect",
                target_domain="wms_integration",
                target_action="notify_pkg_binding",
                idempotency_key="wms-status-pg-idempotency",
                request_hash="a" * 64,
                dispatch_key=request.dispatch_key,
                capability_key="wms.fulfillment.notify_pkg_binding",
                capability_contract_version="v1",
                operation_identity="WMS:PKG-PG-001:PALLET-PG-001",
            )
            adapter = NotifyPackageBindingEffectAdapter(
                gateway=NotifyPackageBindingDispatchGateway(
                    registry=EndpointRegistry({"WMS_PACKAGE_BINDING": "https://wms.example/effects/package-binding"})
                )
            )
            outbox = await NotifyPackageBindingEffectPreparationService().prepare(
                db,
                request=request,
                intent_log=intent,
                adapter=adapter,
            )
            await db.commit()
            outbox.status = SystemOutboxStatus.SENT
            await db.commit()

            first = await repository.claim_by_dispatch_key(
                db,
                dispatch_key=request.dispatch_key,
                now=now,
                lease_seconds=10,
            )
            assert first is not None
            await db.commit()
            first_token = first.lease_token
            assert first.intent.status_check_count == 1

            first.intent.status_check_lease_until = now - timedelta(seconds=1)
            await db.commit()
            second = await repository.claim_by_dispatch_key(
                db,
                dispatch_key=request.dispatch_key,
                now=now,
                lease_seconds=10,
            )
            assert second is not None and second.lease_token != first_token
            await db.commit()

            assert (
                await repository.get_claim_for_update(
                    db,
                    dispatch_key=request.dispatch_key,
                    lease_token=first_token,
                )
                is None
            )
            assert await repository.release_claim(db, claim=second, status_check_after=None)
            await db.commit()

    asyncio.run(with_temporary_runtime_database(scenario))


@pytest.mark.integration
def test_status_service_uses_real_repository_and_reducer_outside_http_transaction() -> None:
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            request, intent = await _seed_status_pair(db, suffix="service")
            query_calls = 0

            class Port:
                async def query_status(self, _request):  # type: ignore[no-untyped-def]
                    nonlocal query_calls
                    assert db.in_transaction() is False, "HTTP 必须在 claim 提交且无行锁事务时执行"
                    query_calls += 1
                    return _status_snapshot(
                        request,
                        state=WmsEffectStatus.PROCESSING,
                        source_version=2,
                        provider_reference="provider-service-v2",
                    )

            service = WmsEffectStatusService(port_factory_builder=lambda _binding: Port)
            first = await service.check_dispatch(db, dispatch_key=request.dispatch_key)
            duplicate = await service.check_dispatch(db, dispatch_key=request.dispatch_key)

            assert first.outcome == WmsEffectStatus.PROCESSING.value
            assert duplicate.outcome == "SKIPPED"
            assert query_calls == 1
            persisted = await db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == request.dispatch_key)
            )
            assert persisted is not None
            assert persisted.effect_status is RuntimeIntentStatus.ACCEPTED
            assert persisted.status_source_version == 2

            # binding 损坏仍须由显式 WMS operation identity claim，并在网络前唯一 fail-closed。
            persisted.status_check_after = timezone.now_for_db() - timedelta(seconds=1)
            persisted.status_binding_snapshot_hash = None
            await db.commit()
            corrupt = await service.check_dispatch(db, dispatch_key=request.dispatch_key)
            replay = await service.check_dispatch(db, dispatch_key=request.dispatch_key)

            assert corrupt.outcome == "RECONCILING"
            assert replay.outcome == "SKIPPED"
            assert query_calls == 1
            assert intent.operation_identity == "WMS:PKG-PG-service:PALLET-PG-service"
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ReconciliationCase)
                    .where(
                        ReconciliationCase.dispatch_key == request.dispatch_key,
                        ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                    )
                )
                == 1
            )

    asyncio.run(with_temporary_runtime_database(scenario))


@pytest.mark.integration
def test_real_status_service_orders_versions_and_real_reducer_serializes_terminal_race() -> None:
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            request, _intent = await _seed_status_pair(db, suffix="versions")
            snapshots = [
                _status_snapshot(
                    request,
                    state=WmsEffectStatus.PROCESSING,
                    source_version=2,
                    provider_reference="provider-version-2",
                ),
                _status_snapshot(
                    request,
                    state=WmsEffectStatus.PROCESSING,
                    source_version=1,
                    provider_reference="provider-version-1",
                ),
                _status_snapshot(
                    request,
                    state=WmsEffectStatus.ACCEPTED,
                    source_version=2,
                    provider_reference="provider-version-2-conflict",
                ),
            ]

            class Port:
                async def query_status(self, _request):  # type: ignore[no-untyped-def]
                    assert db.in_transaction() is False
                    return snapshots.pop(0)

            service = WmsEffectStatusService(port_factory_builder=lambda _binding: Port)
            first = await service.check_dispatch(db, dispatch_key=request.dispatch_key)
            persisted = await db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == request.dispatch_key)
            )
            assert persisted is not None
            persisted.status_check_after = timezone.now_for_db() - timedelta(seconds=1)
            await db.commit()
            stale = await service.check_dispatch(db, dispatch_key=request.dispatch_key)
            persisted.status_check_after = timezone.now_for_db() - timedelta(seconds=1)
            await db.commit()
            conflict = await service.check_dispatch(db, dispatch_key=request.dispatch_key)

            assert first.outcome == WmsEffectStatus.PROCESSING.value
            assert stale.outcome == "STALE"
            assert conflict.outcome == "RECONCILING"
            assert persisted.status_source_version == 2
            assert persisted.effect_status is RuntimeIntentStatus.RECONCILING

            race_request, _race_intent = await _seed_status_pair(db, suffix="race")

        async def reduce_terminal(event_type: EffectReducerEventType, source: str) -> None:
            async with session_factory() as worker_db:
                await EffectReducer().reduce(
                    worker_db,
                    EffectReducerEvent(
                        event_type=event_type,
                        dispatch_key=race_request.dispatch_key,
                        occurred_at_ms=1_800_000_000_000,
                        source_event_id=source,
                        evidence_json={"source_version": 7, "snapshot_hash": source},
                    ),
                )
                await worker_db.commit()

        await asyncio.gather(
            reduce_terminal(EffectReducerEventType.STATUS_COMPLETED, "terminal-completed"),
            reduce_terminal(EffectReducerEventType.STATUS_REJECTED, "terminal-rejected"),
        )
        # 重放冲突事实只能追加/复用同一 OPEN case，不能产生第二个裁决对象。
        await reduce_terminal(EffectReducerEventType.STATUS_REJECTED, "terminal-rejected")

        async with session_factory() as verify_db:
            raced = await verify_db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == race_request.dispatch_key)
            )
            assert raced is not None
            assert raced.effect_status in {RuntimeIntentStatus.COMPLETED, RuntimeIntentStatus.REJECTED}
            assert (
                await verify_db.scalar(
                    select(func.count())
                    .select_from(ReconciliationCase)
                    .where(
                        ReconciliationCase.dispatch_key == race_request.dispatch_key,
                        ReconciliationCase.status == ReconciliationCaseStatus.OPEN,
                    )
                )
                == 1
            )

    asyncio.run(with_temporary_runtime_database(scenario))
