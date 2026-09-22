"""显式 PostgreSQL 验证：工作线锁竞争和 Confirmation 反向锁序。"""

import asyncio
from datetime import timedelta

import pytest
from manual_picking.application.drain_repository import DrainRepository
from rack_cycle_support import BusinessServer, activate, locked_line, rack_database, runtime_for, seed_line, seed_task
from sqlalchemy import select, text

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone

pytest_plugins = ("tests.integration.conftest",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def wait_for_block(sessions, waiter, blocker):
    # pg_blocking_pids 是服务器证据；不依赖时间窗口或概率 sleep。
    async with asyncio.timeout(5):
        async with sessions() as db:
            while not await db.scalar(
                text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"), {"waiter": waiter, "blocker": blocker}
            ):
                await db.execute(text("SELECT pg_stat_clear_snapshot()"))


async def serialized_pair(sessions, line_id, action):
    waiting = asyncio.Event()
    waiter_pid = None

    async def second():
        nonlocal waiter_pid
        async with sessions.begin() as db:
            waiter_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            waiting.set()
            line = await locked_line(db, line_id)
            return await action(db, line)

    async with asyncio.timeout(10):
        async with sessions.begin() as db:
            line = await locked_line(db, line_id)
            blocker = await db.scalar(text("SELECT pg_backend_pid()"))
            task = asyncio.create_task(second())
            try:
                await waiting.wait()
                await wait_for_block(sessions, waiter_pid, blocker)
                first = await action(db, line)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        return first, await task


async def test_duplicate_wakes_create_one_root_and_one_wait_successor(rack_database):
    _, sessions = rack_database
    server = BusinessServer(wait=True)
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db)
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            driver = runtime.plugins[0].picking_task_batch_driver

            async def action(db, line):
                return await driver.advance_completed_in_session(db, line)

            assert await serialized_pair(sessions, line.id, action) == (1, 0)
            assert await runtime.execution.wms_confirmation_service.dispatch_batch() == 1
            await runtime.execution.fact_processor.process_batch(100)
            async with sessions.begin() as db:
                root = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.workline_id == line.id))
                evidence = await db.get(InboundEvidence, root.response_evidence_id)
                assert evidence.published_at is not None
                due = root.completed_at + timedelta(milliseconds=1)
            flow = driver._drain

            async def wait_action(db, line):
                return await flow.decide_in_session(db, line, due)

            results = await serialized_pair(sessions, line.id, wait_action)
            assert [result[0] for result in results] == [1, 0]
            async with sessions() as db:
                rows = (
                    await db.scalars(
                        select(WmsConfirmation)
                        .where(WmsConfirmation.workline_id == line.id)
                        .order_by(WmsConfirmation.created_at, WmsConfirmation.id)
                    )
                ).all()
                assert len(rows) == 2
                assert rows[1].operation_id != rows[0].operation_id
                assert rows[1].request_payload["data"] == rows[0].request_payload["data"]
    finally:
        server.close()


@pytest.mark.parametrize("queued_first", [True, False])
async def test_next_task_claim_and_first_drain_are_atomic(rack_database, queued_first):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
        if queued_first:
            queued = await seed_task(db, line, queued=True)
    async with runtime_for(sessions, "http://127.0.0.1:9") as (runtime, _transport):
        driver = runtime.plugins[0].picking_task_batch_driver

        async def action(db, line):
            return await driver.advance_completed_in_session(db, line)

        assert await serialized_pair(sessions, line.id, action) == (1, 0)
        if not queued_first:
            async with sessions.begin() as db:
                queued = await seed_task(db, line, queued=True)
            result = await driver._drain._prepare.prepare_next_for_workline(line.id)
            assert not result.prepared
        async with sessions() as db:
            task = await db.get(type(queued), queued.id)
            rows = (
                await db.scalars(
                    select(WmsConfirmation).where(
                        (WmsConfirmation.workline_id == line.id) | (WmsConfirmation.picking_task_id == task.id)
                    )
                )
            ).all()
            assert len(rows) == 1
            assert task.status == ("PREPARING" if queued_first else "QUEUED")
            assert rows[0].operation == (
                "outbound.picking_task.prepare@v1" if queued_first else "workline.return_buffer.drain_rack_decide@v1"
            )


