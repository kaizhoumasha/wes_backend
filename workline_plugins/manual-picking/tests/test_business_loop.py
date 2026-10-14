"""显式 PostgreSQL/Redis/Celery 业务闭环；外部 WMS/ECS 使用测试边界。"""

import json
import os
import signal
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from manual_picking.application.batch_driver import SOURCE_RACK_OUT_STEP
from manual_picking.application.batch_progress_model import ManualPickingInboundBatch
from manual_picking.application.bin_line.return_model import BinLineReturn
from manual_picking.application.drain_execution_model import ManualPickingDrainExecution, ManualPickingDrainReturnMember
from manual_picking.application.drain_repository import DRAIN_RACK_IN_STEP, DRAIN_RACK_OUT_STEP, DrainRepository
from manual_picking.application.passage_model import ManualPickingPassage
from rack_cycle_support import BusinessServer, locked_line, rack_database, runtime_for, seed_line, seed_task
from sqlalchemy import select

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, EcsDeviceEvent
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.models import TransportEvidence, TransportMember, TransportTask
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION, validate_callback_envelope
from src.app.wms_integration.outbound_picking.composition import build_outbound_picking_runtime
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareCoordinator
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support.transport_broker import TransportBrokerWorker, close_transport_test_resources
from tests.support.transport_callbacks import record_valid_callback

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ACTIVATE = "src.celery_app.tasks.picking_task_plan.activate_picking_task_plans_batch"
PREPARE = "src.celery_app.tasks.picking_task_prepare.prepare_picking_tasks_batch"
DISPATCH = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
EXECUTE = "src.celery_app.tasks.execution.process_execution_facts_batch"
SUBMIT = "src.celery_app.tasks.transport.submit_transport_tasks_batch"
APPLY = "src.celery_app.tasks.transport.process_transport_evidence_batch"
PUBLISH = "src.celery_app.tasks.transport.publish_transport_outcomes_batch"


def run(worker, name):
    return worker.result(worker.send(name))


