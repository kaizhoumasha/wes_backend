"""插件排空 schema、工作线锁及存量身份的独占 PostgreSQL 验证。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from manual_picking.application.drain_execution_model import ManualPickingDrainExecution, ManualPickingDrainReturnMember
from manual_picking.application.drain_execution_repository import DrainExecutionRepository
from manual_picking.application.drain_repository import DRAIN_RACK_IN_STEP, DRAIN_RACK_OUT_STEP, DrainRepository
from rack_cycle_support import locked_line, rack_database, seed_line
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wes_plugin_sdk import RackFaceSequence, ReturnBufferDrainReady, wms_operations

from deployment.plugin_models import include_plugin_table, load_plugin_models
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    TransportDecisionBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.transport.models import TransportTask
from src.app.wms_adapter.return_buffer_drain.typed import encode_request
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

REVISION = "9d4e6b7a120c"
PARENT_REVISION = "a16c9b2e7d04"


async def seed_ready(db, line, *, racks=None):
    racks = racks or (RackFaceSequence("R1", ("90",)),)
    now = timezone.now_for_db()
    intent = wms_operations.workline_return_buffer_drain_rack_decide(
        operation_id=new_uuid7(),
        workline_code=line.line_code,
        required_slot_count=sum(len(rack.rack_faces) for rack in racks),
    )
    payload = encode_request(intent, timestamp=1)
    answer = {
        "operation_id": intent.operation_id,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {
            "result": "READY",
            "racks": [{"rack_id": rack.rack_id, "rack_face": list(rack.rack_faces)} for rack in racks],
        },
    }
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{RETURN_BUFFER_DRAIN_OPERATION}:{intent.operation_id}",
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=intent.operation_id,
        workline_id=line.id,
        normalized_payload=answer,
        payload_digest=canonical_json_digest(answer),
        received_at=now,
        processed_at=now,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    db.add(evidence)
    await db.flush()
    evidence.decision_digest = canonical_json_digest(
        {"evidence_id": evidence.id, "role": "WMS_DRAIN", "result": "DRAIN_RECEIVED"}
    )
    evidence.published_at = now
    confirmation = WmsConfirmation(
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=intent.operation_id,
        workline_id=line.id,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        deadline_at=now + timedelta(minutes=1),
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=evidence.id,
        response_result="READY",
        completed_at=now,
    )
    db.add(confirmation)
    await db.flush()
    return ReturnBufferDrainRecord(
        line.id,
        WmsConfirmationStatus.COMPLETED,
        now,
        now,
        intent,
        ReturnBufferDrainReady(racks),
        evidence.id,
    )


async def seed_rack_transport(db, line, decision, *, departure=False):
    now, identity = timezone.now_for_db(), new_uuid7()
    client = f"transport-{identity}"
    task = TransportTask(
        transport_task_id=identity,
        client_request_id=client,
        request_digest="a" * 64,
        kind="RACK_MOVE",
        caller_json={},
        request_json={"rack_id": "R1"},
        submit_operation_id=new_uuid7(),
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="b" * 64,
        status="ACCEPTED" if departure else "PENDING",
        authority_workline_id=line.id,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.add(
        TransportDecisionBinding(
            workline_id=line.id,
            picking_task_id=None,
            source_evidence_id=decision.evidence_id,
            correlation_id=f"drain:{decision.intent.operation_id}:rack:R1",
            step=DRAIN_RACK_OUT_STEP if departure else DRAIN_RACK_IN_STEP,
            resource_fence_id="R1",
            client_request_id=client,
        )
    )
    await db.flush()
    return client


@asynccontextmanager
async def previous_schema_database():
    async with temporary_database() as (_, url):
        run_alembic("upgrade", PARENT_REVISION, database_url=url)
        engine = create_async_engine(url)
        try:
            yield url, async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()


@pytest.mark.parametrize("terminal_status", ["COMPLETED", "EXCEPTION_RETIRED", "MANUAL_WITHDRAWN"])
async def test_execution_constraints_and_identity_concurrency(rack_database, terminal_status):
    _, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        line, task = await seed_line(db, bins=0)
        decision = await seed_ready(db, line)
        blocker = await seed_rack_transport(db, line, decision)

    async def register():
        async with sessions.begin() as db:
            await locked_line(db, line.id)
            return (await repository.ensure_ready_in_session(db, decision=decision)).id

    first, replay = await asyncio.gather(register(), register())
    assert first == replay
    async with sessions.begin() as db:
        execution = await repository.active_for_workline(db, line.id)
        assert execution.id == first
        for invalid in ("UNKNOWN", "EXCEPTION_RETIRED", "MANUAL_WITHDRAWN"):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        text("UPDATE wes_biz.manual_picking_drain_executions SET status=:status WHERE id=:id"),
                        {"status": invalid, "id": first},
                    )
        retirement = (
            {}
            if terminal_status == "COMPLETED"
            else {
                "reason_code": "MANUAL_SCOPE_EXIT" if terminal_status == "MANUAL_WITHDRAWN" else "DRAIN_RESULT_UNKNOWN",
                "blocker_client_request_id": blocker,
                "trigger_picking_task_id": task.id,
            }
        )
        assert await repository.close_in_session(
            db, execution, status=terminal_status, closed_at=timezone.now_for_db(), **retirement
        )
        assert await repository.active_for_workline(db, line.id) is None
        assert await DrainRepository().current(db, line.id) is None
        assert (await repository.ensure_ready_in_session(db, decision=decision)).status == terminal_status
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    ManualPickingDrainExecution(
                        workline_id=line.id,
                        drain_operation_id=decision.intent.operation_id,
                        decision_evidence_id=decision.evidence_id,
                    )
                )
                await db.flush()


@pytest.mark.parametrize("departure", [False, True])
async def test_runtime_chain_backfill_preserves_unclosed_references(integration_guard, departure):
    async with previous_schema_database() as (url, sessions):
        async with sessions.begin() as db:
            # 历史迁移只需工作线，不用当前 PickingTask ORM 写入尚不存在的列。
            line = WorkLine(
                line_code="LEGACY-DRAIN",
                line_name="历史排空迁移",
                line_type="MANUAL",
                plugin_key="manual-picking",
                plugin_version="0.1.0",
                is_active=True,
            )
            db.add(line)
            await db.flush()
            decision = await seed_ready(db, line)
            client = await seed_rack_transport(db, line, decision, departure=departure)
        run_alembic("upgrade", REVISION, database_url=url)
        async with sessions() as db:
            execution = await db.scalar(
                select(ManualPickingDrainExecution).where(
                    ManualPickingDrainExecution.drain_operation_id == decision.intent.operation_id
                )
            )
            assert execution.status == ("COMPLETED" if departure else "ACTIVE")
            assert execution.decision_evidence_id == decision.evidence_id
            assert (
                await db.scalar(
                    select(TransportTask.client_request_id).where(TransportTask.client_request_id == client)
                )
                == client
            )
            assert (
                await db.scalar(
                    select(TransportDecisionBinding.source_evidence_id).where(
                        TransportDecisionBinding.client_request_id == client
                    )
                )
                == decision.evidence_id
            )
            assert (await DrainRepository().current(db, line.id) is None) == departure


async def test_unclosed_return_without_frozen_drain_identity_rejects_backfill(integration_guard):
    import subprocess

    async with previous_schema_database() as (url, sessions):
        async with sessions.begin() as db:
            # 历史迁移只需工作线，不用当前 PickingTask ORM 写入尚不存在的列。
            line = WorkLine(
                line_code="LEGACY-DRAIN",
                line_name="历史排空迁移",
                line_type="MANUAL",
                plugin_key="manual-picking",
                plugin_version="0.1.0",
                is_active=True,
            )
            db.add(line)
            await db.flush()
            decision = await seed_ready(db, line)
            await seed_rack_transport(db, line, decision)
            identity, now = new_uuid7(), timezone.now_for_db()
            db.add(
                WmsConfirmation(
                    workline_id=line.id,
                    operation="outbound.bin.return_batch@v1",
                    operation_id=identity,
                    request_payload={},
                    request_digest=canonical_json_digest({}),
                    deadline_at=now + timedelta(minutes=1),
                )
            )
        with pytest.raises(subprocess.CalledProcessError) as failure:
            run_alembic("upgrade", REVISION, database_url=url)
        message = failure.value.stderr
        assert "drain migration preflight" in message
        assert identity in message
        assert f"workline={line.id}" in message
        assert "lacks original drain" in message
        async with sessions() as db:
            assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == PARENT_REVISION
            assert await db.scalar(text("SELECT to_regclass('wes_biz.manual_picking_drain_executions')")) is None


async def test_upgrade_downgrade_reupgrade_drain_schema(integration_guard):
    async with previous_schema_database() as (url, sessions):
        run_alembic("upgrade", REVISION, database_url=url)
        async with sessions() as db:
            for table in (ManualPickingDrainExecution, ManualPickingDrainReturnMember):
                assert await db.scalar(text("SELECT to_regclass(:name)"), {"name": f"wes_biz.{table.__tablename__}"})
        run_alembic("downgrade", PARENT_REVISION, database_url=url)
        run_alembic("upgrade", REVISION, database_url=url)
        load_plugin_models(("manual-picking",))
        for name in ("manual_picking_drain_executions", "manual_picking_drain_return_members"):
            assert include_plugin_table(name, ("manual-picking",))
            assert not include_plugin_table(name, ())


async def request_return(db, line, decision, repository, *, rack_id="R1"):
    from manual_picking.application.bin_line.return_repository import ReturnRepository
    from manual_picking.application.drain_flow import ManualPickingDrainFlow

    from src.app.execution.services import WmsConfirmationLifecycleService
    from src.app.wms_integration.outbound_picking.services import (
        BinBatchScheduler,
        ReturnBatchOwnerService,
    )
    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

    flow = ManualPickingDrainFlow(
        DrainRepository(executions=repository),
        ReturnRepository(),
        None,
        None,
        BinBatchScheduler(WmsConfirmationLifecycleService(workline_owner=ReturnBatchOwnerService())),
        BinBatchResultReader(),
        drain_executions=repository,
    )
    return await flow.return_in_session(
        db, line, decision, rack_id, line.position_bindings["OUTLET"]["location_id"], timezone.now_for_db()
    )


async def return_confirmation(db, line):
    from src.app.wms_adapter.outbound_picking.return_batch_wire import BIN_RETURN_BATCH_OPERATION

    return await db.scalar(
        select(WmsConfirmation)
        .where(WmsConfirmation.workline_id == line.id, WmsConfirmation.operation == BIN_RETURN_BATCH_OPERATION)
        .order_by(WmsConfirmation.id.desc())
    )


async def seed_return_response(db, line, confirmation, *, no_batch=False):
    now = timezone.now_for_db()
    data = (
        {"result": "NO_BATCH", "retry_after_ms": 1000}
        if no_batch
        else {
            "result": "READY",
            "moves": [
                {
                    "sequence_no": candidate["sequence_no"],
                    "bin_code": candidate["bin_code"],
                    "target": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": confirmation.request_payload["data"]["rack_id"],
                        "rack_face": confirmation.request_payload["data"]["rack_face"],
                        "slot_id": f"S{candidate['sequence_no']}",
                    },
                }
                for candidate in confirmation.request_payload["data"]["return_candidates"]
            ],
        }
    )
    payload = {"operation_id": confirmation.operation_id, "code": "DECIDED", "timestamp": 2, "data": data}
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{confirmation.operation}:{confirmation.operation_id}",
        operation=confirmation.operation,
        operation_id=confirmation.operation_id,
        workline_id=line.id,
        normalized_payload=payload,
        payload_digest=canonical_json_digest(payload),
        received_at=now,
        processed_at=now,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    db.add(evidence)
    await db.flush()
    if no_batch:
        evidence.decision_digest = canonical_json_digest(
            {"evidence_id": evidence.id, "role": "WMS_BATCH", "result": "RETURN_NO_BATCH"}
        )
        evidence.published_at = now
    confirmation.status = WmsConfirmationStatus.COMPLETED
    confirmation.response_result = data["result"]
    confirmation.response_evidence_id = evidence.id
    confirmation.completed_at = now
    await db.flush()
    return evidence


async def test_cross_face_requests_serialize_original_members(rack_database):
    from manual_picking.application.bin_line.return_model import BinLineReturn
    from test_rack_cycle_postgresql import serialized_pair

    _, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        line, _ = await seed_line(db, bins=2)
        decision = await seed_ready(db, line, racks=(RackFaceSequence("R1", ("90",)), RackFaceSequence("R2", ("270",))))
        execution = await repository.ensure_ready_in_session(db, decision=decision)
        original_ids = tuple(
            (
                await db.scalars(
                    select(BinLineReturn.id)
                    .where(BinLineReturn.workline_id == line.id)
                    .order_by(BinLineReturn.scan4_event_time)
                )
            ).all()
        )
    calls = []

    async def request(db, locked):
        rack_id = "R1" if not calls else "R2"
        calls.append(rack_id)
        return await request_return(db, locked, decision, repository, rack_id=rack_id)

    assert await serialized_pair(sessions, line.id, request) == (True, False)
    async with sessions() as db:
        confirmation = await return_confirmation(db, line)
        members = await repository.members_for_return_batch(
            db, workline_id=line.id, operation_id=confirmation.operation_id
        )
        assert tuple(member.return_id for member in members) == original_ids
        assert {member.execution_id for member in members} == {execution.id}
        assert (
            len(
                (
                    await db.scalars(
                        select(WmsConfirmation).where(
                            WmsConfirmation.workline_id == line.id, WmsConfirmation.operation == confirmation.operation
                        )
                    )
                ).all()
            )
            == 1
        )


async def test_request_and_membership_rollback_together(rack_database):
    _, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
        decision = await seed_ready(db, line)
        execution = await repository.ensure_ready_in_session(db, decision=decision)
    with pytest.raises(RuntimeError, match="abort request"):
        async with sessions.begin() as db:
            locked = await locked_line(db, line.id)
            assert await request_return(db, locked, decision, repository)
            confirmation = await return_confirmation(db, line)
            assert await repository.members_for_return_batch(
                db, workline_id=line.id, operation_id=confirmation.operation_id
            )
            raise RuntimeError("abort request")
    async with sessions() as db:
        assert await return_confirmation(db, line) is None
        assert not (
            await db.scalars(
                select(ManualPickingDrainReturnMember).where(
                    ManualPickingDrainReturnMember.execution_id == execution.id
                )
            )
        ).all()
    async with sessions.begin() as db:
        assert await request_return(db, await locked_line(db, line.id), decision, repository)


async def test_active_no_batch_releases_request_for_next_face(rack_database):
    _, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
        decision = await seed_ready(db, line, racks=(RackFaceSequence("R1", ("90", "270")),))
        await repository.ensure_ready_in_session(db, decision=decision)
        assert await request_return(db, line, decision, repository)
        first = await return_confirmation(db, line)
        original_members = await repository.members_for_return_batch(
            db, workline_id=line.id, operation_id=first.operation_id
        )
    async with sessions.begin() as db:
        locked = await locked_line(db, line.id)
        assert not await request_return(db, locked, decision, repository)
        first = await return_confirmation(db, line)
        first.status = WmsConfirmationStatus.RECONCILING
    async with sessions.begin() as db:
        locked = await locked_line(db, line.id)
        assert not await request_return(db, locked, decision, repository)
        first = await return_confirmation(db, line)
        await seed_return_response(db, line, first, no_batch=True)
    async with sessions.begin() as db:
        locked = await locked_line(db, line.id)
        assert await request_return(db, locked, decision, repository)
        second = await return_confirmation(db, line)
        assert second.operation_id != first.operation_id
        assert second.request_payload["data"]["rack_face"] == "270"
        members = await repository.members_for_return_batch(db, workline_id=line.id, operation_id=second.operation_id)
        assert tuple(member.return_id for member in members) == tuple(member.return_id for member in original_members)


async def test_retirement_and_ready_response_serialize(rack_database):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from manual_picking.application.batch_result import ManualPickingBatchResultFlow
    from manual_picking.application.bin_line.return_repository import ReturnRepository
    from manual_picking.application.scan_flow import ManualPickingScanFlow
    from test_rack_cycle_postgresql import serialized_pair

    from src.app.execution.plugin_binding import BusinessEvidenceDisposition
    from src.app.wms_integration.outbound_picking.services.bin_batch import BinBatchResultReader

    _, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        line, task = await seed_line(db)
        decision = await seed_ready(db, line)
        execution = await repository.ensure_ready_in_session(db, decision=decision)
        blocker = await seed_rack_transport(db, line, decision)
        assert await request_return(db, line, decision, repository)
        confirmation = await return_confirmation(db, line)
        evidence = await seed_return_response(db, line, confirmation)
    transport = SimpleNamespace(create=AsyncMock())
    reader = BinBatchResultReader()
    flow = ManualPickingScanFlow(
        transport_service=SimpleNamespace(confirm_bin_arrival_in_session=AsyncMock()),
        commands=None,
        admissions=None,
        batch_reader=reader,
        batch_result=ManualPickingBatchResultFlow(reader, transport, ReturnRepository(), drain_executions=repository),
        drain_repository=DrainRepository(executions=repository),
        drain_executions=repository,
    )
    calls = []

    async def retire_or_consume(db, locked):
        calls.append(1)
        if len(calls) == 1:
            original = await repository.for_evidence(db, workline_id=line.id, evidence_id=decision.evidence_id)
            return await repository.close_in_session(
                db,
                original,
                status="EXCEPTION_RETIRED",
                closed_at=timezone.now_for_db(),
                reason_code="DRAIN_RESULT_UNKNOWN",
                blocker_client_request_id=blocker,
                trigger_picking_task_id=task.id,
            )
        return (await flow.apply_in_session(db, evidence.id, workline_id=line.id)).disposition

    assert await serialized_pair(sessions, line.id, retire_or_consume) == (True, BusinessEvidenceDisposition.APPLIED)
    async with sessions.begin() as db:
        assert (
            await flow.apply_in_session(db, evidence.id, workline_id=line.id)
        ).disposition is BusinessEvidenceDisposition.APPLIED
        original = await db.get(ManualPickingDrainExecution, execution.id)
        assert original.status == "EXCEPTION_RETIRED"
        assert await db.get(InboundEvidence, evidence.id) is not None
        members = await repository.members_for_return_batch(
            db, workline_id=line.id, operation_id=confirmation.operation_id
        )
        rows = await ReturnRepository().by_ids_for_update(
            db, workline_id=line.id, return_ids=tuple(member.return_id for member in members)
        )
        assert all(row.return_state == "READY" and row.return_batch_evidence_id is None for row in rows)
    transport.create.assert_not_awaited()


async def test_purgeable_drain_history_is_not_runtime_dependency(rack_database, record_property):
    from manual_picking.application.bin_line.return_model import BinLineReturn
    from rack_cycle_support import BusinessServer, activate, runtime_for, seed_task
    from sqlalchemy import delete
    from test_business_loop import _complete_drain_through_real_worker, _prepare_without_wakeup

    from src.app.device.contracts import EcsDeviceEvent
    from src.app.device.models.command import DeviceCommand

    _, sessions = rack_database
    line, closed, old_returns, departure = await _complete_drain_through_real_worker(
        rack_database, "RECEIVED", use_scan_codes=True
    )
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        assert (await db.get(ManualPickingDrainExecution, closed.id)).status == "COMPLETED"
        assert (await db.get(TransportTask, departure.id)).status == "SUCCEEDED"
        # 此对照仍有未交付义务和原离场物理依赖，不能随闭合历史删除。
        guard_line, _ = await seed_line(db)
        guard_decision = await seed_ready(db, guard_line)
        guard_execution = await repository.ensure_ready_in_session(db, decision=guard_decision)
        guard_departure = await seed_rack_transport(db, guard_line, guard_decision, departure=True)
        assert await request_return(db, guard_line, guard_decision, repository)
        guard_obligation = await return_confirmation(db, guard_line)
        assert guard_obligation.status == "PENDING"
        purge_ids = tuple(
            (
                await db.scalars(
                    select(WmsConfirmation.id).where(
                        WmsConfirmation.workline_id == line.id,
                        WmsConfirmation.status == "COMPLETED",
                    )
                )
            ).all()
        )
        assert len(purge_ids) == 3
        await db.execute(
            delete(ManualPickingDrainReturnMember).where(ManualPickingDrainReturnMember.execution_id == closed.id)
        )
        await db.execute(delete(ManualPickingDrainExecution).where(ManualPickingDrainExecution.id == closed.id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.id.in_(purge_ids)))
        assert await DrainRepository().current(db, line.id) is None
        queued = await seed_task(db, line, queued=True)
        current_line = await locked_line(db, line.id)
        scan3_device = current_line.config["device_bindings"]["SCAN3"]
        current_line.device_contracts = {
            scan3_device: {
                "device_id": 1,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "third_party_integration",
                "contract_version": "1.1",
                "status_max_age_ms": 30000,
                "command_timeout_ms": 30000,
            }
        }
        event = EcsDeviceEvent(
            device_code=scan3_device,
            contract_key="third_party_integration",
            contract_version="1.1",
            event_type="SCAN_COMPLETED",
            timestamp=int(timezone.now_utc().timestamp() * 1000),
            source_event_id=f"SCAN3:{new_uuid7()}",
            is_debug=False,
            data={"bin_code": f"{old_returns[0].bin_code}-B"},
        )
        payload = event.model_dump(mode="json", exclude_unset=True)
        fresh_scan = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=event.source_event_id,
            device_code=scan3_device,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            received_at=timezone.now_for_db(),
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            workline_id=line.id,
        )
        db.add(fresh_scan)
        await db.flush()
        fresh_scan_id = fresh_scan.id
    server = BusinessServer()
    server.start()
    try:
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            await _prepare_without_wakeup(runtime, sessions, line.id)
            await runtime.execution.fact_processor.process_batch(100)
            await activate(runtime, line.id)
        # 重装配后重放持久事实；历史清理前的对象不能重新成为 current。
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            await runtime.execution.fact_processor.process_batch(100)
            await activate(runtime, line.id)
            async with sessions() as db:
                assert await DrainRepository().current(db, line.id) is None
                assert await db.get(ManualPickingDrainExecution, closed.id) is None
                assert not (await db.scalars(select(WmsConfirmation.id).where(WmsConfirmation.id.in_(purge_ids)))).all()
                same_bin = (
                    await db.scalars(
                        select(BinLineReturn).where(
                            BinLineReturn.workline_id == line.id,
                            BinLineReturn.bin_code == old_returns[0].bin_code,
                        )
                    )
                ).all()
                assert len(same_bin) == 2
                fresh = next(row for row in same_bin if row.id != old_returns[0].id)
                assert fresh.scan3_evidence_id == fresh_scan_id and fresh.return_state == "NONE"
                assert fresh.scan4_evidence_id is None and fresh.return_batch_evidence_id is None
                assert fresh.scan3_command_code is not None
                assert (await db.get(type(queued), queued.id)).status == "PREPARING"
                command = await db.scalar(
                    select(DeviceCommand).where(DeviceCommand.command_code == fresh.scan3_command_code)
                )
                assert command.execution_ref_id == f"manual-picking:{fresh_scan_id}:SCAN3"
                assert command.status == "PENDING"
                evidence = await db.get(InboundEvidence, fresh_scan_id)
                assert evidence.published_at is not None and evidence.decision_digest is not None
                assert await db.get(ManualPickingDrainExecution, guard_execution.id) is not None
                saved = await db.get(WmsConfirmation, guard_obligation.id)
                assert saved.status == "PENDING" and saved.request_payload == guard_obligation.request_payload
                assert saved.operation_id == guard_obligation.operation_id
                members = await repository.members_for_return_batch(
                    db, workline_id=guard_line.id, operation_id=guard_obligation.operation_id
                )
                assert len(members) == 1
                original_departure = await db.scalar(
                    select(TransportTask).where(TransportTask.client_request_id == guard_departure)
                )
                assert original_departure.status == "ACCEPTED"
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.client_request_id == guard_departure
                    )
                )
                assert binding.source_evidence_id == guard_decision.evidence_id
                assert await db.get(InboundEvidence, guard_decision.evidence_id) is not None
                record_property(
                    "drain_acceptance",
                    json.dumps(
                        {
                            "purged_execution_id": closed.id,
                            "purged_drain_operation_id": closed.drain_operation_id,
                            "purged_confirmation_ids": purge_ids,
                            "original_return_ids": [row.id for row in old_returns],
                            "completed_departure_id": departure.transport_task_id,
                            "new_task_id": queued.id,
                            "new_return_id": fresh.id,
                            "bin_code": fresh.bin_code,
                            "new_scan_evidence_id": fresh_scan_id,
                            "new_command_code": command.command_code,
                            "retained_execution_id": guard_execution.id,
                            "retained_return_operation_id": guard_obligation.operation_id,
                            "retained_departure_client_id": guard_departure,
                            "retained_evidence_id": guard_decision.evidence_id,
                        },
                        sort_keys=True,
                    ),
                )
    finally:
        server.close()


async def test_nonempty_downgrade_refuses_and_preserves_frozen_runtime_identity(rack_database):
    import subprocess

    url, sessions = rack_database
    repository = DrainExecutionRepository()
    async with sessions.begin() as db:
        starting_revision = await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version"))
        line, task = await seed_line(db)
        decision = await seed_ready(db, line)
        execution = await repository.ensure_ready_in_session(db, decision=decision)
        blocker = await seed_rack_transport(db, line, decision)
        assert await request_return(db, line, decision, repository)
        confirmation = await return_confirmation(db, line)
        members = await repository.members_for_return_batch(
            db, workline_id=line.id, operation_id=confirmation.operation_id
        )
        frozen_members = tuple(
            (member.id, member.execution_id, member.return_batch_confirmation_id, member.return_id, member.sequence_no)
            for member in members
        )
        assert frozen_members
        assert await repository.close_in_session(
            db,
            execution,
            status="EXCEPTION_RETIRED",
            closed_at=timezone.now_for_db(),
            reason_code="DRAIN_RESULT_UNKNOWN",
            blocker_client_request_id=blocker,
            trigger_picking_task_id=task.id,
        )
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_alembic("downgrade", PARENT_REVISION, database_url=url)
    assert "downgrade would lose frozen runtime identity" in failure.value.stderr
    async with sessions() as db:
        assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == starting_revision
        preserved = await db.get(ManualPickingDrainExecution, execution.id)
        assert preserved.status == "EXCEPTION_RETIRED"
        assert preserved.drain_operation_id == decision.intent.operation_id
        assert preserved.decision_evidence_id == decision.evidence_id
        assert preserved.blocker_client_request_id == blocker
        assert preserved.trigger_picking_task_id == task.id
        current_members = await repository.members_for_return_batch(
            db, workline_id=line.id, operation_id=confirmation.operation_id
        )
        assert (
            tuple(
                (
                    member.id,
                    member.execution_id,
                    member.return_batch_confirmation_id,
                    member.return_id,
                    member.sequence_no,
                )
                for member in current_members
            )
            == frozen_members
        )
        assert (await db.get(WmsConfirmation, confirmation.id)).operation_id == confirmation.operation_id
        assert (
            await db.scalar(select(TransportTask.client_request_id).where(TransportTask.client_request_id == blocker))
            == blocker
        )


async def test_manual_withdrawal_migration_preserves_original_ready_and_blocks_downgrade(integration_guard):
    import subprocess

    async with temporary_database() as (_, url):
        run_alembic("upgrade", "e3fec1d9ffae", database_url=url)
        run_alembic("upgrade", "f6d0319c8a42", database_url=url)
        run_alembic("downgrade", "e3fec1d9ffae", database_url=url)
        run_alembic("upgrade", "f6d0319c8a42", database_url=url)
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as db:
                line, task = await seed_line(db, bins=0)
                decision = await seed_ready(db, line)
                blocker = await seed_rack_transport(db, line, decision)
                repository = DrainExecutionRepository()
                execution = await repository.ensure_ready_in_session(db, decision=decision)
                assert await repository.close_in_session(
                    db,
                    execution,
                    status="MANUAL_WITHDRAWN",
                    closed_at=timezone.now_for_db(),
                    reason_code="MANUAL_SCOPE_EXIT",
                    blocker_client_request_id=blocker,
                    trigger_picking_task_id=task.id,
                )
                assert await repository.active_for_workline(db, line.id) is None
                assert await DrainRepository().current(db, line.id) is None
                assert await DrainRepository().retired_clearance_racks(db, workline_id=line.id) == []
                assert (await repository.ensure_ready_in_session(db, decision=decision)).status == "MANUAL_WITHDRAWN"
                original = await db.scalar(select(TransportTask).where(TransportTask.client_request_id == blocker))
                assert original.status == "PENDING" and original.outcome_version == 0
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(
                            text("UPDATE wes_biz.manual_picking_drain_executions SET reason_code=NULL WHERE id=:id"),
                            {"id": execution.id},
                        )
            with pytest.raises(subprocess.CalledProcessError) as failure:
                run_alembic("downgrade", "e3fec1d9ffae", database_url=url)
            assert "manual drain withdrawals exist" in failure.value.stderr
            async with sessions.begin() as db:
                assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == "f6d0319c8a42"
        finally:
            await engine.dispose()
