"""一条完整正常链的真实进程接线验收；不重复业务/共享可靠机制矩阵。"""

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
import test_drain_wait_worker_recovery as processes
from automatic_picking.application.feed_model import AutomaticPickingFeed
from automatic_picking.application.final_target_model import AutomaticPickingFinalTargetRack
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from automatic_picking.application.work_model import AutomaticPickingWork
from sqlalchemy import func, select
from test_material_close_postgresql import report_result
from test_material_flow_postgresql import result
from test_picking_device_ports_postgresql import arrived, assembled
from test_pickup_identity_postgresql import TARGET, database, prepared
from test_return_rack_completion_postgresql import COMPLETION, DEPARTURE, answer, callback, offline_wakeups
from wes_plugin_sdk import TransportRackPosition, TransportRcsTemplateId

from src.app.device.contracts import EcsCommandResultReport, EcsDeviceEventReport
from src.app.device.models.command import DeviceCommand
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.workline.models import WorkLine
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
MODULE = "normal_picking_worker_runtime"


def start(worker):
    original = subprocess.Popen

    def launch(command, **kwargs):
        command = list(command)
        if "--include" in command:
            command[command.index("--include") + 1] += "," + MODULE
        return original(command, **kwargs)

    with patch.object(broker.subprocess, "Popen", launch):
        worker.process, worker._log_file, worker.log_path = worker._start_process(
            "device-command", worker.hostname, "wes-normal-facts-"
        )
        worker.confirmation_process, worker._confirmation_log_file, worker.confirmation_log_path = (
            worker._start_process("celery", "plans-" + worker.hostname, "wes-normal-plans-")
        )
        fulfillment = worker._start_process("wms-fulfillment", "transport-" + worker.hostname, "wes-normal-transport-")
    worker._capture_descendants()
    return fulfillment


async def row_after_scan(p, query, predicate=lambda row: row is not None):
    found = None

    async def progressed():
        nonlocal found
        async with p.sessions() as db:
            found = await db.scalar(query)
            return predicate(found)

    await processes.wait_for(progressed)
    return found


async def admit_result(p, code):
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == code))
        # 显式给定设备已收到原命令的前提；不运行派发、不伪造 ACK 或物理完成。
        assert command.status == "PENDING"
        command.status = "DISPATCHING"
        device = command.device_code
    return await DeviceEvidenceService(session_factory=p.sessions).accept_result(
        EcsCommandResultReport(
            command_code=code,
            device_code=device,
            result="SUCCESS",
            finish_time=int(timezone.now_utc().timestamp() * 1000),
            data={"pick_and_put_result": "PUT_FINISHED", "actual_qty": 1},
        )
    )