async def test_scan2_deferred_evidence_is_reclaimed_after_old_command_closes(rack_database, monkeypatch):
    database_url, sessions = rack_database
    now = timezone.now_for_db()
    async with sessions.begin() as db:
        line, task = await seed_line(db, bins=0)
        task.status = PickingTaskStatus.EXECUTING
        # 本例验证设备等待；SCAN2 的当前有效批次准入仍须成立。
        operation_id = new_uuid7()
        batch_evidence = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"outbound.bin.inbound_batch@v1:{operation_id}",
            operation="outbound.bin.inbound_batch@v1",
            operation_id=operation_id,
            workline_id=line.id,
            payload_digest="a" * 64,
            normalized_payload={
                "operation_id": operation_id,
                "code": "DECIDED",
                "timestamp": 1,
                "data": {
                    "result": "READY",
                    "bins": [
                        {
                            "bin_code": "A000000001",
                            "source_locator": {
                                "type": "RACK_BIN_SLOT",
                                "rack_id": "R1",
                                "rack_face": "90",
                                "slot_id": "S1",
                            },
                        }
                    ],
                },
            },
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest="b" * 64,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add(batch_evidence)
        await db.flush()
        db.add_all(
            [
                PickingTaskBinSourceRack(
                    picking_task_id=task.id,
                    plan_revision=1,
                    rack_id="R1",
                    rack_face="90",
                    source_evidence_id=task.issued_evidence_id,
                ),
                ManualPickingInboundBatch(
                    workline_id=line.id,
                    picking_task_id=task.id,
                    task_id=task.task_id,
                    operation_id=operation_id,
                    plan_revision=1,
                    rack_id="R1",
                    rack_face="90",
                    response_evidence_id=batch_evidence.id,
                ),
            ]
        )
        line.device_contracts = {
            line.config["device_bindings"]["SCAN2"]: {
                "device_id": 1,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "third_party_integration",
                "contract_version": "1.1",
                "status_max_age_ms": 30000,
                "command_timeout_ms": 30000,
            }
        }
        prior = []
        for role in ("SCAN1",):
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"{role}:{new_uuid7()}",
                device_code=line.config["device_bindings"][role],
                payload_digest="a" * 64,
                normalized_payload={},
                received_at=now,
                processed_at=now,
                published_at=now,
                decision_digest="b" * 64,
                apply_status=InboundEvidenceApplyStatus.APPLIED,
                workline_id=line.id,
            )
            db.add(evidence)
            await db.flush()
            prior.append(evidence)
        for role, status in (("SCAN1", CommandStatus.SUCCEEDED), ("SCAN2", CommandStatus.ACKNOWLEDGED)):
            db.add(
                DeviceCommand(
                    command_code=f"{role}-{new_uuid7()}",
                    device_code=line.config["device_bindings"][role],
                    workline_id=line.id,
                    execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
                    execution_ref_id=f"manual-picking:{new_uuid7()}:{role}",
                    contract_key="third_party_integration",
                    contract_version="1.1",
                    task_type="MOVE_FORWARD",
                    payload_digest="c" * 64,
                    deadline_at=now + timedelta(minutes=5),
                    endpoint_base_url="http://localhost",
                    command_timeout_ms=30000,
                    status_max_age_ms=30000,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.flush()
        commands = (await db.scalars(select(DeviceCommand).where(DeviceCommand.workline_id == line.id))).all()
        scan1_command = next(
            command for command in commands if command.device_code == line.config["device_bindings"]["SCAN1"]
        )
        old_scan2_command = next(
            command for command in commands if command.device_code == line.config["device_bindings"]["SCAN2"]
        )
        db.add(
            ManualPickingPassage(
                workline_id=line.id,
                task_id=task.task_id,
                bin_code="A000000001",
                scan1_evidence_id=prior[0].id,
                scan1_received_at=now,
                scan1_command_code=scan1_command.command_code,
                disposition="OPEN",
            )
        )
        payload = EcsDeviceEvent(
            device_code=line.config["device_bindings"]["SCAN2"],
            contract_key="third_party_integration",
            contract_version="1.1",
            event_type="SCAN_COMPLETED",
            timestamp=int(timezone.now_utc().timestamp() * 1000),
            source_event_id=f"SCAN2:{new_uuid7()}",
            is_debug=False,
            data={"bin_code": "A000000001-C"},
        )
        incoming = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=payload.source_event_id,
            device_code=payload.device_code,
            payload_digest="d" * 64,
            normalized_payload=payload.model_dump(mode="json", exclude_unset=True),
            received_at=now,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            workline_id=line.id,
        )
        db.add(incoming)
        await db.flush()
        evidence_id, line_id, old_command_id = incoming.id, line.id, old_scan2_command.id

    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", '["manual-picking"]')
    worker = TransportBrokerWorker(database_url, os.environ["INTEGRATION_REDIS_URL"], "http://127.0.0.1:1")
    success = False
    try:
        worker.start()
        first_count = run(worker, EXECUTE)
        assert first_count == 1
        async with sessions() as db:
            waiting = await db.get(InboundEvidence, evidence_id)
            assert waiting.decision_next_attempt_at is not None
            assert waiting.published_at is None
        async with sessions.begin() as db:
            old_command = await db.get(DeviceCommand, old_command_id)
            old_command.status = CommandStatus.SUCCEEDED
            waiting = await db.get(InboundEvidence, evidence_id)
            waiting.decision_next_attempt_at = timezone.now_for_db() - timedelta(seconds=1)
        second_count = run(worker, EXECUTE)
        async with sessions() as db:
            completed = await db.get(InboundEvidence, evidence_id)
            passage = await db.scalar(select(ManualPickingPassage).where(ManualPickingPassage.workline_id == line_id))
            assert second_count == 1, (
                completed.apply_status,
                completed.published_at,
                completed.decision_next_attempt_at,
                completed.decision_claim_token,
            )
            assert completed.published_at is not None
            assert passage.scan2_evidence_id == evidence_id
        success = True
    finally:
        worker.close(success=success)


async def test_scan4_first_arrival_survives_deferred_worker_reclaim(rack_database, monkeypatch):
    database_url, sessions = rack_database
    now = timezone.now_for_db()
    async with sessions.begin() as db:
        line, task = await seed_line(db, bins=0)
        task.status = PickingTaskStatus.EXECUTING
        scan4_device = line.config["device_bindings"]["SCAN4"]
        line.device_contracts = {
            scan4_device: {
                "device_id": 1,
                "endpoint_base_url": "http://127.0.0.1:1",
                "contract_key": "third_party_integration",
                "contract_version": "1.1",
                "status_max_age_ms": 30000,
                "command_timeout_ms": 30000,
            }
        }
        prior = []
        for role in ("SCAN1", "SCAN2", "SCAN3"):
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"{role}:{new_uuid7()}",
                device_code=line.config["device_bindings"][role],
                payload_digest="a" * 64,
                normalized_payload={"timestamp": int(timezone.to_utc(now).timestamp() * 1000)},
                received_at=now,
                processed_at=now,
                published_at=now,
                decision_digest="b" * 64,
                apply_status=InboundEvidenceApplyStatus.APPLIED,
                workline_id=line.id,
            )
            db.add(evidence)
            await db.flush()
            prior.append(evidence)
        prior_command = DeviceCommand(
            command_code=f"SCAN3-{new_uuid7()}",
            device_code=line.config["device_bindings"]["SCAN3"],
            workline_id=line.id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=f"manual-picking:{prior[2].id}:SCAN3",
            contract_key="third_party_integration",
            contract_version="1.1",
            task_type="MOVE_FORWARD",
            payload_digest="c" * 64,
            deadline_at=now + timedelta(minutes=5),
            endpoint_base_url="http://localhost",
            command_timeout_ms=30000,
            status_max_age_ms=30000,
            status=CommandStatus.ACKNOWLEDGED,
            created_at=now,
            updated_at=now,
        )
        db.add(prior_command)
        db.add(
            ManualPickingPassage(
                workline_id=line.id,
                task_id=task.task_id,
                bin_code="A000000001",
                scan1_evidence_id=prior[0].id,
                scan1_received_at=now,
                scan2_evidence_id=prior[1].id,
                scan3_evidence_id=prior[2].id,
                disposition="CLOSED",
            )
        )
        db.add(
            BinLineReturn(
                workline_id=line.id,
                bin_code="A000000001",
                scan3_evidence_id=prior[2].id,
                scan3_command_code=prior_command.command_code,
            )
        )
        incoming_event = EcsDeviceEvent(
            device_code=scan4_device,
            contract_key="third_party_integration",
            contract_version="1.1",
            event_type="SCAN_COMPLETED",
            timestamp=int(timezone.now_utc().timestamp() * 1000),
            source_event_id=f"SCAN4:{new_uuid7()}",
            is_debug=False,
            data={"bin_code": "A000000001-B"},
        )
        incoming = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=incoming_event.source_event_id,
            device_code=scan4_device,
            payload_digest="d" * 64,
            normalized_payload=incoming_event.model_dump(mode="json", exclude_unset=True),
            received_at=now,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            workline_id=line.id,
        )
        db.add(incoming)
        await db.flush()
        evidence_id, line_id, command_id = incoming.id, line.id, prior_command.id

    monkeypatch.setenv("ENABLED_WORKLINE_PLUGINS", '["manual-picking"]')
    worker = TransportBrokerWorker(database_url, os.environ["INTEGRATION_REDIS_URL"], "http://127.0.0.1:1")
    success = False
    try:
        worker.start()
        first_count = run(worker, EXECUTE)
        assert first_count == 1
        async with sessions() as db:
            waiting = await db.get(InboundEvidence, evidence_id)
            row = await db.scalar(select(BinLineReturn).where(BinLineReturn.workline_id == line_id))
            assert waiting.decision_next_attempt_at is not None and waiting.published_at is None
            assert row.scan4_evidence_id == evidence_id and row.scan4_event_time == incoming_event.timestamp
            assert row.scan4_command_code is None
        async with sessions.begin() as db:
            command = await db.get(DeviceCommand, command_id)
            command.status = CommandStatus.SUCCEEDED
            waiting = await db.get(InboundEvidence, evidence_id)
            waiting.decision_next_attempt_at = timezone.now_for_db() - timedelta(seconds=1)
        second_count = run(worker, EXECUTE)
        assert second_count == 1
        async with sessions() as db:
            completed = await db.get(InboundEvidence, evidence_id)
            row = await db.scalar(select(BinLineReturn).where(BinLineReturn.workline_id == line_id))
            assert completed.published_at is not None
            assert row.scan4_event_time == incoming_event.timestamp and row.scan4_evidence_id == evidence_id
            assert row.scan4_command_code is not None and row.return_state == "MOVE_PENDING"
            commands = (
                await db.scalars(
                    select(DeviceCommand).where(
                        DeviceCommand.workline_id == line_id,
                        DeviceCommand.device_code == scan4_device,
                    )
                )
            ).all()
            assert len(commands) == 1
        success = True
    finally:
        worker.close(success=success)


