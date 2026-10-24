"""人工来源架当前事实的迁移、真实审计与并发幂等验证。"""

import asyncio
import hashlib
import subprocess

import pytest
from manual_picking.application.manual_source_arrival import ManualSourceArrivalService
from manual_picking.application.manual_source_arrival_model import ManualPickingSourceArrival
from manual_picking.application.manual_source_arrival_repository import ManualSourceArrivalRepository
from rack_cycle_support import seed_line
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deployment.plugin_models import include_plugin_table, load_plugin_models
from src.app.execution.models import TransportDecisionBinding
from src.app.sys.models.audit_log import AuditLog
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
REVISION = "e3fec1d9ffae"
PARENT = "c6e209af714b"


async def test_manual_arrival_migration_audit_and_concurrent_recovery(integration_guard):
    async with temporary_database() as (_, url):
        run_alembic("upgrade", PARENT, database_url=url)
        run_alembic("upgrade", REVISION, database_url=url)
        run_alembic("downgrade", PARENT, database_url=url)
        run_alembic("upgrade", REVISION, database_url=url)
        load_plugin_models(("manual-picking",))
        assert include_plugin_table("manual_picking_source_arrivals", ("manual-picking",))
        assert not include_plugin_table("manual_picking_source_arrivals", ())
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as db:
                line, task = await seed_line(db, bins=0)
                task.status = "EXECUTING"
                rack = "510022"
                location = line.position_bindings["FIVE_RACK"]["location_id"]
                db.add(
                    PickingTaskBinSourceRack(
                        picking_task_id=task.id,
                        plan_revision=16,
                        rack_id=rack,
                        rack_face="270",
                        source_evidence_id=task.issued_evidence_id,
                    )
                )
                client = new_uuid7()
                identity = "transport-" + new_uuid7()
                now = timezone.now_for_db()
                binding = TransportDecisionBinding(
                    workline_id=line.id,
                    picking_task_id=task.id,
                    source_evidence_id=task.issued_evidence_id,
                    correlation_id="manual-arrival",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id=rack,
                    client_request_id=client,
                    window_target_location_code=location,
                )
                db.add(binding)
                db.add(
                    TransportTask(
                        transport_task_id=identity,
                        client_request_id=client,
                        kind="RACK_MOVE",
                        authority_workline_id=line.id,
                        caller_json={},
                        request_json={"target_face": "270"},
                        request_digest="a" * 64,
                        submit_operation_id=new_uuid7(),
                        submit_timestamp_ms=1,
                        submit_request_body="{}",
                        submit_request_body_digest=hashlib.sha256(b"{}").hexdigest(),
                        status="ACCEPTED",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.flush()
                db.add(
                    TransportMember(
                        transport_task_id=identity,
                        ordinal=0,
                        object_type="RACK",
                        object_id=rack,
                        source_json={"kind": "RACK", "location_code": rack},
                        target_json={"kind": "RACK_POSITION", "location_code": location},
                        status="ACCEPTED",
                        updated_at=now,
                    )
                )
            args = {
                "workline_id": line.id,
                "picking_task_id": task.id,
                "plan_revision": 16,
                "rack_id": rack,
                "rack_face": "270",
                "location_code": location,
                "transport_task_id": identity,
                "confirmed_by": "现场负责人",
                "expected_request_body_digest": hashlib.sha256(b"{}").hexdigest(),
                "confirmation_reference": "当前作业位已确认",
            }
            service = ManualSourceArrivalService(sessions=sessions)
            first, replay = await asyncio.gather(service.confirm(**args), service.confirm(**args))
            assert first == replay
            async with sessions.begin() as db:
                fact = await ManualSourceArrivalRepository().current(db, line.id)
                audit = await db.get(AuditLog, first)
                original = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == identity))
                assert fact.audit_id == first and audit.action == "MANUAL_SOURCE_RACK_ARRIVED"
                assert audit.args["confirmation"]["transport_task_id"] == identity
                assert (original.status, original.outcome_version, original.published_outcome_version) == (
                    "ACCEPTED",
                    0,
                    0,
                )
                assert await db.scalar(select(ManualPickingSourceArrival.audit_id)) == first
            with pytest.raises(subprocess.CalledProcessError) as rejected:
                run_alembic("downgrade", PARENT, database_url=url)
            assert "would discard current facts" in rejected.value.stderr
            async with sessions.begin() as db:
                assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == REVISION
        finally:
            await engine.dispose()
