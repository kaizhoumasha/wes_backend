"""真实 PostgreSQL 验证投料请求归属迁移与可靠义务原子提交。"""

import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from drain_execution_support import unbound_drain_executions
from manual_picking.application.batch_flow import ManualPickingBatchFlow
from manual_picking.application.batch_progress_model import ManualPickingInboundBatch
from manual_picking.application.batch_repository import BatchRepository
from rack_cycle_support import seed_line
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_batch_flow import _Inbound, _Passages

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchScheduler
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _intent(task_id, operation_id):
    return sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id=operation_id, task_id=task_id, rack_id="R1", rack_face="90"
    )


async def test_migration_preserves_allocations_and_refuses_pending_request_downgrade(integration_guard):
    async with temporary_database() as (_, url):
        run_alembic("upgrade", "0f1be9a476d2", database_url=url)
        engine = create_async_engine(url)
        repo = BatchRepository()
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as db:
                line, task = await seed_line(db, bins=0)
                line_id, parent_id, task_id = line.id, task.id, task.task_id
                evidence = InboundEvidence(
                    kind="WMS_RESULT",
                    source_identity="p1:original-response",
                    payload_digest="b" * 64,
                    normalized_payload={},
                    received_at=timezone.now_for_db(),
                    workline_id=line_id,
                    operation="outbound.bin.inbound_batch@v1",
                    operation_id="original",
                )
                db.add(evidence)
                await db.flush()
                evidence_id = evidence.id
                await repo.record_allocation(
                    db,
                    workline_id=line_id,
                    picking_task_id=parent_id,
                    plan_revision=1,
                    intent=_intent(task_id, "original"),
                    evidence_id=evidence_id,
                )
            await engine.dispose()
            run_alembic("upgrade", "head", database_url=url)
            engine = create_async_engine(url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as db:
                old = await repo.inbound_request(db, workline_id=line_id, operation_id="original")
                assert (old.plan_revision, old.response_evidence_id) == (1, evidence_id)
                await repo.record_allocation(
                    db,
                    workline_id=line_id,
                    picking_task_id=parent_id,
                    plan_revision=5,
                    intent=_intent(task_id, "pending"),
                    evidence_id=None,
                )
            await engine.dispose()
            with pytest.raises(subprocess.CalledProcessError) as error:
                run_alembic("downgrade", "0f1be9a476d2", database_url=url)
            assert "pending inbound requests exist" in error.value.stderr
            engine = create_async_engine(url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as db:
                pending = await repo.inbound_request(db, workline_id=line_id, operation_id="pending")
                assert (pending.plan_revision, pending.response_evidence_id) == (5, None)
                await db.delete(pending)
            await engine.dispose()
            run_alembic("downgrade", "0f1be9a476d2", database_url=url)
            run_alembic("upgrade", "head", database_url=url)
        finally:
            await engine.dispose()


async def test_request_binding_and_wms_obligation_commit_or_rollback_together(integration_guard):
    async with temporary_database() as (_, url):
        run_alembic("upgrade", "head", database_url=url)
        engine = create_async_engine(url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as db:
                line, task = await seed_line(db, bins=0)
                line_id, parent_id, task_id = line.id, task.id, task.task_id
            repo = BatchRepository()
            repo.has_unclosed_action_for_face = AsyncMock(return_value=False)
            scheduler = BinBatchScheduler(
                WmsConfirmationLifecycleService(
                    workline_owner=SimpleNamespace(validate_owner=AsyncMock(return_value=True)),
                )
            )
            flow = ManualPickingBatchFlow(
                repo,
                _Passages(()),
                scheduler,
                _Inbound(),
                uuid_factory=lambda: "019f0000-0000-7000-8000-000000000901",
                drain_executions=unbound_drain_executions(),
            )
            kwargs = {
                "workline_id": line_id,
                "workline_code": line.line_code,
                "picking_task_id": parent_id,
                "task_id": task_id,
                "plan_revision": 5,
                "source_evidence_id": 11,
                "rack_id": "R1",
                "rack_face": "90",
                "return_location": "RETURN",
                "inlet_location": "INLET",
                "now": timezone.now_for_db(),
            }
            with pytest.raises(RuntimeError, match="rollback"):
                async with sessions.begin() as db:
                    assert await flow.advance_in_session(db, **kwargs)
                    raise RuntimeError("rollback")
            async with sessions.begin() as db:
                assert await db.scalar(select(ManualPickingInboundBatch)) is None
                assert await db.scalar(select(WmsConfirmation)) is None
                assert await flow.advance_in_session(db, **kwargs)
            await engine.dispose()
            engine = create_async_engine(url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as db:
                frozen = await repo.inbound_request(
                    db, workline_id=line_id, operation_id="019f0000-0000-7000-8000-000000000901"
                )
                request = await db.scalar(select(WmsConfirmation))
                assert (frozen.plan_revision, frozen.response_evidence_id) == (5, None)
                assert request.operation_id == frozen.operation_id
                assert request.request_payload["data"] == {"task_id": task_id, "rack_id": "R1", "rack_face": "90"}
        finally:
            await engine.dispose()