@asynccontextmanager
async def worker_for(database, server):
    database_url, sessions = database
    worker = TransportBrokerWorker(database_url, os.environ["INTEGRATION_REDIS_URL"], server.url)
    success, primary_error = False, None

    async def cleanup_database():
        pass  # rack_database fixture 销毁独占逻辑库，必须晚于 worker 退出。

    async with runtime_for(sessions, server.url) as (runtime, transport):
        try:
            previous_plugins = os.environ.get("ENABLED_WORKLINE_PLUGINS")
            os.environ["ENABLED_WORKLINE_PLUGINS"] = '["manual-picking"]'
            server.start()
            worker.start()
            yield worker, runtime, transport
            success = True
        except BaseException as exc:
            primary_error = exc
        finally:
            if previous_plugins is None:
                os.environ.pop("ENABLED_WORKLINE_PLUGINS", None)
            else:
                os.environ["ENABLED_WORKLINE_PLUGINS"] = previous_plugins
            await close_transport_test_resources(
                worker=worker,
                runtime=None,
                server=server,
                cleanup_database=cleanup_database,
                success=success,
                primary_error=primary_error,
            )


async def bound_transport(sessions, line_id, step):
    async with sessions() as db:
        rows = (
            (
                await db.execute(
                    select(TransportTask)
                    .join(
                        TransportDecisionBinding,
                        TransportTask.client_request_id == TransportDecisionBinding.client_request_id,
                    )
                    .where(TransportDecisionBinding.workline_id == line_id, TransportDecisionBinding.step == step)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != 1:
            confirmations = (await db.scalars(select(WmsConfirmation))).all()
            evidences = (await db.scalars(select(InboundEvidence))).all()
            pytest.fail(
                f"{step}: {len(rows)} bindings; confirmations="
                f"{[(r.operation, r.status, r.response_result) for r in confirmations]}; evidence="
                f"{[(r.kind, r.operation, r.apply_status, r.processed_at, r.published_at, r.decision_attempt_count, r.decision_next_attempt_at) for r in evidences]}"
            )
        return rows[0]


async def callback(service, task, payload, *, operation_id=None):
    identity = operation_id or new_uuid7()
    validate_callback_envelope(
        {
            "operation_id": identity,
            "operation": RESULT_OPERATION,
            "timestamp": 1,
            "data": {**payload, "transport_task_id": task.transport_task_id},
        }
    )
    ack = await record_valid_callback(
        service,
        operation_id=identity,
        transport_task_id=task.transport_task_id,
        operation=RESULT_OPERATION,
        timestamp=1,
        payload=payload,
    )
    assert (ack["http_status"], ack["code"]) == ((200, "DUPLICATE") if operation_id else (202, "RECEIVED"))
    return identity


@pytest.mark.parametrize("transport_code", ["RECEIVED", "DUPLICATE"])
async def test_completed_task_drains_fifo_through_real_worker(rack_database, transport_code):
    await _complete_drain_through_real_worker(rack_database, transport_code)


async def _complete_drain_through_real_worker(rack_database, transport_code, *, use_scan_codes=False):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _task = await seed_line(db, bins=2)
        if use_scan_codes:
            rows = (await db.scalars(select(BinLineReturn).where(BinLineReturn.workline_id == line.id))).all()
            for index, row in enumerate(rows):
                row.bin_code = f"A{index + 1:09d}"
    server = BusinessServer(transport_code=transport_code)
    async with worker_for(rack_database, server) as (worker, _runtime, transport):
        assert run(worker, ACTIVATE) == 1
        assert run(worker, ACTIVATE) == 0
        assert run(worker, DISPATCH) == 1
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        run(worker, ACTIVATE)
        ingress = await bound_transport(sessions, line.id, DRAIN_RACK_IN_STEP)
        run(worker, SUBMIT)
        ingress = await bound_transport(sessions, line.id, DRAIN_RACK_IN_STEP)
        assert ingress.status == "ACCEPTED" and ingress.result_deadline_at is not None
        assert run(worker, ACTIVATE) == 0
        arrival = {
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": server.drain_rack_id,
            "status": "SUCCEEDED",
            "final_position": {
                "kind": "RACK_POSITION",
                "location_code": line.position_bindings["FIVE_RACK"]["location_id"],
            },
            "arrival_face": "90",
        }
        arrival_id = await callback(transport.service, ingress, arrival)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        async with sessions() as db:
            projection = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK", PositionProjection.object_id == arrival["rack_id"]
                )
            )
            member = await db.scalar(
                select(TransportMember).where(TransportMember.transport_task_id == ingress.transport_task_id)
            )
            assert projection.source_transport_task_id == ingress.transport_task_id
            assert projection.position_json == arrival["final_position"] and not projection.position_unknown
            assert member.status == "SUCCEEDED" and member.arrival_face == "90"
        run(worker, ACTIVATE)
        run(worker, DISPATCH)
        run(worker, EXECUTE)
        returned = await bound_transport(sessions, line.id, "MANUAL_PICKING_RETURN_BATCH")
        async with sessions() as db:
            returns = (
                await db.scalars(
                    select(BinLineReturn)
                    .where(BinLineReturn.workline_id == line.id)
                    .order_by(BinLineReturn.scan4_event_time)
                )
            ).all()
            assert [move["bin_code"] for move in returned.request_json["moves"]] == [row.bin_code for row in returns]
            assert {row.return_state for row in returns} == {"RETURN_REQUESTED"}
        run(worker, SUBMIT)
        assert run(worker, ACTIVATE) >= len(returned.request_json["moves"])
        result = {
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {"container_id": move["bin_code"], "status": "SUCCEEDED", "final_position": move["target"]}
                for move in returned.request_json["moves"]
            ],
        }
        async with sessions() as db:
            returns = (await db.scalars(select(BinLineReturn).where(BinLineReturn.workline_id == line.id))).all()
            assert {row.return_state for row in returns} == {"EXITED"}
            current_transport = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == returned.transport_task_id)
            )
            assert current_transport.status == "ACCEPTED"
        for move in returned.request_json["moves"]:
            message = validate_callback_envelope(
                {
                    "operation_id": new_uuid7(),
                    "operation": POSITION_OPERATION,
                    "timestamp": 1,
                    "data": {
                        "transport_task_id": returned.transport_task_id,
                        "container_id": move["bin_code"],
                        "milestone": "TARGET_PLACED",
                        "final_position": move["target"],
                    },
                }
            )
            ack = await transport.service.record_callback(
                operation_id=message["operation_id"],
                operation=POSITION_OPERATION,
                message=message,
                payload=message["data"],
                rejection_reason_code=None,
            )
            assert ack["http_status"] == 202
        run(worker, APPLY)
        await callback(transport.service, returned, result)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        run(worker, DISPATCH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        departure = await bound_transport(sessions, line.id, DRAIN_RACK_OUT_STEP)
        run(worker, SUBMIT)
        departure = await bound_transport(sessions, line.id, DRAIN_RACK_OUT_STEP)
        # ACK 仅证明 RCS/WMS 已接管离场动作，后续步骤仍等待权威结果。
        assert departure.status == "ACCEPTED" and departure.result_deadline_at is not None
        await callback(
            transport.service,
            departure,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": arrival["rack_id"],
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "WHE0502"},
            },
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        async with sessions() as db:
            execution = await db.scalar(
                select(ManualPickingDrainExecution).where(ManualPickingDrainExecution.workline_id == line.id)
            )
            assert execution.status == "COMPLETED" and execution.closed_at is not None
            assert await DrainRepository().current(db, line.id) is None
            returns = (await db.scalars(select(BinLineReturn).where(BinLineReturn.workline_id == line.id))).all()
            assert {row.return_state for row in returns} == {"EXITED"}
            drain = await db.scalar(
                select(WmsConfirmation).where(
                    WmsConfirmation.workline_id == line.id,
                    WmsConfirmation.operation == "workline.return_buffer.drain_rack_decide@v1",
                )
            )
            assert drain.status == "COMPLETED"
            assert (await db.get(InboundEvidence, drain.response_evidence_id)).published_at is not None
        await callback(transport.service, ingress, arrival, operation_id=arrival_id)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        assert run(worker, ACTIVATE) == 0
        assert run(worker, ACTIVATE) == 0
        assert run(worker, SUBMIT) == 0
        assert (await bound_transport(sessions, line.id, DRAIN_RACK_OUT_STEP)).id == departure.id
        operations = [r["envelope"]["operation"] for r in server.requests]
        assert operations.count("workline.return_buffer.drain_rack_decide@v1") == 1
        assert operations.count("outbound.bin.return_batch@v1") == 1
        assert len([r for r in server.requests if r["path"].endswith("transport-requests")]) == 3
        return line, execution, returns, departure