@pytest.mark.parametrize("phase", ["pre_dispatch", "response_save"])
async def test_dispatch_confirmation_and_activation_lock_order(rack_database, phase):
    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            await activate(runtime, line.id)
            confirmation_locked = asyncio.Event()
            let_owner_lock = asyncio.Event()
            dispatcher_pid = None

            class CoordinatedOwner(ReturnBufferDrainOwnerService):
                calls = 0

                async def validate_owner(self, db, **kwargs):
                    nonlocal dispatcher_pid
                    self.calls += 1
                    if self.calls == (1 if phase == "pre_dispatch" else 2):
                        dispatcher_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                        confirmation_locked.set()
                        await let_owner_lock.wait()
                    return await super().validate_owner(db, **kwargs)

            dispatcher = WmsConfirmationService(
                session_factory=sessions,
                adapter=WmsConfirmationAdapter(transport.client),
                workline_owner=CoordinatedOwner(),
            )
            async with asyncio.timeout(10):
                # response-save must pass pre-dispatch validation before activation takes WorkLine.
                if phase == "response_save":
                    task = asyncio.create_task(dispatcher.dispatch_batch())
                    await confirmation_locked.wait()
                async with sessions.begin() as db:
                    await locked_line(db, line.id)
                    blocker = await db.scalar(text("SELECT pg_backend_pid()"))
                    if phase == "pre_dispatch":
                        task = asyncio.create_task(dispatcher.dispatch_batch())
                        await confirmation_locked.wait()
                    let_owner_lock.set()
                    try:
                        await wait_for_block(sessions, dispatcher_pid, blocker)
                        current = await DrainRepository().current(db, line.id)
                        assert current is not None and current.result is None
                    except BaseException:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise
                assert await task == 1
            async with sessions() as db:
                row = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.workline_id == line.id))
                assert row.status == "COMPLETED"
                assert row.response_evidence_id is not None
                assert len(server.requests) == 1
    finally:
        server.close()


