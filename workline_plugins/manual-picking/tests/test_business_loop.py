"""显式 PostgreSQL/Redis/Celery 业务闭环；外部 WMS/ECS 使用测试边界。"""

import os
from contextlib import asynccontextmanager

import pytest
from manual_picking.application.batch_driver import SOURCE_RACK_OUT_STEP
from manual_picking.application.batch_repository import BatchRepository
from manual_picking.application.drain_repository import DRAIN_RACK_IN_STEP, DRAIN_RACK_OUT_STEP, DrainRepository
from manual_picking.application.passage_model import ManualPickingPassage
from rack_cycle_support import BusinessServer, rack_database, runtime_for, seed_line, seed_task
from sqlalchemy import select

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
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
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _task = await seed_line(db, bins=2)
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
            "rack_id": server.drain_result["rack_id"],
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
            passages = (
                await db.scalars(
                    select(ManualPickingPassage)
                    .where(ManualPickingPassage.workline_id == line.id)
                    .order_by(ManualPickingPassage.scan4_received_at)
                )
            ).all()
            assert [move["bin_code"] for move in returned.request_json["moves"]] == [p.bin_code for p in passages]
            assert {p.return_state for p in passages} == {"RETURN_REQUESTED"}
        run(worker, SUBMIT)
        assert run(worker, ACTIVATE) == 0
        result = {
            "kind": "BIN_MOVE",
            "outcome_revision": 1,
            "results": [
                {"container_id": move["bin_code"], "status": "SUCCEEDED", "final_position": move["target"]}
                for move in returned.request_json["moves"]
            ],
        }
        for move in returned.request_json["moves"]:
            ack = await record_valid_callback(
                transport.service,
                operation_id=new_uuid7(),
                transport_task_id=returned.transport_task_id,
                operation=POSITION_OPERATION,
                timestamp=1,
                payload={
                    "container_id": move["bin_code"],
                    "milestone": "TARGET_PLACED",
                    "final_position": move["target"],
                },
            )
            assert ack["http_status"] == 202
        run(worker, APPLY)
        await callback(transport.service, returned, result)
        run(worker, APPLY)
        run(worker, PUBLISH)
        run(worker, EXECUTE)
        run(worker, ACTIVATE)
        departure = await bound_transport(sessions, line.id, DRAIN_RACK_OUT_STEP)
        run(worker, SUBMIT)
        departure = await bound_transport(sessions, line.id, DRAIN_RACK_OUT_STEP)
        # ACK 证明 RCS/WMS 已接管离场动作，释放准入窗口；同架围栏仍等最终结果。
        assert departure.status == "ACCEPTED" and departure.result_deadline_at is not None
        async with sessions() as db:
            assert await BatchRepository().occupied_source_rack_ids(db, line.id) == set()
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
        async with sessions() as db:
            assert await BatchRepository().occupied_source_rack_ids(db, line.id) == set()
            assert await DrainRepository().current(db, line.id) is None
            passages = (
                await db.scalars(select(ManualPickingPassage).where(ManualPickingPassage.workline_id == line.id))
            ).all()
            assert {(p.return_state, p.disposition) for p in passages} == {("RETURNED", "CLOSED")}
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
async def test_source_departure_acceptance_refills_one_slot_through_real_worker(rack_database, transport_code):
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
        picking.target_rack_id = f"TARGET-{line.id}"
        picking.target_rack_face = "90"
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
        target = await bound_transport(sessions, line.id, "PICKING_TASK_TARGET_RACK_IN")
        # ACCEPTED 只证明接纳。通过正式 callback/Evidence 路径提供目标架及首个来源架到位事实。
        for task, rack_id, role in (
            (target, picking.target_rack_id, "TRANSFER_RACK"),
            (initial[rack_ids[0]], rack_ids[0], "FIVE_RACK"),
        ):
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
        assert sum(request["path"].endswith("transport-requests") for request in server.requests) == 5