async def test_real_worker_prepares_next_task_without_drain(rack_database):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
        queued = await seed_task(db, line, queued=True)
    server = BusinessServer()
    async with worker_for(rack_database, server) as (worker, _runtime, _transport):
        assert run(worker, PREPARE) == 1
        run(worker, DISPATCH)
        async with sessions() as db:
            task = await db.get(type(queued), queued.id)
            assert task.status == "PREPARING" and task.workline_id == line.id
            rows = (await db.scalars(select(WmsConfirmation))).all()
            assert len(rows) == 1 and rows[0].operation == "outbound.picking_task.prepare@v1"
            assert rows[0].status == "COMPLETED"
        assert [r["envelope"]["operation"] for r in server.requests] == ["outbound.picking_task.prepare@v1"]


@pytest.mark.parametrize("transport_code", ["RECEIVED", "DUPLICATE"])
@pytest.mark.parametrize("deliver_departure_result", [True, False])
@pytest.mark.parametrize("managed_target", [True, False])
async def test_source_departure_acceptance_refills_one_slot_through_real_worker(
    rack_database, transport_code, deliver_departure_result, managed_target
):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, picking = await seed_line(db, bins=0, capacity=2)
        identity = new_uuid7()
        now = timezone.now_for_db()
        plan = InboundEvidence(
            kind="WMS_EVENT",
            source_identity=f"outbound.picking_task.plan_delta@v1:{identity}",
            operation="outbound.picking_task.plan_delta@v1",
            operation_id=identity,
            payload_digest="d" * 64,
            normalized_payload={"data": {"task_id": picking.task_id}},
            workline_id=line.id,
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest="e" * 64,
            apply_status="APPLIED",
        )
        db.add(plan)
        await db.flush()
        picking.status = "EXECUTING"
        picking.last_applied_plan_revision = 1
        picking.target_rack_id = f"TARGET-{line.id}" if managed_target else None
        picking.target_rack_face = "90" if managed_target else None
        if not managed_target:
            line.position_bindings = {
                key: value for key, value in line.position_bindings.items() if key != "TRANSFER_RACK"
            }
        picking.initial_plan_evidence_id = plan.id
        picking.last_plan_evidence_id = plan.id
        rack_ids = [f"SOURCE-{index}-{line.id}" for index in range(3)]
        for rack_id in rack_ids:
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=picking.id,
                    rack_id=rack_id,
                    rack_face="90",
                    plan_revision=1,
                    source_evidence_id=plan.id,
                )
            )

    async def source_ingresses():
        async with sessions() as db:
            return dict(
                (
                    await db.execute(
                        select(TransportDecisionBinding.resource_fence_id, TransportTask)
                        .join(
                            TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id
                        )
                        .where(
                            TransportDecisionBinding.workline_id == line.id,
                            TransportDecisionBinding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                        )
                    )
                ).all()
            )

    server = BusinessServer(transport_code=transport_code)
    async with worker_for(rack_database, server) as (worker, _runtime, transport):
        run(worker, ACTIVATE)
        run(worker, SUBMIT)
        initial = await source_ingresses()
        assert set(initial) == set(rack_ids[:2])
        assert all(task.status == "ACCEPTED" for task in initial.values())
        assert all(task.request_json["rcs_template_id"] == "CTU01" for task in initial.values())
        run(worker, ACTIVATE)
        assert set(await source_ingresses()) == set(rack_ids[:2])
        arrivals = [(initial[rack_ids[0]], rack_ids[0], "FIVE_RACK")]
        if managed_target:
            target = await bound_transport(sessions, line.id, "PICKING_TASK_TARGET_RACK_IN")
            arrivals.insert(0, (target, picking.target_rack_id, "TRANSFER_RACK"))
        else:
            async with sessions() as db:
                assert (
                    await db.scalar(
                        select(TransportDecisionBinding.id).where(
                            TransportDecisionBinding.workline_id == line.id,
                            TransportDecisionBinding.step == "PICKING_TASK_TARGET_RACK_IN",
                        )
                    )
                    is None
                )
        # ACCEPTED 只证明接纳。通过正式 callback/Evidence 路径提供目标架及首个来源架到位事实。
        for task, rack_id, role in arrivals:
            await callback(
                transport.service,
                task,
                {
                    "kind": "RACK_MOVE",
                    "outcome_revision": 1,
                    "rack_id": rack_id,
                    "status": "SUCCEEDED",
                    "final_position": {
                        "kind": "RACK_POSITION",
                        "location_code": line.position_bindings[role]["location_id"],
                    },
                    "arrival_face": "90",
                },
            )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        run(worker, DISPATCH)  # WMS 权威空面决定 RACK_FACE_DONE，正常业务路径创建 CTU03。
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        run(worker, DISPATCH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        departure = await bound_transport(sessions, line.id, SOURCE_RACK_OUT_STEP)
        run(worker, SUBMIT)
        departure = await bound_transport(sessions, line.id, SOURCE_RACK_OUT_STEP)
        assert departure.status == "ACCEPTED" and departure.result_deadline_at is not None

        run(worker, ACTIVATE)
        run(worker, SUBMIT)
        refilled = await source_ingresses()
        assert set(refilled) == set(rack_ids), "CTU03 ACK must refill exactly one CTU01 through activation"
        assert {rack: task.transport_task_id for rack, task in initial.items()} == {
            rack: refilled[rack].transport_task_id for rack in initial
        }
        assert refilled[rack_ids[2]].status == "ACCEPTED"
        assert refilled[rack_ids[2]].request_json["rcs_template_id"] == "CTU01"
        if not deliver_departure_result:
            await callback(
                transport.service,
                refilled[rack_ids[2]],
                {
                    "kind": "RACK_MOVE",
                    "outcome_revision": 1,
                    "rack_id": rack_ids[2],
                    "status": "SUCCEEDED",
                    "final_position": {
                        "kind": "RACK_POSITION",
                        "location_code": line.position_bindings["FIVE_RACK"]["location_id"],
                    },
                    "arrival_face": "90",
                },
            )
            run(worker, APPLY)
            run(worker, PUBLISH)
            async with sessions() as db:
                old_departure = await db.get(TransportTask, departure.id)
                old_members = (
                    await db.scalars(
                        select(TransportMember).where(TransportMember.transport_task_id == departure.transport_task_id)
                    )
                ).all()
                old_position = await db.scalar(
                    select(PositionProjection).where(
                        PositionProjection.object_type == "RACK", PositionProjection.object_id == rack_ids[0]
                    )
                )
                next_position = await db.scalar(
                    select(PositionProjection).where(
                        PositionProjection.object_type == "RACK", PositionProjection.object_id == rack_ids[2]
                    )
                )
                assert old_departure.status == "ACCEPTED" and old_members
                assert all(member.status == "PENDING" for member in old_members)
                assert old_position.position_unknown is True
                assert next_position.position_unknown is False
                assert next_position.source_transport_task_id == refilled[rack_ids[2]].transport_task_id
            return
        await callback(
            transport.service,
            departure,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": rack_ids[0],
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "WHE0502"},
            },
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, ACTIVATE)
        run(worker, SUBMIT)
        run(worker, ACTIVATE)
        run(worker, ACTIVATE)
        run(worker, SUBMIT)
        assert {rack: task.transport_task_id for rack, task in (await source_ingresses()).items()} == {
            rack: task.transport_task_id for rack, task in refilled.items()
        }
        # 第一架窗口已在 ACK 时释放；成功回调只闭合其物理终态和同架复用围栏。
        departure = await bound_transport(sessions, line.id, SOURCE_RACK_OUT_STEP)
        assert departure.status == "SUCCEEDED"
        assert departure.request_json["rcs_template_id"] == "CTU03"
        assert departure.last_applied_wms_outcome_revision == 1 and departure.outcome_json is not None
        async with sessions() as db:
            members = (
                await db.scalars(
                    select(TransportMember).where(TransportMember.transport_task_id == departure.transport_task_id)
                )
            ).all()
            assert members and all(member.status == "SUCCEEDED" for member in members)
        requests = [request["envelope"] for request in server.requests]
        assert sum(request["operation"] == "outbound.bin.inbound_batch@v1" for request in requests) == 1
        assert sum(request["path"].endswith("transport-requests") for request in server.requests) == (
            5 if managed_target else 4
        )


