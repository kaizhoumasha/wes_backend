"""WMS EFFECT status migration、短事务 claim 与 lease fencing 的 PostgreSQL 验证。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.notify_package_binding_effect_preparation_service import (
    NotifyPackageBindingEffectPreparationService,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
    NotifyPackageBindingEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.sys.models.outbox import SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database
from tests.support.runtime_inbox_processing_postgresql import with_temporary_runtime_database

REVISION = "65e212c90737"
PARENT = "6ea20f0c0d22"


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