async def test_normal_chain_real_workers_beat_and_completed_task_departure_recovery(
    database, prepared, monkeypatch, tmp_path
):
    p = prepared
    p.plugin = await assembled(p)
    p.scan_platform = "EXPLICIT-PLATFORM"
    async with p.sessions.begin() as db:
        task = await db.get(PickingTask, p.task.id)
        task.last_applied_plan_revision = 1
        task.target_rack_id, task.target_rack_face = TARGET.rack_id, TARGET.rack_face
        task.initial_plan_evidence_id = task.last_plan_evidence_id = task.issued_evidence_id
        feed = await db.get(AutomaticPickingFeed, p.feed.id)
        feed.completed_source_member_ids = [feed.source_member_id]
    # Work READY、来源服务已结束和原目标架已到位是本轮起点；不重复搬运验收。
    async with p.sessions.begin() as db:
        line = await db.get(WorkLine, p.line.id)
        line.position_bindings = {
            **line.position_bindings,
            "WORK_BIN": {"location_type": "BIN", "location_id": p.work_location},
            "SCAN_PLATFORM": {"location_type": "SCAN_PLATFORM", "location_id": p.scan_platform},
        }
        source_handle = await p.creator.create(
            db,
            workline_id=line.id,
            picking_task_id=p.task.id,
            source_evidence_id=p.task.issued_evidence_id,
            correlation_id="normal-worker-source-premise",
            step="PICKING_TASK_BIN_SOURCE_RACK_IN",
            resource_fence_id="RACK-1",
            intent=N(
                rack_id="RACK-1",
                source=TransportRackPosition("OFFLINE-STORAGE"),
                target=TransportRackPosition(line.position_bindings["FIVE_RACK"]["location_id"]),
                target_face="A",
                rcs_template_id=TransportRcsTemplateId.CTU01,
            ),
        )
        source_inbound = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == source_handle.transport_task_id)
        )
    await callback(p, source_inbound)
    original_inbound = await arrived(p)
    associations = tmp_path / "associations.json"
    associations.write_text("{}")
    event_file, fixture = tmp_path / "events.jsonl", tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "line_id": p.line.id,
                "events": str(event_file),
                "fact_processing": True,
                "callback_processing": True,
                "associations": str(associations),
            }
        )
    )
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join((str(Path(__file__).parents[2] / "src"), str(Path(__file__).parent))),
    )
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")
    server = broker.MockWmsHttpServer().start()
    url, _ = database
    worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
    beat, fulfillment, success = None, None, False
    try:
        fulfillment = start(worker)
        with patch.object(processes, "MODULE", MODULE):
            beat = processes.start_beat(worker, tmp_path)
        beat_pids = {beat[0].pid}
        pickup = await row_after_scan(p, select(AutomaticPickingPickup))
        p.pickup_id = pickup.pickup_id
        source_code = pickup.command_code
        source_result = await admit_result(p, source_code)
        raw = " 单盘扫码原文\nworker-normal "
        scan = await DeviceEvidenceService(session_factory=p.sessions).accept_event(
            EcsDeviceEventReport(
                device_code="ARM01",
                event_type="SCAN_COMPLETED",
                timestamp=int(timezone.now_utc().timestamp() * 1000),
                data={"location": p.scan_platform, "barcode": raw},
            )
        )
        associations.write_text(json.dumps({str(scan.evidence_id): source_code}))
        decide = await row_after_scan(
            p, select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.material.decide@v1")
        )
        p.material_id = decide.material_execution_id
        p.now = timezone.now_for_db()
        await result(p, decide, data={"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}})
        pickup = await row_after_scan(
            p, select(AutomaticPickingPickup), lambda row: row is not None and row.put_command_code is not None
        )
        put_code = pickup.put_command_code
        async with p.sessions() as db:
            source = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == source_code))
            put = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == put_code))
            assert source.device_code == "ARM01" and put.device_code == "ARM02"
            assert source.params["source"]["bin_type"] == "2"
            assert put.params["target"]["rack_face"] == TARGET.rack_face
            assert (await db.get(InboundEvidence, source_result.evidence_id)).published_at is not None
            assert (await db.get(InboundEvidence, scan.evidence_id)).command_code == source_code
        put_result = await admit_result(p, put_code)
        report = await row_after_scan(
            p, select(WmsConfirmation).where(WmsConfirmation.operation == "outbound.material.movement_report@v1")
        )
        assert report.request_payload["data"]["barcode"] == raw
        p.now = timezone.now_for_db()
        await report_result(p)
        await row_after_scan(
            p,
            select(MaterialExecution).where(MaterialExecution.id == p.material_id),
            lambda row: row.status == "CLOSED",
        )
        completion = await row_after_scan(p, select(WmsConfirmation).where(WmsConfirmation.operation == COMPLETION))
        p.now = timezone.now_for_db()
        await answer(p, COMPLETION, {"result": "COMPLETED"}, publish=False)
        await row_after_scan(
            p, select(PickingTask).where(PickingTask.id == p.task.id), lambda row: row.status == "EXECUTION_COMPLETED"
        )
        departure = await row_after_scan(p, select(WmsConfirmation).where(WmsConfirmation.operation == DEPARTURE))
        p.now = timezone.now_for_db()
        await answer(
            p,
            DEPARTURE,
            {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": "OFFLINE-RETURN"}},
            publish=False,
        )
        current = await row_after_scan(
            p,
            select(AutomaticPickingFinalTargetRack),
            lambda row: row is not None and row.departure_transport_task_id is not None,
        )
        original_out = await row_after_scan(
            p, select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
        )
        assert current.root_transport_task_id == original_inbound.transport_task_id
        assert current.closed_evidence_id is None and original_out.request_json["rcs_template_id"] == "F01"
        async with p.sessions() as db:
            assert (
                await db.scalar(select(func.count()).select_from(PickingTask).where(PickingTask.status == "EXECUTING"))
                == 0
            )
            assert (await db.get(InboundEvidence, put_result.evidence_id)).published_at is not None
            work = await db.get(AutomaticPickingWork, p.work.id)
            assert work.phase == "EXIT_PENDING"  # 任务完成不等待退箱或最终架清场。
            commands = {row.command_code for row in (await db.scalars(select(DeviceCommand))).all()}
            confirmations = {row.id for row in (await db.scalars(select(WmsConfirmation))).all()}
            transports = {row.transport_task_id for row in (await db.scalars(select(TransportTask))).all()}
        processes.stop_beat(beat)
        beat = None
        processes.stop_beat(fulfillment)
        fulfillment = None
        worker.close(success=True)
        first_pids = {event["pid"] for event in processes.events(event_file) if event["kind"] == "normal_runtime"}
        worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
        fulfillment = start(worker)
        with patch.object(processes, "MODULE", MODULE):
            beat = processes.start_beat(worker, tmp_path)
        beat_pids.add(beat[0].pid)

        async def scanned_after_restart():
            trace = processes.events(event_file)
            received = {
                e["task_id"]
                for e in trace
                if e["kind"] == "received" and e["task"] == processes.TASK and e["pid"] not in first_pids
            }
            return any(e["kind"] == "finished" and e["task_id"] in received for e in trace)

        await processes.wait_for(scanned_after_restart)
        async with p.sessions() as db:
            pending = await db.get(AutomaticPickingFinalTargetRack, current.id)
            assert pending.closed_evidence_id is None
            assert pending.departure_transport_task_id == original_out.transport_task_id
            assert (
                await db.scalar(select(func.count()).select_from(PickingTask).where(PickingTask.status == "EXECUTING"))
                == 0
            )
        data = {
            "transport_task_id": original_out.transport_task_id,
            "kind": original_out.kind,
            "outcome_revision": 1,
            "rack_id": TARGET.rack_id,
            "status": "SUCCEEDED",
            "final_position": original_out.request_json["target"],
            "arrival_face": TARGET.rack_face,
        }
        operation_id = new_uuid7()
        message = {
            "operation": "transport.task.resulted@v1",
            "operation_id": operation_id,
            "timestamp": 1,
            "data": data,
        }
        assert (
            await p.service.record_callback(
                operation_id=operation_id,
                operation=message["operation"],
                message=message,
                payload=data,
                rejection_reason_code=None,
            )
        )["code"] == "RECEIVED"
        closed = await row_after_scan(
            p,
            select(AutomaticPickingFinalTargetRack),
            lambda row: row is not None and row.closed_evidence_id is not None,
        )
        assert closed.id == current.id and closed.departure_transport_task_id == original_out.transport_task_id
        async with p.sessions() as db:
            assert {row.command_code for row in (await db.scalars(select(DeviceCommand))).all()} == commands
            assert {row.id for row in (await db.scalars(select(WmsConfirmation))).all()} == confirmations
            assert {row.transport_task_id for row in (await db.scalars(select(TransportTask))).all()} == transports
            assert (await db.get(PickingTask, p.task.id)).status == "EXECUTION_COMPLETED"
            evidence = await db.get(InboundEvidence, closed.closed_evidence_id)
            assert evidence.transport_task_id == original_out.transport_task_id and evidence.published_at is not None
        trace = processes.events(event_file)
        assert {e["pid"] for e in trace if e["kind"] == "normal_runtime"} - first_pids
        assert not any(e["kind"] == "forbidden_dispatch" for e in trace)
        assert all(e["state"] == "SUCCESS" for e in trace if e["kind"] == "finished")
        expected = {
            processes.TASK: "celery",
            "src.celery_app.tasks.device_command.process_device_evidence_batch": "device-command",
            "src.celery_app.tasks.execution.process_execution_facts_batch": "device-command",
            "src.celery_app.tasks.transport.process_transport_evidence_batch": "wms-fulfillment",
            "src.celery_app.tasks.transport.publish_transport_outcomes_batch": "wms-fulfillment",
        }
        for task, queue in expected.items():
            assert any(e["kind"] == "received" and e["task"] == task and e["queue"] == queue for e in trace)
        assert {e["task"] for e in trace if e["kind"] == "published"} == set(expected)
        assert {e["pgid"] for e in trace if e["kind"] == "published"} <= beat_pids
        assert server.requests == []
        assert completion.picking_task_id == p.task.id and departure.picking_task_id == p.task.id
        success = True
    finally:
        processes.stop_beat(beat)
        processes.stop_beat(fulfillment)
        worker.close(success=success)
        server.close()