async def _start_drain(worker, sessions, line):
    run(worker, ACTIVATE)
    run(worker, DISPATCH)
    run(worker, EXECUTE)
    run(worker, ACTIVATE)
    run(worker, ACTIVATE)
    run(worker, SUBMIT)
    async with sessions() as db:
        execution = await db.scalar(
            select(ManualPickingDrainExecution).where(ManualPickingDrainExecution.workline_id == line.id)
        )
        originals = (
            await db.scalars(
                select(TransportTask)
                .join(
                    TransportDecisionBinding,
                    TransportTask.client_request_id == TransportDecisionBinding.client_request_id,
                )
                .where(TransportDecisionBinding.source_evidence_id == execution.decision_evidence_id)
            )
        ).all()
        assert execution.status == "ACTIVE" and originals
        assert all(row.status == "ACCEPTED" and row.result_deadline_at is not None for row in originals)
        return execution, originals


async def _expire_result(sessions, task):
    # 仅推进测试时钟边界；不注入物理状态、结果或成功投影。
    async with sessions.begin() as db:
        original = await db.get(TransportTask, task.id)
        original.result_deadline_at = timezone.now_for_db() - timedelta(seconds=1)


def _assert_worker_tasks(worker, *names):
    returned = {row["task_name"] for row in worker._task_records if row["result"] == "returned"}
    assert set(names) <= returned


