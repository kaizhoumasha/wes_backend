"""原离场结果经真实 FactProcessor 发布后，交回 batch 收口；不接回调进程链。"""

import json
import os
import signal
from pathlib import Path

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_completion_postgresql import clean_closed_root
from test_drain_departure_postgresql import departure_result, departures
from test_drain_departure_worker_recovery import snapshot
from test_drain_progress_postgresql import current, requests
from test_drain_ready_continuation_postgresql import serve, successor
from test_drain_replenishment_postgresql import database, drainable, observed, pending, qualified
from test_drain_wait_worker_recovery import TASK, events, start_beat, start_worker, stop_beat, wait_for
from test_return_allocation_postgresql import arrived

from src.app.execution.models import InboundEvidence
from src.app.wms_integration.outbound_picking.models import PickingTask
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
FACT_TASK = "src.celery_app.tasks.execution.process_execution_facts_batch"


async def test_fact_worker_publishes_before_plan_worker_closes_original_motion(
    database, drainable, monkeypatch, tmp_path
):
    p = drainable
    url, _ = database
    first, ready, _ = await successor(p)
    original_departure = (await departures(p))[0]
    _, last_departure = await serve(p, ready, fill=True)
    assert await current(p) is None
    await clean_closed_root(p, first)
    await clean_closed_root(p, ready)
    p.positions, p.buffer = {}, None
    # 父进程只准备已保存但未由 FactProcessor 发布的权威结果检查点。
    for task in (original_departure, last_departure):
        await departure_result(p, task, process=False)
    transport_ids = {original_departure.transport_task_id, last_departure.transport_task_id}
    async with p.sessions() as db:
        facts = (
            await db.scalars(
                select(InboundEvidence).where(
                    InboundEvidence.kind == "TRANSPORT_RESULT", InboundEvidence.transport_task_id.in_(transport_ids)
                )
            )
        ).all()
        assert len(facts) == 2
        fact_ids = {row.transport_task_id: row.id for row in facts}
        assert all(
            row.apply_status == "APPLIED" and row.published_at is None and row.decision_digest is None for row in facts
        )
        stages = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
        frozen = {row.id: row.departure_transport_task_id for row in stages}
        assert set(frozen.values()) == transport_ids
        assert all(row.archived_at is None and row.departure_result_evidence_id is None for row in stages)
    before = await snapshot(p.sessions)
    event_file = tmp_path / "events.jsonl"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "line_id": p.line.id,
                "location": None,
                "events": str(event_file),
                "departure_recovery": True,
                "fact_processing": True,
            }
        )
    )
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
    # 沿用设备 runtime 配置测试的启动输入；没有设备端点或业务映射。
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            (str(Path(__file__).parents[2] / "src"), str(Path(__file__).parent), os.environ.get("PYTHONPATH", ""))
        ),
    )
    server = broker.MockWmsHttpServer().start()
    beat_pids, closed = set(), None
    try:
        for restart in range(2):
            worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
            beat, stopped, success = None, False, False
            try:
                start_worker(worker)
                plan_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]
                if restart:
                    start_worker(worker, facts=True)
                beat = start_beat(worker, tmp_path)
                beat_pids.add(beat[0].pid)

                async def plan_finished(beat=beat, pid=plan_pid):
                    assert beat[0].poll() is None
                    return any(e["kind"] == "finished" and e["pid"] == pid for e in events(event_file))

                await wait_for(plan_finished)
                if not restart:
                    assert await snapshot(p.sessions) == before
                    os.killpg(worker.confirmation_process.pid, signal.SIGSTOP)
                    stopped = True
                    start_worker(worker, facts=True)
                    fact_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]

                    async def published():
                        async with p.sessions() as db:
                            rows = (
                                await db.scalars(
                                    select(InboundEvidence).where(InboundEvidence.id.in_(fact_ids.values()))
                                )
                            ).all()
                            return len(rows) == len(fact_ids) and all(row.published_at is not None for row in rows)

                    await wait_for(published)
                    applied = await snapshot(p.sessions)
                    for table, rows in before.items():
                        assert applied[table].keys() == rows.keys()
                        if table not in {AutomaticPickingReturnMotion.__tablename__, InboundEvidence.__tablename__}:
                            assert applied[table] == rows
                    for key, row in applied[InboundEvidence.__tablename__].items():
                        if key in fact_ids.values():
                            assert row["published_at"] is not None and row["decision_digest"] is not None
                            assert row["apply_status"] == "APPLIED"
                            assert row["decision_claim_token"] is None and row["decision_claim_expires_at"] is None
                        else:
                            assert row == before[InboundEvidence.__tablename__][key]
                    for key, row in applied[AutomaticPickingReturnMotion.__tablename__].items():
                        assert row["archived_at"] is None
                        assert row["departure_transport_task_id"] == frozen[key]
                        assert row["departure_result_evidence_id"] == fact_ids[frozen[key]]
                        for field in (
                            "workline_id",
                            "arrival_evidence_id",
                            "source_evidence_id",
                            "rack_id",
                            "rack_face",
                            "departure_operation_id",
                        ):
                            assert row[field] == before[AutomaticPickingReturnMotion.__tablename__][key][field]
                    os.killpg(worker.confirmation_process.pid, signal.SIGCONT)
                    stopped = False
                else:
                    fact_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]

                async def closed_and_consumed(plan_pid=plan_pid, fact_pid=fact_pid):
                    async with p.sessions() as db:
                        rows = (await db.scalars(select(AutomaticPickingReturnMotion))).all()
                        finished_pids = {e["pid"] for e in events(event_file) if e["kind"] == "finished"}
                        return (
                            len(rows) == len(frozen)
                            and all(row.archived_at is not None for row in rows)
                            and {plan_pid, fact_pid} <= finished_pids
                        )

                await wait_for(closed_and_consumed)
                saved = await snapshot(p.sessions)
                if not restart:
                    assert saved[AutomaticPickingReturnMotion.__tablename__].keys() == frozen.keys()
                    for table, rows in applied.items():
                        if table != AutomaticPickingReturnMotion.__tablename__:
                            assert saved[table] == rows
                    for key, row in saved[AutomaticPickingReturnMotion.__tablename__].items():
                        assert row["archived_at"] is not None
                        assert row["departure_transport_task_id"] == frozen[key]
                        assert row["departure_result_evidence_id"] == fact_ids[frozen[key]]
                    closed = saved
                else:
                    assert saved == closed
                assert await current(p) is None and await requests(p) == []
                async with p.sessions() as db:
                    assert all([await db.get(AutomaticPickingDrain, root.id) is None for root in (first, ready)])
                    assert (
                        await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))
                    ).first() is None
                success = True
            finally:
                if stopped:
                    os.killpg(worker.confirmation_process.pid, signal.SIGCONT)
                stop_beat(beat)
                worker.close(success=success)
        trace = events(event_file)
        routed = {TASK: "celery", FACT_TASK: "device-command"}
        sent = {e["task_id"] for e in trace if e["kind"] == "published"}
        consumed = [e for e in trace if e["kind"] == "received"]
        assert {e["task"] for e in consumed} == set(routed)
        assert {e["task_id"] for e in consumed} <= sent
        assert all(e["queue"] == routed[e["task"]] for e in trace if e["kind"] in {"published", "received"})
        assert all(e["state"] == "SUCCESS" for e in trace if e["kind"] == "finished")
        runtime = {e["pid"]: e for e in trace if e["kind"] == "runtime"}
        assert len(runtime) == 4 and {e["pid"] for e in consumed} == set(runtime)
        assert all(runtime[e["pid"]]["consumer_queue"] == e["queue"] for e in consumed)
        assert {e["pgid"] for e in trace if e["kind"] == "published"} == beat_pids
        task_names = {e["task_id"]: e["task"] for e in consumed}
        for task in routed:
            assert sum(
                e["result"] for e in trace if e["kind"] == "finished" and task_names[e["task_id"]] == task
            ) == len(frozen)
        assert {e["fact_source"] for e in runtime.values()} == {
            str(Path(__file__).parents[4] / "src/app/execution/services/fact_processor.py")
        }
        assert server.requests == []
    finally:
        server.close()
