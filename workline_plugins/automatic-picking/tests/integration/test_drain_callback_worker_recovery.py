"""真实 HTTP→Transport→FactProcessor→batch，恢复原离场义务。"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import psutil
import pytest
from automatic_picking.application.drain_model import AutomaticPickingDrain
from automatic_picking.application.return_motion_model import AutomaticPickingReturnMotion
from sqlalchemy import select
from test_drain_completion_postgresql import clean_closed_root
from test_drain_departure_postgresql import departures
from test_drain_departure_worker_recovery import snapshot
from test_drain_progress_postgresql import current, requests
from test_drain_ready_continuation_postgresql import serve, successor
from test_drain_replenishment_postgresql import database, drainable, observed, pending, qualified
from test_drain_wait_worker_recovery import MODULE, TASK, events, start_beat, start_worker, stop_beat, wait_for
from test_return_allocation_postgresql import arrived

from src.app.execution.models import InboundEvidence
from src.app.transport.models import TransportCallbackReceipt, TransportEvidence, TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.core.uuid7 import new_uuid7
from tests.support import transport_broker as broker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
FACT = "src.celery_app.tasks.execution.process_execution_facts_batch"
APPLY = "src.celery_app.tasks.transport.process_transport_evidence_batch"
PUBLISH = "src.celery_app.tasks.transport.publish_transport_outcomes_batch"


async def saved(sessions):
    result = await snapshot(sessions)
    async with sessions() as db:
        for model in (TransportCallbackReceipt, TransportEvidence):
            result[model.__tablename__] = {
                row.id: row.model_dump(mode="json") for row in (await db.scalars(select(model))).all()
            }
    return result


def unchanged(before, after, *changed):
    assert before.keys() == after.keys()
    for table in before.keys() - set(changed):
        assert after[table] == before[table], table


async def start_http(worker, directory):
    uv = shutil.which("uv")
    assert uv
    log = (directory / f"http-{new_uuid7()}.log").open("w")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        process = await asyncio.to_thread(
            subprocess.Popen,
            [uv, "run", "--no-sync", "uvicorn", "drain_callback_http_runtime:app", "--fd", str(listener.fileno())],
            cwd=broker.REPO_ROOT,
            env=broker._worker_environment(
                worker.database_url,
                worker.redis_url,
                worker.wms_base_url,
                worker.transport_submit_path,
                worker.run_id,
                worker.key_prefix,
            ),
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:

        async def ready():
            assert process.poll() is None, await asyncio.to_thread(Path(log.name).read_text)
            try:
                async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                    return (await client.get(url + "/openapi.json")).status_code == 200
            except httpx.TransportError:
                return False

        await wait_for(ready)
        return (process, log), url
    except BaseException:
        stop_beat((process, log))
        raise


def start_transport(worker):
    original = subprocess.Popen

    def launch(command, **kwargs):
        command = list(command)
        if "--include" in command:
            command[command.index("--include") + 1] += "," + MODULE
        return original(command, **kwargs)

    with patch.object(broker.subprocess, "Popen", launch):
        process, log, _ = worker._start_process(
            broker.FULFILLMENT_QUEUE, f"it-callback-{worker.run_id}@localhost", "wes-drain-callback-"
        )
    worker._descendant_pids.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
    return process, log


async def test_original_departure_callback_survives_process_handoffs_and_replay(
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
    tasks = (original, last)
    ids = {task.transport_task_id for task in tasks}
    before = await saved(p.sessions)
    assert all(row["archived_at"] is None for row in before[AutomaticPickingReturnMotion.__tablename__].values())
    assert {
        row["transport_task_id"]
        for row in before[TransportTask.__tablename__].values()
        if row["transport_task_id"] in ids and row["status"] == "ACCEPTED"
    } == ids
    assert all(
        row["departure_result_evidence_id"] is None
        for row in before[AutomaticPickingReturnMotion.__tablename__].values()
    )
    messages = [
        {
            "operation": "transport.task.resulted@v1",
            "operation_id": new_uuid7(),
            "timestamp": 1,
            "data": {
                "transport_task_id": task.transport_task_id,
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": task.request_json["rack_id"],
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "OFFLINE-OUT"},
                "arrival_face": "A",
            },
        }
        for task in tasks
    ]
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
                "callback_processing": True,
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
    closed, beat_pids = None, set()
    try:
        for restart in range(2):
            worker = broker.TransportBrokerWorker(url, os.environ["INTEGRATION_REDIS_URL"], server.url, pool="prefork")
            http, transport, beat, success = None, None, None, False
            try:
                http, address = await start_http(worker, tmp_path)

                async def post(expected, address=address):
                    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                        for message in messages:
                            reply = await client.post(address + "/api/v1/wms/events", json=message)
                            assert reply.status_code == (202 if expected == "RECEIVED" else 200), reply.text
                            assert reply.json()["code"] == expected, reply.text
                            assert reply.json()["operation_id"] == message["operation_id"]

                await post("DUPLICATE" if restart else "RECEIVED")
                received = await saved(p.sessions)
                if not restart:
                    unchanged(before, received, TransportCallbackReceipt.__tablename__, TransportEvidence.__tablename__)
                    for model in (TransportCallbackReceipt, TransportEvidence):
                        new = received[model.__tablename__].keys() - before[model.__tablename__].keys()
                        assert len(new) == len(tasks)
                    new_evidence = [
                        row
                        for key, row in received[TransportEvidence.__tablename__].items()
                        if key not in before[TransportEvidence.__tablename__]
                    ]
                    assert {row["transport_task_id"] for row in new_evidence} == ids
                    assert all(row["status"] == "PENDING" for row in new_evidence)
                    # ACK 已提交后重建 HTTP 进程，后续只靠原持久义务与 Beat。
                    stop_beat(http)
                    http = None
                    http, address = await start_http(worker, tmp_path)
                    await post("DUPLICATE", address)
                    assert await saved(p.sessions) == received
                else:
                    assert received == closed
                transport = start_transport(worker)
                transport_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]
                beat = start_beat(worker, tmp_path)
                beat_pids.add(beat[0].pid)

                async def outcome_published(beat=beat):
                    assert beat[0].poll() is None
                    async with p.sessions() as db:
                        rows = (
                            await db.scalars(select(TransportTask).where(TransportTask.transport_task_id.in_(ids)))
                        ).all()
                        return len(rows) == len(ids) and all(
                            row.status == "SUCCEEDED" and row.published_outcome_version == row.outcome_version
                            for row in rows
                        )

                await wait_for(outcome_published)
                published = await saved(p.sessions)
                if not restart:
                    unchanged(
                        received,
                        published,
                        TransportTask.__tablename__,
                        TransportMember.__tablename__,
                        TransportEvidence.__tablename__,
                        InboundEvidence.__tablename__,
                    )
                    assert published[TransportTask.__tablename__].keys() == before[TransportTask.__tablename__].keys()
                    for evidence in new_evidence:
                        assert published[TransportEvidence.__tablename__][evidence["id"]]["status"] == "APPLIED"
                    for key, row in published[TransportTask.__tablename__].items():
                        for field in (
                            "transport_task_id",
                            "client_request_id",
                            "request_digest",
                            "request_json",
                            "caller_json",
                            "submit_operation_id",
                            "submit_timestamp_ms",
                            "submit_request_body",
                            "submit_request_body_digest",
                            "submit_attempt_count",
                            "authority_workline_id",
                        ):
                            assert row[field] == before[TransportTask.__tablename__][key][field]
                    facts = {
                        key: row
                        for key, row in published[InboundEvidence.__tablename__].items()
                        if key not in before[InboundEvidence.__tablename__]
                    }
                    assert len(facts) == len(tasks)
                    assert {row["transport_task_id"] for row in facts.values()} == ids
                    assert all(
                        row["kind"] == "TRANSPORT_RESULT"
                        and row["published_at"] is None
                        and row["decision_digest"] is None
                        for row in facts.values()
                    )
                start_worker(worker, facts=True)
                fact_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]

                async def facts_published():
                    async with p.sessions() as db:
                        rows = (
                            await db.scalars(
                                select(InboundEvidence).where(
                                    InboundEvidence.kind == "TRANSPORT_RESULT",
                                    InboundEvidence.transport_task_id.in_(ids),
                                )
                            )
                        ).all()
                        return len(rows) == len(tasks) and all(row.published_at is not None for row in rows)

                await wait_for(facts_published)
                applied = await saved(p.sessions)
                if not restart:
                    unchanged(
                        published, applied, InboundEvidence.__tablename__, AutomaticPickingReturnMotion.__tablename__
                    )
                    assert (
                        applied[InboundEvidence.__tablename__].keys() == published[InboundEvidence.__tablename__].keys()
                    )
                    for key, row in applied[InboundEvidence.__tablename__].items():
                        if key in facts:
                            assert row["published_at"] and row["decision_digest"] and row["apply_status"] == "APPLIED"
                            assert row["decision_claim_token"] is None and row["decision_claim_expires_at"] is None
                        else:
                            assert row == published[InboundEvidence.__tablename__][key]
                    for key, row in applied[AutomaticPickingReturnMotion.__tablename__].items():
                        assert row["archived_at"] is None
                        assert row["departure_result_evidence_id"] in facts
                        assert (
                            facts[row["departure_result_evidence_id"]]["transport_task_id"]
                            == row["departure_transport_task_id"]
                        )
                        old = published[AutomaticPickingReturnMotion.__tablename__][key]
                        assert row["updated_at"] >= old["updated_at"]
                        assert row == {
                            **old,
                            "departure_result_evidence_id": row["departure_result_evidence_id"],
                            "updated_at": row["updated_at"],
                        }
                start_worker(worker)
                plan_pid = [e["pid"] for e in events(event_file) if e["kind"] == "runtime"][-1]

                async def finished(transport_pid=transport_pid, fact_pid=fact_pid, plan_pid=plan_pid):
                    state = await saved(p.sessions)
                    completed = {(e["pid"], e["task_id"]) for e in events(event_file) if e["kind"] == "finished"}
                    observed = events(event_file)
                    return all(
                        row["archived_at"] for row in state[AutomaticPickingReturnMotion.__tablename__].values()
                    ) and all(
                        any(
                            e["kind"] == "received"
                            and e["pid"] == pid
                            and e["task"] == task
                            and (pid, e["task_id"]) in completed
                            for e in observed
                        )
                        for pid, task in (
                            (transport_pid, APPLY),
                            (transport_pid, PUBLISH),
                            (fact_pid, FACT),
                            (plan_pid, TASK),
                        )
                    )

                await wait_for(finished)
                final = await saved(p.sessions)
                if not restart:
                    unchanged(applied, final, AutomaticPickingReturnMotion.__tablename__)
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
                stop_beat(http)
                stop_beat(transport)
                worker.close(success=success)
        trace = events(event_file)
        route = {APPLY: "wms-fulfillment", PUBLISH: "wms-fulfillment", FACT: "device-command", TASK: "celery"}
        runtime = {e["pid"]: e for e in trace if e["kind"] == "runtime"}
        sent = {e["task_id"]: e for e in trace if e["kind"] == "published"}
        consumed = {e["task_id"]: e for e in trace if e["kind"] == "received"}
        assert len(runtime) == 6 and {e["pid"] for e in consumed.values()} == set(runtime)
        assert set(route) == {e["task"] for e in consumed.values()}
        assert all(e["pgid"] in beat_pids and e["queue"] == route[e["task"]] for e in sent.values())
        for task_id, e in consumed.items():
            assert task_id in sent and e["queue"] == runtime[e["pid"]]["consumer_queue"] == route[e["task"]]
        done = [e for e in trace if e["kind"] == "finished"]
        assert all(e["state"] == "SUCCESS" for e in done)
        for task in route:
            assert sum(e["result"] for e in done if consumed[e["task_id"]]["task"] == task) == len(tasks)
        http_ready = [e for e in trace if e["kind"] == "http_ready"]
        assert len({e["pid"] for e in http_ready}) == 3
        assert {e["route_source"] for e in http_ready} == {
            str(Path(__file__).parents[4] / "src/app/wms_adapter/v1/events.py")
        }
        assert {e["outcome_source"] for e in runtime.values()} == {
            str(Path(__file__).parents[4] / "src/app/workline/plugin_routing.py")
        }
        assert {e["method"] for e in trace if e["kind"] == "wakeup_dropped"} >= {
            "enqueue_transport_evidence",
            "enqueue_transport_outcomes",
            "enqueue_picking_task_plans",
        }
        assert server.requests == []
    finally:
        server.close()