@pytest.mark.parametrize("reservation_conflict", [False, True])
async def test_real_worker_retires_overdue_drain_and_prepares_next_task(
    rack_database, reservation_conflict, record_property
):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
    server = BusinessServer(reservation_conflict=reservation_conflict)
    async with worker_for(rack_database, server) as (worker, _runtime, _transport):
        execution, (original,) = await _start_drain(worker, sessions, line)
        async with sessions.begin() as db:
            queued = await seed_task(db, line, queued=True)
        await _expire_result(sessions, original)
        run(worker, ACTIVATE)
        run(worker, PREPARE)
        run(worker, DISPATCH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        async with sessions() as db:
            retired = await db.get(ManualPickingDrainExecution, execution.id)
            assert retired.status == "EXCEPTION_RETIRED"
            assert retired.reason_code == "DRAIN_RESULT_OVERDUE"
            assert retired.blocker_client_request_id == original.client_request_id
            assert retired.trigger_picking_task_id == queued.id
            assert (await db.get(type(queued), queued.id)).status == "PREPARING"
            current = await db.get(TransportTask, original.id)
            assert current.status == "ACCEPTED" and current.submit_request_body == original.submit_request_body
            assert current.submit_operation_id == original.submit_operation_id
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.source_evidence_id == execution.decision_evidence_id
                    )
                )
            ).all()
            assert [row.client_request_id for row in bindings] == [original.client_request_id]
            confirmation = await db.scalar(
                select(WmsConfirmation).where(
                    WmsConfirmation.picking_task_id == queued.id,
                    WmsConfirmation.operation == "outbound.picking_task.prepare@v1",
                )
            )
            assert confirmation.status == ("RECONCILING" if reservation_conflict else "COMPLETED")
            evidence = await db.scalar(
                select(InboundEvidence).where(
                    InboundEvidence.operation == confirmation.operation,
                    InboundEvidence.operation_id == confirmation.operation_id,
                )
            )
            assert evidence.normalized_payload["code"] == ("CONFLICT" if reservation_conflict else "PREPARE_ACCEPTED")
            if reservation_conflict:
                assert evidence.normalized_payload["data"] == {"reason_code": "STATE_CONFLICT"}
            current_drain = await DrainRepository().current(db, line.id)
            assert current_drain is None or current_drain.intent.operation_id != execution.drain_operation_id
            record_property(
                "drain_acceptance",
                json.dumps(
                    {
                        "execution_id": execution.id,
                        "drain_operation_id": execution.drain_operation_id,
                        "status": retired.status,
                        "reason": retired.reason_code,
                        "original_client_id": original.client_request_id,
                        "original_submit_id": original.submit_operation_id,
                        "original_request_digest": original.request_digest,
                        "new_task_id": queued.id,
                        "prepare_operation_id": confirmation.operation_id,
                        "prepare_evidence_id": evidence.id,
                        "prepare_status": confirmation.status,
                        "old_new_successors": 0,
                    },
                    sort_keys=True,
                ),
            )
        _assert_worker_tasks(worker, ACTIVATE, PREPARE, DISPATCH, EXECUTE, SUBMIT)
        requests = [row["envelope"] for row in server.requests]
        assert sum(row["operation"] == "outbound.picking_task.prepare@v1" for row in requests) == 1
        assert sum(row["operation"] == "workline.return_buffer.drain_rack_decide@v1" for row in requests) == 1


async def test_real_worker_preserves_normal_drain_with_independent_task(rack_database, record_property):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db, capacity=2)
    server = BusinessServer()
    async with worker_for(rack_database, server) as (worker, runtime, transport):
        execution, (original,) = await _start_drain(worker, sessions, line)
        async with sessions.begin() as db:
            queued = await seed_task(db, line, queued=True)
        run(worker, PREPARE)
        run(worker, DISPATCH)
        await _accept_source_plan(runtime, sessions, queued, f"INDEPENDENT-{line.id}")
        run(worker, ACTIVATE)
        source = await bound_transport(sessions, line.id, "PICKING_TASK_BIN_SOURCE_RACK_IN")
        run(worker, SUBMIT)
        await callback(
            transport.service,
            source,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": source.request_json["rack_id"],
                "status": "SUCCEEDED",
                "final_position": source.request_json["target"],
                "arrival_face": "90",
            },
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        await callback(
            transport.service,
            original,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": server.drain_rack_id,
                "status": "SUCCEEDED",
                "final_position": original.request_json["target"],
                "arrival_face": "90",
            },
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        async with sessions() as db:
            assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "ACTIVE"
            assert (await db.get(type(queued), queued.id)).status == "EXECUTING"
            current = await db.get(TransportTask, original.id)
            assert current.status == "SUCCEEDED" and current.submit_operation_id == original.submit_operation_id
            assert current.submit_request_body == original.submit_request_body
            assert await DrainRepository().current(db, line.id) is not None
            projection = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK",
                    PositionProjection.object_id == source.request_json["rack_id"],
                )
            )
            assert projection.source_transport_task_id == source.transport_task_id
            assert not projection.position_unknown
            fact = await db.scalar(
                select(InboundEvidence).where(
                    InboundEvidence.transport_task_id == source.transport_task_id,
                    InboundEvidence.kind == InboundEvidenceKind.TRANSPORT_RESULT,
                )
            )
            assert fact.published_at is not None and fact.apply_status == "APPLIED"
            record_property(
                "drain_acceptance",
                json.dumps(
                    {
                        "execution_id": execution.id,
                        "drain_operation_id": execution.drain_operation_id,
                        "status": "ACTIVE",
                        "original_client_id": original.client_request_id,
                        "original_submit_id": original.submit_operation_id,
                        "new_task_id": queued.id,
                        "new_source_id": source.transport_task_id,
                        "new_source_submit_id": source.submit_operation_id,
                        "fact_id": fact.id,
                        "projection_source": projection.source_transport_task_id,
                    },
                    sort_keys=True,
                ),
            )
            assert (
                await db.scalar(
                    select(WmsConfirmation.id).where(
                        WmsConfirmation.operation == "outbound.bin.return_batch@v1",
                        WmsConfirmation.workline_id == line.id,
                    )
                )
                is not None
            )
        _assert_worker_tasks(worker, ACTIVATE, PREPARE, DISPATCH, APPLY, PUBLISH, EXECUTE)