@pytest.mark.parametrize("reply", ["RECEIVED", "DUPLICATE", "CONFLICT", "INVALID"])
async def test_ctu03_acceptance_releases_window_and_success_closes_fence(rack_database, reply):
    from manual_picking.application.batch_repository import BatchRepository
    from wes_plugin_sdk import TransportZonePosition

    from src.app.execution.models import TransportDecisionBinding
    from src.app.transport.models import TransportTask
    from src.app.wms_adapter.transport_wire import RESULT_OPERATION
    from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
    from src.core.uuid7 import new_uuid7
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, picking = await seed_line(db, bins=0, capacity=2)
            picking.status = "EXECUTING"
            picking.last_applied_plan_revision = 1
            picking.target_rack_id = "TARGET"
            picking.target_rack_face = "90"
            picking.initial_plan_evidence_id = picking.issued_evidence_id
            picking.last_plan_evidence_id = picking.issued_evidence_id
            for index in range(3):
                db.add(
                    PickingTaskBinSourceRack(
                        picking_task_id=picking.id,
                        rack_id=f"R{index}-{line.id}",
                        rack_face="90",
                        plan_revision=1,
                        source_evidence_id=picking.issued_evidence_id,
                    )
                )
        async with runtime_for(sessions, server.url) as (runtime, transport):
            driver = runtime.plugins[0].picking_task_batch_driver

            async def action(db, line):
                return await driver._fill_source_window(db, line, picking)

            assert await serialized_pair(sessions, line.id, action) == (2, 0)
            assert await transport.service.submit_pending_tasks(100) == 2
            async with sessions() as db:
                ingress = await db.scalar(
                    select(TransportTask)
                    .join(
                        TransportDecisionBinding,
                        TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                    )
                    .where(TransportDecisionBinding.resource_fence_id == f"R0-{line.id}")
                )
            payload = {
                "kind": "RACK_MOVE",
                "outcome_revision": 1,
                "rack_id": f"R0-{line.id}",
                "status": "SUCCEEDED",
                "final_position": {
                    "kind": "RACK_POSITION",
                    "location_code": line.position_bindings["FIVE_RACK"]["location_id"],
                },
                "arrival_face": "90",
            }
            callback_id = new_uuid7()
            await record_valid_callback(
                transport.service,
                operation_id=callback_id,
                transport_task_id=ingress.transport_task_id,
                operation=RESULT_OPERATION,
                timestamp=1,
                payload=payload,
            )
            assert await transport.service.process_pending_evidence(100) == 1
            async with sessions.begin() as db:
                await locked_line(db, line.id)
                await driver._rack_creator.create_source_return(
                    db,
                    workline_id=line.id,
                    source_evidence_id=picking.issued_evidence_id,
                    correlation_id=f"pt:{picking.id}:e:{picking.issued_evidence_id}:rack:R0-{line.id}",
                    step="MANUAL_PICKING_SOURCE_RACK_OUT",
                    rack_id=f"R0-{line.id}",
                    destination=TransportZonePosition("WH01"),
                )
                assert await BatchRepository().occupied_source_rack_ids(db, line.id) == {
                    f"R0-{line.id}",
                    f"R1-{line.id}",
                }
            server.transport_code = reply
            assert await transport.service.submit_pending_tasks(100) == 1
            released = reply in {"RECEIVED", "DUPLICATE"}
            assert await serialized_pair(sessions, line.id, action) == (int(released), 0)
            async with sessions() as db:
                departure = await db.scalar(
                    select(TransportTask)
                    .join(
                        TransportDecisionBinding,
                        TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                    )
                    .where(TransportDecisionBinding.step == "MANUAL_PICKING_SOURCE_RACK_OUT")
                )
            if released:
                await record_valid_callback(
                    transport.service,
                    operation_id=new_uuid7(),
                    transport_task_id=departure.transport_task_id,
                    operation=RESULT_OPERATION,
                    timestamp=2,
                    payload={
                        "kind": "RACK_MOVE",
                        "outcome_revision": 1,
                        "rack_id": f"R0-{line.id}",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "WHE0502"},
                    },
                )
                assert await transport.service.process_pending_evidence(100) == 1
            assert await serialized_pair(sessions, line.id, action) == (0, 0)
            await record_valid_callback(
                transport.service,
                operation_id=callback_id,
                transport_task_id=ingress.transport_task_id,
                operation=RESULT_OPERATION,
                timestamp=1,
                payload=payload,
            )
            await transport.service.process_pending_evidence(100)
            assert await serialized_pair(sessions, line.id, action) == (0, 0)
            async with sessions() as db:
                bindings = (
                    await db.scalars(
                        select(TransportDecisionBinding).where(
                            TransportDecisionBinding.workline_id == line.id,
                            TransportDecisionBinding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                        )
                    )
                ).all()
                assert len(bindings) == (3 if released else 2)
                assert len(await BatchRepository().occupied_source_rack_ids(db, line.id)) == 2
                departure = await db.get(TransportTask, departure.id)
                expected_status = "SUCCEEDED" if released else "RECONCILING" if reply == "CONFLICT" else "PENDING"
                assert departure.status == expected_status
                assert (departure.result_deadline_at is not None) == released
                if not released:
                    assert departure.reason_code == (
                        "TRANSPORT_SUBMIT_CONFLICT" if reply == "CONFLICT" else "SUBMIT_DELIVERY_UNKNOWN"
                    )
    finally:
        server.close()


async def test_completed_drain_reader_does_not_lock_confirmation_under_workline(rack_database):
    from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db)
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            await activate(runtime, line.id)
            await runtime.execution.wms_confirmation_service.dispatch_batch()
            await runtime.execution.fact_processor.process_batch(100)
        async with sessions() as db:
            root = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.workline_id == line.id))
        locked = asyncio.Event()
        writer_pid = None

        async def writer():
            nonlocal writer_pid
            async with sessions.begin() as db:
                await WmsConfirmationRepository().get_by_identity_for_update(db, root.operation, root.operation_id)
                writer_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                locked.set()
                # 使用 owner 的真实 Confirmation -> WorkLine 锁序。
                assert await ReturnBufferDrainOwnerService().validate_owner(
                    db, workline_id=line.id, request_payload=root.request_payload
                )

        async with asyncio.timeout(10):
            async with sessions.begin() as db:
                await locked_line(db, line.id)
                blocker = await db.scalar(text("SELECT pg_backend_pid()"))
                task = asyncio.create_task(writer())
                try:
                    await locked.wait()
                    await wait_for_block(sessions, writer_pid, blocker)
                    current = await DrainRepository().current(db, line.id)
                    assert current.result.racks[0].rack_id == server.drain_rack_id
                except BaseException:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
            await task
    finally:
        server.close()
