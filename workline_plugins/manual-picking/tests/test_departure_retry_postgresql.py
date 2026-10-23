"""真实 PostgreSQL 验证清退退避迁移、跨进程恢复及并发唯一重新决定。"""

import asyncio
import subprocess
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from manual_picking.application.batch_driver import SOURCE_RACK_OUT_STEP, ManualPickingBatchDriver
from manual_picking.application.departure_retry_model import ManualPickingDepartureRetry
from rack_cycle_support import locked_line, seed_line
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deployment.plugin_models import include_plugin_table, load_plugin_models
from src.app.execution.models import WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureScheduler
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def driver():
    return ManualPickingBatchDriver(
        None,
        plans=None,
        positions=None,
        transports=None,
        rack_creator=None,
        departure_reader=None,
        departure_scheduler=RackDepartureScheduler(
            WmsConfirmationLifecycleService(workline_owner=RackDepartureOwnerService())
        ),
    )


async def test_migration_keeps_prior_rows_and_registers_only_installed_plugin(integration_guard):
    load_plugin_models(("manual-picking",))
    assert include_plugin_table("manual_picking_departure_retries", ()) is False
    assert include_plugin_table("manual_picking_departure_retries", ("manual-picking",)) is True
    async with temporary_database() as (_, url):
        run_alembic("upgrade", "c6e209af714b", database_url=url)
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as db:
                line, _ = await seed_line(db, bins=0)
                line_id = line.id
            run_alembic("upgrade", "a72d4e6c9b13", database_url=url)
            async with sessions.begin() as db:
                assert (
                    await db.scalar(text("SELECT count(*) FROM wes_biz.work_lines WHERE id=:id"), {"id": line_id}) == 1
                )
                assert await db.scalar(select(ManualPickingDepartureRetry)) is None
            run_alembic("downgrade", "c6e209af714b", database_url=url)
            run_alembic("upgrade", "a72d4e6c9b13", database_url=url)
        finally:
            await engine.dispose()


async def test_redecision_is_atomic_persistent_and_serialized_by_workline(integration_guard, monkeypatch):
    import src.app.wms_integration.outbound_picking.services.rack_departure as departure_module

    queued = Mock()
    monkeypatch.setattr(departure_module, "task_queue_gateway", SimpleNamespace(enqueue_wms_confirmations=queued))
    async with temporary_database() as (_, url):
        run_alembic("upgrade", "a72d4e6c9b13", database_url=url)
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as db:
                line, parent = await seed_line(db, bins=0)
                line_id, parent_id = line.id, parent.id
            binding = SimpleNamespace(picking_task_id=parent_id, causal_token=1, correlation_id="departure:first")
            rejected = SimpleNamespace(transport_task_id="rejected-1", outcome_version=1, published_outcome_version=1)
            kwargs = {
                "scope_identity": "arrival:first",
                "rack_id": "R1",
                "step": SOURCE_RACK_OUT_STEP,
                "location": "KT18",
                "face": "90",
            }
            now = timezone.now_for_db()
            async with sessions.begin() as db:
                line = await locked_line(db, line_id)
                assert (
                    await driver()._redecide_rejected_departure(db, line, (binding, rejected), now=now, **kwargs) == 0
                )
            async with sessions.begin() as db:
                row = await db.scalar(select(ManualPickingDepartureRetry))
                assert row.next_attempt_at == now + timedelta(seconds=1)
                assert row.retry_count == 1
            # Transaction rollback cannot consume the one due decision or leak a request.
            async with sessions() as db:
                await db.begin()
                line = await locked_line(db, line_id)
                assert (
                    await driver()._redecide_rejected_departure(
                        db, line, (binding, rejected), now=now + timedelta(seconds=1), **kwargs
                    )
                    == 1
                )
                await db.rollback()
            async with sessions.begin() as db:
                assert await db.scalar(select(WmsConfirmation)) is None
                assert (await db.scalar(select(ManualPickingDepartureRetry))).decision_operation_id is None

            async def due_scan():
                async with sessions.begin() as db:
                    line = await locked_line(db, line_id)
                    return await driver()._redecide_rejected_departure(
                        db, line, (binding, rejected), now=now + timedelta(seconds=1), **kwargs
                    )

            assert sorted(await asyncio.gather(due_scan(), due_scan())) == [0, 1]
            async with sessions.begin() as db:
                confirmations = list(await db.scalars(select(WmsConfirmation)))
                row = await db.scalar(select(ManualPickingDepartureRetry))
                assert len(confirmations) == 1
                assert row.decision_operation_id == confirmations[0].operation_id
                first_body = confirmations[0].request_payload
                first_digest = confirmations[0].request_digest
            # A fresh driver/session seeing the next rejected identity must wait two seconds.
            binding.causal_token = 2
            binding.correlation_id = "departure:new-decision"
            rejected.transport_task_id = "rejected-2"
            async with sessions.begin() as db:
                line = await locked_line(db, line_id)
                assert (
                    await driver()._redecide_rejected_departure(
                        db, line, (binding, rejected), now=now + timedelta(seconds=2), **kwargs
                    )
                    == 0
                )
                row = await db.scalar(select(ManualPickingDepartureRetry))
                assert row.retry_count == 2 and row.next_attempt_at == now + timedelta(seconds=4)
                original = await db.scalar(select(WmsConfirmation))
                assert original.request_payload == first_body and original.request_digest == first_digest
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(text("UPDATE wes_biz.manual_picking_departure_retries SET retry_count=0"))
            with pytest.raises(subprocess.CalledProcessError) as failure:
                run_alembic("downgrade", "c6e209af714b", database_url=url)
            assert "departure retry cursors exist" in failure.value.stderr
        finally:
            await engine.dispose()