@pytest.mark.parametrize("result_first", [False, True])
async def test_real_worker_consumes_late_ready_without_reallocating_old_members(
    rack_database, result_first, record_property
):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db, bins=2, capacity=2)
    server = BusinessServer(disconnect_return_once=not result_first)
    server.drain_result["racks"].append({"rack_id": "SECOND-RACK", "rack_face": ["90"]})
    async with worker_for(rack_database, server) as (worker, runtime, transport):
        execution, originals, arrived, blocker, arrival_callback_id = await _start_return_request(
            worker, sessions, line, server, transport
        )
        async with sessions.begin() as db:
            queued = await seed_task(db, line, queued=True)
            confirmation = await db.scalar(
                select(WmsConfirmation).where(
                    WmsConfirmation.workline_id == line.id,
                    WmsConfirmation.operation == "outbound.bin.return_batch@v1",
                )
            )
            assert confirmation is not None
            members = (
                await db.scalars(
                    select(ManualPickingDrainReturnMember).where(
                        ManualPickingDrainReturnMember.execution_id == execution.id
                    )
                )
            ).all()
            assert len(members) == 2
            member_id, return_id = members[0].id, members[0].return_id
        if result_first:
            run(worker, DISPATCH)
        await _expire_result(sessions, blocker)
        run(worker, ACTIVATE)
        run(worker, PREPARE)
        run(worker, DISPATCH)
        await _accept_source_plan(runtime, sessions, queued, server.drain_rack_id)
        run(worker, ACTIVATE)
        source = await bound_transport(sessions, line.id, "PICKING_TASK_BIN_SOURCE_RACK_IN")
        assert source.transport_task_id != arrived.transport_task_id
        run(worker, SUBMIT)
        await callback(
            transport.service,
            source,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": server.drain_rack_id,
                "status": "SUCCEEDED",
                "final_position": source.request_json["target"],
                "arrival_face": "90",
            },
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        if not result_first:
            async with sessions.begin() as db:
                pending = await db.get(WmsConfirmation, confirmation.id)
                assert pending.status == "PENDING"
                pending.next_attempt_at = timezone.now_for_db() - timedelta(seconds=1)
            run(worker, DISPATCH)
        run(worker, EXECUTE)
        await callback(
            transport.service,
            arrived,
            {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": server.drain_rack_id,
                "status": "SUCCEEDED",
                "final_position": arrived.request_json["target"],
                "arrival_face": "90",
            },
            operation_id=arrival_callback_id,
        )
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        async with sessions() as db:
            assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "EXCEPTION_RETIRED"
            assert (await db.get(type(queued), queued.id)).status == "EXECUTING"
            saved = await db.get(WmsConfirmation, confirmation.id)
            assert saved.operation_id == confirmation.operation_id
            assert saved.request_payload == confirmation.request_payload and saved.status == "COMPLETED"
            evidence = await db.get(InboundEvidence, saved.response_evidence_id)
            assert evidence.published_at is not None and evidence.decision_digest is not None
            assert (await db.get(ManualPickingDrainReturnMember, member_id)).return_id == return_id
            returned = await db.get(BinLineReturn, return_id)
            assert returned.return_state == "READY" and returned.return_batch_evidence_id is None
            assert not await db.scalar(select(TransportTask.id).where(TransportTask.kind == "BIN_MOVE"))
            assert len((await db.scalars(select(ManualPickingDrainReturnMember))).all()) == 2
            projection = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK",
                    PositionProjection.object_id == server.drain_rack_id,
                )
            )
            assert projection.source_transport_task_id == source.transport_task_id and not projection.position_unknown
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.source_evidence_id == execution.decision_evidence_id,
                    )
                )
            ).all()
            assert {row.client_request_id for row in bindings} == {row.client_request_id for row in originals}
            assert (await db.get(TransportTask, blocker.id)).status == "ACCEPTED"
            record_property(
                "drain_acceptance",
                json.dumps(
                    {
                        "execution_id": execution.id,
                        "drain_operation_id": execution.drain_operation_id,
                        "status": "EXCEPTION_RETIRED",
                        "return_ids": [member.return_id for member in members],
                        "return_operation_id": confirmation.operation_id,
                        "return_evidence_id": evidence.id,
                        "original_client_ids": [row.client_request_id for row in originals],
                        "original_submit_ids": [row.submit_operation_id for row in originals],
                        "new_task_id": queued.id,
                        "new_source_id": source.transport_task_id,
                        "old_callback_id": arrival_callback_id,
                        "projection_source": projection.source_transport_task_id,
                        "old_new_successors": 0,
                    },
                    sort_keys=True,
                ),
            )
        _assert_worker_tasks(worker, ACTIVATE, DISPATCH, EXECUTE, PREPARE, APPLY, PUBLISH)


