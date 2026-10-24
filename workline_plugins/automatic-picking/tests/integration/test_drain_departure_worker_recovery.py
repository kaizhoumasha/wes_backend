"""根已清理后的原离场义务由 Beat 恢复；不创建或派发新动作。"""

import json
import os
from pathlib import Path

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_completion_postgresql import clean_closed_root
from test_drain_departure_postgresql import departure_result, departures
from test_drain_progress_postgresql import current, requests
from test_drain_ready_continuation_postgresql import serve, successor
from test_drain_ready_worker_recovery import records
from test_drain_replenishment_postgresql import database, drainable, observed, pending, qualified
from test_drain_wait_worker_recovery import TASK, events, start_beat, start_worker, stop_beat, wait_for
from test_return_allocation_postgresql import arrived

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def snapshot(sessions):
    result = await records(sessions)
    async with sessions() as db:
        for model in (AutomaticPickingReturnMotion, TransportMember, InboundEvidence):
            result[model.__tablename__] = {
                row.id: row.model_dump(mode="json") for row in (await db.scalars(select(model))).all()
            }
    return result


async def test_beat_recovers_original_departures_without_current_or_roots(drainable, database, monkeypatch, tmp_path):
    p = drainable
    url, _ = database
    # 复用宿主入口检查点；以下历史装配均在真实进程验收开始之前完成。
    first, ready, _ = await successor(p)
    original_departure = (await departures(p))[0]
    _, last_departure = await serve(p, ready, fill=True)
    assert await current(p) is None
    await clean_closed_root(p, first)
    await clean_closed_root(p, ready)
    p.positions, p.buffer = {}, None
    async with p.sessions() as db:
        phases = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        frozen = {row.id: row.departure_transport_task_id for row in phases}
        assert set(frozen.values()) == {original_departure.transport_task_id, last_departure.transport_task_id}
        assert all(row.archived_at is None for row in phases)
        for row in phases:
            # 未闭合动作的来源和 departure READY 是活动依据，不能随根删除。
            assert await db.get(InboundEvidence, row.source_evidence_id) is not None
            request = await db.scalar(
                select(WmsConfirmation).where(WmsConfirmation.operation_id == row.departure_operation_id)
            )
            assert request.status == "COMPLETED" and request.response_result == "READY"
        for task in (original_departure, last_departure):
            assert (await db.get(TransportTask, task.id)).status == "ACCEPTED"
    before = await snapshot(p.sessions)
    event_file = tmp_path / "events.jsonl"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps({"line_id": p.line.id, "location": None, "events": str(event_file), "departure_recovery": True})
    )
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            (str(Path(__file__).parents[2] / "src"), str(Path(__file__).parent), os.environ.get("PYTHONPATH", ""))
        ),
    )
    server = broker.MockWmsHttpServer().start()
    beat_pids, runtime_pids, closed = set(), [], None
    try:
        for checkpoint in range(3):
            if checkpoint == 1:
                # 只给原动作注入权威结果；夹具负责接收/应用/发布，不调用 batch 收口。
                for task in (original_departure, last_departure):
                    await departure_result(p, task)
                before_success = await snapshot(p.sessions)
                for table, rows in before.items():
                    if table != InboundEvidence.__tablename__:
                        assert before_success[table].keys() == rows.keys()
                assert all(
                    row["archived_at"] is None
                    for row in before_success[AutomaticPickingReturnMotion.__tablename__].values()
                )
            worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
            beat, success = None, False
            try:
                start_worker(worker)
                pid = [event["pid"] for event in events(event_file) if event["kind"] == "runtime"][-1]
                runtime_pids.append(pid)
                beat = start_beat(worker, tmp_path)
                beat_pids.add(beat[0].pid)

                async def finished_tick(beat=beat, pid=pid):
                    assert beat[0].poll() is None
                    return any(e["kind"] == "finished" and e["pid"] == pid for e in events(event_file))

                await wait_for(finished_tick)
                saved = await snapshot(p.sessions)
                if checkpoint == 0:
                    assert saved == before
                elif checkpoint == 1:
                    for table, rows in before_success.items():
                        if table != AutomaticPickingReturnMotion.__tablename__:
                            assert saved[table] == rows
                    stages = saved[AutomaticPickingReturnMotion.__tablename__]
                    assert {key: value["departure_transport_task_id"] for key, value in stages.items()} == frozen
                    assert all(row["archived_at"] is not None for row in stages.values())
                    assert all(row["departure_result_evidence_id"] is not None for row in stages.values())
                    for key, stage in stages.items():
                        for field in (
                            "workline_id",
                            "arrival_evidence_id",
                            "source_evidence_id",
                            "rack_id",
                            "rack_face",
                            "closed_at",
                            "departure_operation_id",
                            "departure_transport_task_id",
                            "departure_result_evidence_id",
                        ):
                            assert (
                                stage[field] == before_success[AutomaticPickingReturnMotion.__tablename__][key][field]
                            )
                    closed = saved
                else:
                    assert saved == closed
                assert await current(p) is None and await requests(p) == []
                async with p.sessions() as db:
                    for root in (first, ready):
                        assert await db.get(AutomaticPickingDrain, root.id) is None
                        assert (
                            await db.scalar(
                                select(WmsConfirmation).where(WmsConfirmation.operation_id == root.operation_id)
                            )
                            is None
                        )
                    assert (
                        await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))
                    ).first() is None
                    assert {row.transport_task_id for row in (await db.scalars(select(TransportTask))).all()} == {
                        row["transport_task_id"] for row in before[TransportTask.__tablename__].values()
                    }
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
        assert len(set(runtime_pids)) == 3
        assert {e["pid"] for e in trace if e["kind"] == "received"} == set(runtime_pids)
        assert {e["pgid"] for e in trace if e["kind"] == "published"} == beat_pids
        for index, pid in enumerate(runtime_pids):
            results = [e["result"] for e in trace if e["kind"] == "finished" and e["pid"] == pid]
            assert results and sum(results) == (len(frozen) if index == 1 else 0)
        assert {e["departure_source"] for e in trace if e["kind"] == "runtime"} == {
            str(Path(__file__).parents[2] / "src/automatic_picking/application/drain_departure.py")
        }
        assert server.requests == []
    finally:
        server.close()
