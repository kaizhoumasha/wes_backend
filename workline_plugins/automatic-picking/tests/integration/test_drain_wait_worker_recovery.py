"""真实 Beat 是唯一唤醒来源；只创建续问，不派发 WMS 或物理动作。"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_allocation_repository import ReturnAllocationRepository
from automatic_picking.application.return_model import AutomaticPickingReturn
from automatic_picking.application.return_repository import ReturnRepository
from automatic_picking.definition import DEFINITION
from sqlalchemy import select
from test_pickup_identity_postgresql import database
from wes_plugin_sdk import wms_operations

from src.app.device.contracts import EcsDeviceEvent
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService, ReturnBufferDrainScheduler
from src.app.workline.models import WorkLine
from src.celery_app.config import beat_schedule
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
MODULE = "drain_wait_worker_runtime"
TASK = "src.celery_app.tasks.picking_task_plan.activate_picking_task_plans_batch"


async def checkpoint(sessions):
    now = timezone.now_for_db() - timedelta(seconds=30)
    operation = new_uuid7()
    async with sessions.begin() as db:
        line = WorkLine(
            line_code="WAIT-" + operation[-12:],
            line_name="offline WAIT",
            line_type="AUTO",
            is_active=True,
            plugin_key=DEFINITION.plugin_key,
            plugin_version=DEFINITION.plugin_version,
        )
        db.add(line)
        await db.flush()
        # 只预置已完成的历史方向依据，不调用命令创建服务或任何派发入口。
        commands = [
            DeviceCommand(
                command_code=new_uuid7(),
                device_code="OFFLINE-HISTORY",
                workline_id=line.id,
                execution_ref_type="WORKLINE_BUSINESS",
                execution_ref_id=new_uuid7(),
                contract_key="FIXTURE_ONLY",
                contract_version="1",
                task_type="MOVE_FORWARD",
                deadline_at=now,
                endpoint_base_url="http://127.0.0.1:1",
                command_timeout_ms=1000,
                status_max_age_ms=1000,
                payload_digest="a" * 64,
                status="SUCCEEDED",
                completed_at=now,
                outcome_published_at=now,
            )
            for _ in range(2)
        ]
        db.add_all(commands)
        await db.flush()
        facts = []
        for kind in ("DEVICE_EVENT", "DEVICE_EVENT", "DEVICE_RESULT"):
            payload = {"fixture": "historical return qualification", "kind": kind, "id": new_uuid7()}
            if kind == "DEVICE_EVENT":
                payload = EcsDeviceEvent(
                    device_code="OFFLINE-HISTORY",
                    contract_key="FIXTURE_ONLY",
                    contract_version="1",
                    source_event_id=new_uuid7(),
                    event_type="SCAN_COMPLETED",
                    timestamp=1000,
                    is_debug=False,
                    data={"bin_code": "A123456789"},
                ).model_dump(mode="json", exclude_none=True)
            fact = InboundEvidence(
                kind=kind,
                workline_id=line.id,
                device_code="OFFLINE-HISTORY",
                contract_key="FIXTURE_ONLY",
                contract_version="1",
                source_event_id=payload.get("source_event_id"),
                source_identity=new_uuid7(),
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                command_code=commands[1].command_code if kind == "DEVICE_RESULT" else None,
                apply_status="APPLIED",
                received_at=now,
                processed_at=now,
                published_at=now,
                decision_digest=canonical_json_digest({"fixture": "applied"}),
            )
            db.add(fact)
            facts.append(fact)
        await db.flush()
        commands[1].result_evidence_id = facts[2].id
        row = AutomaticPickingReturn(
            workline_id=line.id,
            bin_code="A123456789",
            source_work_id=101,
            entry_command_code=commands[0].command_code,
            scan3_evidence_id=facts[0].id,
            scan4_evidence_id=facts[1].id,
            scan4_event_time=1000,
            scan4_command_code=commands[1].command_code,
            ready_evidence_id=facts[2].id,
        )
        db.add(row)
        await db.flush()
        await ReturnBufferDrainScheduler(
            WmsConfirmationLifecycleService(workline_owner=ReturnBufferDrainOwnerService())
        ).create_in_session(
            db,
            wms_operations.workline_return_buffer_drain_rack_decide(
                operation_id=operation,
                workline_code=line.line_code,
                required_slot_count=1,
            ),
            workline_id=line.id,
            created_at=now,
        )
        payload = {
            "operation_id": operation,
            "code": "DECIDED",
            "timestamp": 1000,
            "data": {
                "result": "WAIT",
                "reason_code": "NO_DRAIN_RACK_AVAILABLE",
                "retry_after_ms": 1000,
            },
        }
        fact = InboundEvidence(
            kind="WMS_RESULT",
            operation="workline.return_buffer.drain_rack_decide@v1",
            operation_id=operation,
            workline_id=line.id,
            source_identity="drain-result:" + operation,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            apply_status="APPLIED",
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest=canonical_json_digest({"fixture": "applied"}),
        )
        db.add(fact)
        await db.flush()
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == operation))
        request.status, request.completed_at, request.response_result = "COMPLETED", now, "WAIT"
        request.response_evidence_id = fact.id
        root = AutomaticPickingDrain(
            workline_id=line.id,
            operation_id=operation,
            started_operation_id=new_uuid7(),
            return_ids=[row.id],
            request_return_ids=[row.id],
            response_evidence_id=fact.id,
            retry_at=now + timedelta(seconds=1),
        )
        db.add(root)
        await db.flush()
        return {
            "line_id": line.id,
            "line_code": line.line_code,
            "return_id": row.id,
            "bin_code": row.bin_code,
            "location": "OFFLINE-OUTLET",
            "capacity": 4,
        }, root


def start_worker(worker):
    original = subprocess.Popen

    def launch(command, **kwargs):
        command = list(command)
        if "--include" in command:
            command[command.index("--include") + 1] += "," + MODULE
        return original(command, **kwargs)

    # 只追加测试启动模块，继续使用既有真实进程、readiness 和清理机制。
    with patch.object(broker.subprocess, "Popen", launch):
        worker.confirmation_process, worker._confirmation_log_file, worker.confirmation_log_path = (
            worker._start_process(broker.CONFIRMATION_QUEUE, worker.hostname, "wes-drain-wait-")
        )
    worker._capture_descendants()


def start_beat(worker, directory):
    log = (directory / ("beat-" + worker.run_id + ".log")).open("w")
    uv = shutil.which("uv")
    assert uv is not None
    process = subprocess.Popen(
        [
            uv,
            "run",
            "--no-sync",
            "celery",
            "-A",
            MODULE,
            "beat",
            "--loglevel=INFO",
            "--schedule",
            str(directory / "beat-schedule"),
        ],
        cwd=broker.REPO_ROOT,
        env=broker._worker_environment(
            worker.database_url,
            worker.redis_url,
            worker.wms_base_url,
            worker.transport_submit_path,
            worker.run_id,
            worker.key_prefix,
            broker.CONFIRMATION_QUEUE,
        ),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return process, log


def stop_beat(beat):
    if beat is not None:
        broker.TransportBrokerWorker._terminate_process_group(beat[0])
        beat[1].close()


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def wait_for(check, *, timeout=broker.TASK_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.2)
    pytest.fail("Beat/worker checkpoint did not progress within the bounded deadline")


async def test_beat_alone_recovers_due_wait_after_pause_and_restart(database, monkeypatch, tmp_path):
    url, sessions = database
    inputs, old = await checkpoint(sessions)
    async with sessions() as db:
        rows = await ReturnRepository().list_fifo(db, inputs["line_id"])
        assert [r.id for r in rows] == old.return_ids
        assert await ReturnAllocationRepository().qualified(db, rows[0])
        historical_commands = {
            (r.id, r.command_code, r.payload_digest, r.status, r.attempt_count)
            for r in (await db.scalars(select(DeviceCommand))).all()
        }
    event_file = tmp_path / "events.jsonl"
    inputs["events"] = str(event_file)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(inputs))
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            (
                str(Path(__file__).parents[2] / "src"),
                str(Path(__file__).parent),
                os.environ.get("PYTHONPATH", ""),
            )
        ),
    )
    server = broker.MockWmsHttpServer().start()
    worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
    beat, stopped, success = None, False, False
    try:
        start_worker(worker)
        os.killpg(worker.confirmation_process.pid, signal.SIGSTOP)
        stopped = True
        beat = start_beat(worker, tmp_path)
        beat_pids = {beat[0].pid}

        async def published_once():
            assert beat[0].poll() is None
            return any(e["kind"] == "published" for e in events(event_file))

        await wait_for(published_once)
        async with sessions() as db:
            assert (await db.get(AutomaticPickingDrain, old.id)).archived_at is None
            assert len((await db.scalars(select(WmsConfirmation))).all()) == 1
            assert (await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))).first() is None
        os.killpg(worker.confirmation_process.pid, signal.SIGCONT)
        stopped = False

        async def continued():
            async with sessions() as db:
                rows = (
                    await db.scalars(select(AutomaticPickingDrain).where(AutomaticPickingDrain.archived_at.is_(None)))
                ).all()
                return len(rows) == 1 and rows[0].operation_id != old.operation_id

        await wait_for(continued)
        async with sessions() as db:
            current = await db.scalar(select(AutomaticPickingDrain).where(AutomaticPickingDrain.archived_at.is_(None)))
            assert current.return_ids == current.request_return_ids == old.return_ids
            assert current.started_operation_id == old.started_operation_id
            assert current.response_evidence_id is None and current.retry_at is None
            original = await db.get(AutomaticPickingDrain, old.id)
            assert original.archived_at is not None and original.retry_at == old.retry_at
            next_operation = current.operation_id
        stop_beat(beat)
        beat = None
        worker.close(success=True)
        previous = {e["task_id"] for e in events(event_file) if e["kind"] == "finished"}
        worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
        start_worker(worker)
        beat = start_beat(worker, tmp_path)
        beat_pids.add(beat[0].pid)

        async def tick_after_restart():
            assert beat[0].poll() is None
            return any(e["kind"] == "finished" and e["task_id"] not in previous for e in events(event_file))

        await wait_for(tick_after_restart)
        async with sessions() as db:
            current = await db.scalar(select(AutomaticPickingDrain).where(AutomaticPickingDrain.archived_at.is_(None)))
            assert current.operation_id == next_operation
            requests = (await db.scalars(select(WmsConfirmation))).all()
            assert {r.operation_id for r in requests} == {old.operation_id, next_operation}
            new = next(r for r in requests if r.operation_id == next_operation)
            assert new.status == "PENDING" and new.attempt_count == 0
            assert new.request_payload["data"] == {
                "workline_code": inputs["line_code"],
                "required_slot_count": 1,
            }
            assert list((await db.scalars(select(TransportTask))).all()) == []
            assert {
                (r.id, r.command_code, r.payload_digest, r.status, r.attempt_count)
                for r in (await db.scalars(select(DeviceCommand))).all()
            } == historical_commands
        trace = events(event_file)
        sent = {e["task_id"] for e in trace if e["kind"] == "published"}
        received = {e["task_id"] for e in trace if e["kind"] == "received"}
        assert received and received <= sent
        assert all(
            e["task"] == TASK and e["queue"] == "celery" for e in trace if e["kind"] in {"published", "received"}
        )
        assert all(e["state"] == "SUCCESS" for e in trace if e["kind"] == "finished")
        runtime_pids = {e["pid"] for e in trace if e["kind"] == "runtime"}
        assert len(runtime_pids) == 2
        assert {e["pid"] for e in trace if e["kind"] == "received"} == runtime_pids
        assert {e["pgid"] for e in trace if e["kind"] == "published"} == beat_pids
        assert {e["source"] for e in trace if e["kind"] == "runtime"} == {
            str(Path(__file__).parents[2] / "src/automatic_picking/application/drain_replenishment_reader.py")
        }
        assert server.requests == []
        success = True
    finally:
        if stopped and worker.confirmation_process is not None:
            os.killpg(worker.confirmation_process.pid, signal.SIGCONT)
        stop_beat(beat)
        try:
            worker.close(success=success)
        finally:
            server.close()