@pytest.mark.parametrize("pending_result", [False, True])
async def test_restart_and_missing_wakeup_preserve_retired_state_and_obligations(
    rack_database, pending_result, record_property
):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db, bins=2, capacity=2)
    server = BusinessServer(disconnect_return_once=True)
    server.drain_result["racks"].append({"rack_id": "SECOND-RACK", "rack_face": ["90"]})
    async with worker_for(rack_database, server) as (worker, runtime, transport):
        execution, originals, _arrived, original, _arrival_id = await _start_return_request(
            worker, sessions, line, server, transport
        )
        async with sessions.begin() as db:
            queued = await seed_task(db, line, queued=True)
            obligation = await db.scalar(
                select(WmsConfirmation).where(
                    WmsConfirmation.workline_id == line.id,
                    WmsConfirmation.operation == "outbound.bin.return_batch@v1",
                )
            )
            members = (
                await db.scalars(
                    select(ManualPickingDrainReturnMember).where(
                        ManualPickingDrainReturnMember.execution_id == execution.id
                    )
                )
            ).all()
            assert obligation.status == "PENDING" and len(members) == 2
        await _expire_result(sessions, original)
        payload = {
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": original.request_json["rack_id"],
            "status": "SUCCEEDED",
            "final_position": original.request_json["target"],
            "arrival_face": "90",
        }
        if pending_result:
            callback_id = await callback(transport.service, original, payload)
            run(worker, ACTIVATE)
            async with sessions() as db:
                assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "ACTIVE"
        else:
            run(worker, ACTIVATE)
        # 提交独立任务，不发加速通知；原 drain 分配交付未知后杀死全部 worker。
        await _prepare_without_wakeup(runtime, sessions, line.id)
        run(worker, DISPATCH)
        async with sessions.begin() as db:
            awaiting_retry = await db.get(WmsConfirmation, obligation.id)
            assert awaiting_retry.status == "PENDING"
            assert awaiting_retry.request_payload == obligation.request_payload
            awaiting_retry.next_attempt_at = timezone.now_for_db() - timedelta(seconds=1)
        old_pids = {worker.process.pid, worker.confirmation_process.pid}
        for process in (worker.process, worker.confirmation_process):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        worker._log_file.close()
        worker._confirmation_log_file.close()
        worker.start()
        assert old_pids.isdisjoint({worker.process.pid, worker.confirmation_process.pid})
        run(worker, DISPATCH)
        if not pending_result:
            callback_id = await callback(transport.service, original, payload)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        await callback(transport.service, original, payload, operation_id=callback_id)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        async with sessions() as db:
            status = (await db.get(ManualPickingDrainExecution, execution.id)).status
            assert status == ("ACTIVE" if pending_result else "EXCEPTION_RETIRED")
            current = await db.get(TransportTask, original.id)
            assert current.status == "SUCCEEDED" and current.submit_operation_id == original.submit_operation_id
            assert current.submit_request_body == original.submit_request_body
            saved = await db.get(WmsConfirmation, obligation.id)
            assert saved.operation_id == obligation.operation_id and saved.request_payload == obligation.request_payload
            assert saved.status == "COMPLETED"
            response = await db.get(InboundEvidence, saved.response_evidence_id)
            assert response.published_at is not None and response.decision_digest is not None
            assert (await db.get(type(queued), queued.id)).status == "PREPARING"
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.source_evidence_id.in_(
                            (execution.decision_evidence_id, saved.response_evidence_id)
                        ),
                    )
                )
            ).all()
            original_clients = {row.client_request_id for row in originals}
            assert original_clients <= {row.client_request_id for row in bindings}
            returns = (
                await db.scalars(
                    select(BinLineReturn).where(
                        BinLineReturn.id.in_(tuple(member.return_id for member in members)),
                    )
                )
            ).all()
            if pending_result:
                new_bindings = [row for row in bindings if row.client_request_id not in original_clients]
                assert len(new_bindings) == 1 and new_bindings[0].step == "MANUAL_PICKING_RETURN_BATCH"
                binding = new_bindings[0]
                assert binding.source_evidence_id == response.id and binding.resource_fence_id == saved.operation_id
                submitted = await db.scalar(
                    select(TransportTask).where(TransportTask.client_request_id == binding.client_request_id)
                )
                by_id = {row.id: row for row in returns}
                frozen = [by_id[member.return_id] for member in sorted(members, key=lambda row: row.sequence_no)]
                requested = [move["bin_code"] for move in submitted.request_json["moves"]]
                allocated = [move["bin_code"] for move in response.normalized_payload["data"]["moves"]]
                assert requested == allocated == [row.bin_code for row in frozen[: len(requested)]]
                assert requested
                for index, row in enumerate(frozen):
                    if index < len(requested):
                        assert row.return_batch_evidence_id == response.id
                        if row.return_state == "EXITED":
                            assert submitted.status in {"ACCEPTED", "SUCCEEDED", "FAILED"}
                        else:
                            assert row.return_state == "RETURN_REQUESTED"
                    else:
                        assert row.return_state == "READY" and row.return_batch_evidence_id is None
            else:
                assert len(bindings) == len(original_clients)
                assert all(row.return_state == "READY" and row.return_batch_evidence_id is None for row in returns)
            assert len((await db.scalars(select(ManualPickingDrainReturnMember))).all()) == 2
            evidence = (
                await db.scalars(
                    select(TransportEvidence).where(
                        TransportEvidence.operation_id == callback_id,
                        TransportEvidence.operation == RESULT_OPERATION,
                    )
                )
            ).all()
            assert len(evidence) == 1 and evidence[0].status == "APPLIED"
            assert evidence[0].transport_task_id == original.transport_task_id
            fact = await db.scalar(
                select(InboundEvidence).where(
                    InboundEvidence.transport_task_id == original.transport_task_id,
                    InboundEvidence.kind == InboundEvidenceKind.TRANSPORT_RESULT,
                )
            )
            assert fact is not None and fact.published_at is not None and fact.apply_status == "APPLIED"
            record_property(
                "drain_acceptance",
                json.dumps(
                    {
                        "execution_id": execution.id,
                        "drain_operation_id": execution.drain_operation_id,
                        "status": status,
                        "original_client_ids": sorted(original_clients),
                        "original_submit_ids": [row.submit_operation_id for row in originals],
                        "return_ids": [member.return_id for member in members],
                        "return_states": {str(row.id): row.return_state for row in returns},
                        "bin_transport_status": submitted.status if pending_result else None,
                        "bin_transport_id": submitted.transport_task_id if pending_result else None,
                        "return_operation_id": saved.operation_id,
                        "return_request_digest": saved.request_digest,
                        "return_evidence_id": response.id,
                        "new_task_id": queued.id,
                        "callback_id": callback_id,
                        "callback_evidence_id": evidence[0].id,
                        "fact_id": fact.id,
                        "old_new_successors": len(bindings) - len(original_clients),
                        "old_worker_pids": sorted(old_pids),
                        "new_worker_pids": [worker.process.pid, worker.confirmation_process.pid],
                    },
                    sort_keys=True,
                ),
            )
        _assert_worker_tasks(worker, DISPATCH, APPLY, PUBLISH, EXECUTE, ACTIVATE)
        requests = [
            row["envelope"] for row in server.requests if row["envelope"]["operation"] == "outbound.bin.return_batch@v1"
        ]
        assert len(requests) == 2 and requests[0] == requests[1] == obligation.request_payload


async def _start_return_request(worker, sessions, line, server, transport):
    execution, originals = await _start_drain(worker, sessions, line)
    arrived = next(row for row in originals if row.request_json["rack_id"] == server.drain_rack_id)
    blocker = next(row for row in originals if row.id != arrived.id)
    callback_id = await callback(
        transport.service,
        arrived,
        {
            "kind": "RACK_MOVE",
            "outcome_revision": 1,
            "rack_id": server.drain_rack_id,
            "status": "SUCCEEDED",
            "final_position": arrived.request_json["target"],
            "arrival_face": "90",
        },
    )
    run(worker, APPLY)
    run(worker, PUBLISH)
    run(worker, EXECUTE)
    run(worker, ACTIVATE)
    return execution, originals, arrived, blocker, callback_id


async def _prepare_without_wakeup(runtime, sessions, line_id):
    coordinator = PickingTaskPrepareCoordinator(
        sessions,
        policy=runtime.plugins[0].picking_task_prepare_policy,
        task_queue_gateway=runtime.picking_task_prepare_service._task_queue,
    )
    async with sessions.begin() as db:
        line = await locked_line(db, line_id)
        assert (await coordinator.prepare_next_in_session(db, line)).prepared


async def _accept_source_plan(runtime, sessions, task, rack_id):
    policies = {
        (plugin.plugin_key, plugin.plugin_version): plugin.picking_task_plan_admission_policy
        for plugin in runtime.plugins
        if plugin.picking_task_plan_admission_policy is not None
    }
    outbound = build_outbound_picking_runtime(
        session_factory=sessions,
        prepare_plugin_identities=runtime.picking_task_prepare_service.plugin_identities,
        plan_activation_plugin_identities=runtime.picking_task_plan_activation_service.plugin_identities,
        plan_admission_policies=policies,
    )
    payload = {
        "operation_id": new_uuid7(),
        "operation": "outbound.picking_task.plan_delta@v1",
        "timestamp": 1,
        "data": {
            "task_id": task.task_id,
            "plan_revision": 1,
            "added_bin_source_racks": [{"rack_id": rack_id, "rack_face": ["90"]}],
        },
    }
    ack = await outbound.picking_task_plan_delta_handler.handle(payload)
    assert (ack.http_status, ack.body["code"]) == (202, "RECEIVED")
    async with sessions() as db:
        planned = await db.get(type(task), task.id)
        assert planned.status == "EXECUTING" and planned.last_applied_plan_revision == 1
        evidence = await db.get(InboundEvidence, planned.last_plan_evidence_id)
        assert evidence.operation_id == payload["operation_id"] and evidence.apply_status == "APPLIED"
