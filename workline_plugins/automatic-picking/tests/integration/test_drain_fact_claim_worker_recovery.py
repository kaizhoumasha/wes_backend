"""原 CTU03 业务 Evidence 领取提交后失效，沿原租约恢复。"""

import json
import os
import signal
from datetime import datetime
from pathlib import Path

import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_callback_worker_recovery import unchanged
from test_drain_completion_postgresql import clean_closed_root
from test_drain_departure_postgresql import departure_result, departures
from test_drain_departure_worker_recovery import snapshot
from test_drain_fact_worker_recovery import FACT_TASK
from test_drain_progress_postgresql import current, requests
from test_drain_ready_continuation_postgresql import serve, successor
from test_drain_replenishment_postgresql import database, drainable, observed, pending, qualified
from test_drain_wait_worker_recovery import TASK, events, start_beat, start_worker, stop_beat, wait_for
from test_return_allocation_postgresql import arrived

from src.app.execution.models import InboundEvidence
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.utils.timezone import timezone
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_committed_fact_claim_survives_killed_worker_and_recovers_after_original_expiry(
    database, drainable, monkeypatch, tmp_path
):
    p = drainable
    url, _ = database
    first, ready, _ = await successor(p)
    original = (await departures(p))[0]
    _, last = await serve(p, ready, fill=True)
    assert await current(p) is None
    for root in (first, ready):
        await clean_closed_root(p, root)
    p.positions, p.buffer = {}, None
    ids = {original.transport_task_id, last.transport_task_id}
    for task in (original, last):
        await departure_result(p, task, process=False)
    async with p.sessions() as db:
        facts = (
            await db.scalars(
                select(InboundEvidence)
                .where(InboundEvidence.kind == "TRANSPORT_RESULT", InboundEvidence.transport_task_id.in_(ids))
                .order_by(InboundEvidence.received_at, InboundEvidence.id)
            )
        ).all()
        assert len(facts) == 2
        assert all(row.published_at is None and row.decision_claim_token is None for row in facts)
        fact_ids = {row.id: row.transport_task_id for row in facts}
        first_id = facts[0].id
    before = await snapshot(p.sessions)
    assert len(before[AutomaticPickingReturnMotion.__tablename__]) == len(fact_ids)
    assert {
        row["departure_transport_task_id"] for row in before[AutomaticPickingReturnMotion.__tablename__].values()
    } == ids
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
                "claim_stop": {"evidence_id": first_id, "marker": str(tmp_path / "stopped-once")},
            }
        )
    )
    monkeypatch.setenv("AUTOMATIC_PICKING_WAIT_FIXTURE", str(fixture))
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
    claimed, closed, dead_claim, dead_pid = None, None, None, None
    beat_pids = set()
    try:
        for phase in range(3):
            worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
            beat, success = None, False
            try:
                start_worker(worker, facts=True)
                fact_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]
                if phase == 2:
                    start_worker(worker)
                    plan_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]
                beat = start_beat(worker, tmp_path)
                beat_pids.add(beat[0].pid)
                if phase == 0:

                    async def at_stop(beat=beat):
                        assert beat[0].poll() is None
                        return any(e["kind"] == "claim_ready" and e["stopping"] for e in events(event_file))

                    await wait_for(at_stop)
                    dead_claim = next(e for e in events(event_file) if e["kind"] == "claim_ready")
                    dead_pid = fact_pid
                    assert dead_claim["pid"] == dead_pid and dead_claim["evidence_id"] == first_id
                    claimed = await snapshot(p.sessions)
                    unchanged(before, claimed, InboundEvidence.__tablename__)
                    for key, row in claimed[InboundEvidence.__tablename__].items():
                        if key in fact_ids:
                            assert row["decision_claim_token"] == dead_claim["token"]
                            assert row["decision_claim_expires_at"] == dead_claim["expiry"]
                            assert datetime.fromisoformat(row["decision_claim_expires_at"]) > timezone.now_for_db()
                            assert row == {
                                **before[InboundEvidence.__tablename__][key],
                                "decision_claim_token": row["decision_claim_token"],
                                "decision_claim_expires_at": row["decision_claim_expires_at"],
                                "updated_at": row["updated_at"],
                            }
                        else:
                            assert row == before[InboundEvidence.__tablename__][key]
                    stop_beat(beat)
                    beat = None
                    worker._capture_descendants()
                    os.killpg(worker.process.pid, signal.SIGKILL)
                    worker.process.wait(timeout=5)
                    assert worker.process.returncode == -signal.SIGKILL
                    assert await snapshot(p.sessions) == claimed
                    success = True
                    continue

                async def first_fact_scan(pid=fact_pid, beat=beat):
                    assert beat[0].poll() is None
                    return any(e["kind"] == "finished" and e["pid"] == pid for e in events(event_file))

                await wait_for(first_fact_scan)
                if phase == 1:
                    first_result = next(
                        e for e in events(event_file) if e["kind"] == "finished" and e["pid"] == fact_pid
                    )
                    assert first_result["result"] == 0
                    assert timezone.now_for_db() < datetime.fromisoformat(dead_claim["expiry"])
                    assert await snapshot(p.sessions) == claimed

                    async def published():
                        state = await snapshot(p.sessions)
                        return all(state[InboundEvidence.__tablename__][key]["published_at"] for key in fact_ids)

                    remaining = (datetime.fromisoformat(dead_claim["expiry"]) - timezone.now_for_db()).total_seconds()
                    await wait_for(published, timeout=max(remaining, 0) + broker.TASK_TIMEOUT_SECONDS)
                    recovered = [e for e in events(event_file) if e["kind"] == "claim_ready" and not e["stopping"]]
                    assert len(recovered) == 1
                    assert recovered[0]["pid"] == fact_pid and recovered[0]["evidence_id"] == first_id
                    assert recovered[0]["token"] != dead_claim["token"]
                    assert datetime.fromisoformat(recovered[0]["observed_at"]) > datetime.fromisoformat(
                        dead_claim["expiry"]
                    )
                    applied = await snapshot(p.sessions)
                    unchanged(
                        claimed, applied, InboundEvidence.__tablename__, AutomaticPickingReturnMotion.__tablename__
                    )
                    assert (
                        applied[InboundEvidence.__tablename__].keys() == claimed[InboundEvidence.__tablename__].keys()
                    )
                    for key, row in applied[InboundEvidence.__tablename__].items():
                        if key in fact_ids:
                            assert row["published_at"] and row["decision_digest"]
                            assert row["decision_claim_token"] is None and row["decision_claim_expires_at"] is None
                            assert row == {
                                **claimed[InboundEvidence.__tablename__][key],
                                "published_at": row["published_at"],
                                "decision_digest": row["decision_digest"],
                                "decision_claim_token": None,
                                "decision_claim_expires_at": None,
                                "updated_at": row["updated_at"],
                            }
                        else:
                            assert row == claimed[InboundEvidence.__tablename__][key]
                    assert (
                        applied[AutomaticPickingReturnMotion.__tablename__].keys()
                        == claimed[AutomaticPickingReturnMotion.__tablename__].keys()
                    )
                    for key, row in applied[AutomaticPickingReturnMotion.__tablename__].items():
                        assert row["archived_at"] is None
                        assert fact_ids[row["departure_result_evidence_id"]] == row["departure_transport_task_id"]
                        assert row == {
                            **claimed[AutomaticPickingReturnMotion.__tablename__][key],
                            "departure_result_evidence_id": row["departure_result_evidence_id"],
                            "updated_at": row["updated_at"],
                        }
                    start_worker(worker)
                    plan_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]

                async def closed_and_consumed(plan_pid=plan_pid, fact_pid=fact_pid):
                    state = await snapshot(p.sessions)
                    finished = {e["pid"] for e in events(event_file) if e["kind"] == "finished"}
                    return (
                        all(row["archived_at"] for row in state[AutomaticPickingReturnMotion.__tablename__].values())
                        and {fact_pid, plan_pid} <= finished
                    )

                await wait_for(closed_and_consumed)
                final = await snapshot(p.sessions)
                if phase == 1:
                    unchanged(applied, final, AutomaticPickingReturnMotion.__tablename__)
                    assert (
                        final[AutomaticPickingReturnMotion.__tablename__].keys()
                        == applied[AutomaticPickingReturnMotion.__tablename__].keys()
                    )
                    for key, row in final[AutomaticPickingReturnMotion.__tablename__].items():
                        assert row == {
                            **applied[AutomaticPickingReturnMotion.__tablename__][key],
                            "archived_at": row["archived_at"],
                            "updated_at": row["updated_at"],
                        }
                    closed = final
                else:
                    assert final == closed
                assert await current(p) is None and await requests(p) == []
                async with p.sessions() as db:
                    assert all([await db.get(AutomaticPickingDrain, root.id) is None for root in (first, ready)])
                    assert (
                        await db.scalars(select(PickingTask).where(PickingTask.status == "EXECUTING"))
                    ).first() is None
                success = True
            finally:
                stop_beat(beat)
                if worker.process is not None and worker.process.poll() is None:
                    os.killpg(worker.process.pid, signal.SIGCONT)
                worker.close(success=success)
        trace = events(event_file)
        runtime = {e["pid"]: e for e in trace if e["kind"] == "runtime"}
        sent = {e["task_id"]: e for e in trace if e["kind"] == "published"}
        consumed = {e["task_id"]: e for e in trace if e["kind"] == "received"}
        finished = {e["task_id"]: e for e in trace if e["kind"] == "finished"}
        routes = {FACT_TASK: "device-command", TASK: "celery"}
        assert len(runtime) == 5 and {e["pid"] for e in consumed.values()} == set(runtime)
        interrupted = consumed.keys() - finished.keys()
        assert len(interrupted) == 1 and consumed[next(iter(interrupted))]["pid"] == dead_pid
        assert {e["pgid"] for e in sent.values()} == beat_pids
        assert all(e["state"] == "SUCCESS" for e in finished.values())
        for task_id, row in consumed.items():
            assert task_id in sent
            assert row["queue"] == routes[row["task"]] == runtime[row["pid"]]["consumer_queue"]
        for task in routes:
            assert sum(e["result"] for key, e in finished.items() if consumed[key]["task"] == task) == len(fact_ids)
        assert {e["fact_source"] for e in runtime.values()} == {
            str(Path(__file__).parents[4] / "src/app/execution/services/fact_processor.py")
        }
        assert server.requests == []
    finally:
        server.close()
