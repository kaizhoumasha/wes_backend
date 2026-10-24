"""Beat 扫描 successor READY：只创建本地进场义务，不发送或模拟到位。"""

import json
import os
from pathlib import Path

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain, AutomaticPickingDrainRack
from automatic_picking.application.return_model import AutomaticPickingReturn
from sqlalchemy import select
from test_drain_completion_postgresql import clean_closed_root
from test_drain_ready_continuation_postgresql import input_for, successor
from test_drain_replenishment_postgresql import database, drainable, observed, pending, qualified
from test_drain_wait_worker_recovery import TASK, events, start_beat, start_worker, stop_beat, wait_for
from test_return_allocation_postgresql import arrived

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
TABLES = (
    AutomaticPickingDrain,
    AutomaticPickingDrainRack,
    TransportDecisionBinding,
    TransportTask,
    WmsConfirmation,
    DeviceCommand,
)


async def records(sessions):
    async with sessions() as db:
        return {
            model.__tablename__: {
                row.id: row.model_dump(mode="json") for row in (await db.scalars(select(model))).all()
            }
            for model in TABLES
        }


async def test_beat_recovers_successor_ready_after_root_cleanup_and_restart(database, drainable, monkeypatch, tmp_path):
    p = drainable
    url, _ = database
    original, ready, _ = await successor(p)
    await input_for(p, ready, "OFFLINE-NEXT")
    await clean_closed_root(p, original)
    async with p.sessions() as db:
        member = await db.get(AutomaticPickingReturn, p.return_id)
        assert await db.get(AutomaticPickingDrain, original.id) is None
        assert (
            await db.scalar(select(WmsConfirmation).where(WmsConfirmation.operation_id == original.operation_id))
            is None
        )
        assert (await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))).first() is None
        assert not (
            await db.scalars(select(AutomaticPickingDrainRack).where(AutomaticPickingDrainRack.drain_id == ready.id))
        ).all()
    before = await records(p.sessions)
    event_file = tmp_path / "events.jsonl"
    intent = p.inbound_inputs["OFFLINE-NEXT"]
    inputs = {
        "line_id": p.line.id,
        "line_code": p.line.line_code,
        "return_id": p.return_id,
        "bin_code": member.bin_code,
        "location": p.buffer.location_code,
        "capacity": p.buffer.capacity,
        "events": str(event_file),
        "inbound": {
            "workline_id": intent.workline_id,
            "operation_id": intent.operation_id,
            "ready_evidence_id": intent.ready_evidence_id,
            "rack_id": intent.rack_id,
            "location": intent.target.location_code,
            "face": intent.target_face,
            "template": intent.rcs_template_id.value,
        },
    }
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(inputs))
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            (str(Path(__file__).parents[2] / "src"), str(Path(__file__).parent), os.environ.get("PYTHONPATH", ""))
        ),
    )
    server = broker.MockWmsHttpServer().start()
    beat_pids, previous, saved = set(), set(), None
    try:
        for _ in range(2):
            worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
            beat, success = None, False
            try:
                start_worker(worker)
                beat = start_beat(worker, tmp_path)
                beat_pids.add(beat[0].pid)

                async def finished_tick(beat=beat, previous=previous):
                    assert beat[0].poll() is None
                    return any(e["kind"] == "finished" and e["task_id"] not in previous for e in events(event_file))

                await wait_for(finished_tick)
                snapshot = await records(p.sessions)
                if saved is None:
                    async with p.sessions() as db:
                        tasks = (
                            await db.scalars(
                                select(TransportTask).where(
                                    TransportTask.id.not_in(before[TransportTask.__tablename__])
                                )
                            )
                        ).all()
                        assert len(tasks) == 1
                        task = tasks[0]
                        assert task.status == "PENDING" and task.submit_attempt_count == 0
                        assert task.send_started_at is None and task.result_deadline_at is None
                        assert task.request_json["rcs_template_id"] == "CTU01"
                        assert task.request_json["rack_id"] == intent.rack_id
                        assert task.request_json["source"] == {"kind": "RACK", "location_code": intent.rack_id}
                        assert task.request_json["target"] == {
                            "kind": "RACK_POSITION",
                            "location_code": intent.target.location_code,
                        }
                        assert task.request_json["target_face"] == intent.target_face
                        rows = (
                            await db.scalars(
                                select(AutomaticPickingDrainRack).where(AutomaticPickingDrainRack.drain_id == ready.id)
                            )
                        ).all()
                        assert len(rows) == 1
                        rack = rows[0]
                        binding = await db.get(TransportDecisionBinding, rack.binding_id)
                        assert rack.rack_id == intent.rack_id and rack.arrival_evidence_id is None
                        assert binding.client_request_id == task.client_request_id
                        assert binding.source_evidence_id == ready.response_evidence_id
                        assert binding.workline_id == p.line.id and binding.picking_task_id is None
                        assert binding.correlation_id == f"drain:{ready.operation_id}:{intent.rack_id}"
                        assert binding.resource_fence_id == intent.rack_id
                    for table, rows in before.items():
                        assert all(snapshot[table][key] == value for key, value in rows.items())
                    assert snapshot[WmsConfirmation.__tablename__] == before[WmsConfirmation.__tablename__]
                    assert snapshot[DeviceCommand.__tablename__] == before[DeviceCommand.__tablename__]
                    assert snapshot[AutomaticPickingDrain.__tablename__] == before[AutomaticPickingDrain.__tablename__]
                    saved = snapshot
                else:
                    assert snapshot == saved
                async with p.sessions() as db:
                    assert await db.get(AutomaticPickingDrain, original.id) is None
                    assert (
                        await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))
                    ).first() is None
                previous = {e["task_id"] for e in events(event_file) if e["kind"] == "finished"}
                success = True
            finally:
                stop_beat(beat)
                worker.close(success=success)
        trace = events(event_file)
        sent = {e["task_id"] for e in trace if e["kind"] == "published"}
        consumed = {e["task_id"] for e in trace if e["kind"] == "received"}
        assert consumed and consumed <= sent
        assert all(
            e["task"] == TASK and e["queue"] == "celery" for e in trace if e["kind"] in {"published", "received"}
        )
        assert all(e["state"] == "SUCCESS" for e in trace if e["kind"] == "finished")
        results = [e["result"] for e in trace if e["kind"] == "finished"]
        assert len(results) >= 2 and results[0] == 1 and all(result == 0 for result in results[1:])
        runtime_pids = {e["pid"] for e in trace if e["kind"] == "runtime"}
        assert len(runtime_pids) == 2
        assert {e["pid"] for e in trace if e["kind"] == "received"} == runtime_pids
        assert {e["pgid"] for e in trace if e["kind"] == "published"} == beat_pids
        assert {e["inbound_source"] for e in trace if e["kind"] == "runtime"} == {
            str(Path(__file__).parents[2] / "src/automatic_picking/application/drain_inbound.py")
        }
        assert server.requests == []
    finally:
        server.close()
