"""显式 PostgreSQL 验证：工作线锁竞争和 Confirmation 反向锁序。"""

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from manual_picking.application.drain_repository import DrainRepository
from rack_cycle_support import BusinessServer, activate, locked_line, rack_database, runtime_for, seed_line, seed_task
from sqlalchemy import select, text

from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.execution.services import WmsConfirmationService
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services import PickingTaskConfirmationOwnerService
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainOwnerService
from src.app.workline.models import WorkLine
from src.app.workline.repositories import WorkLineRepository
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


async def test_picking_confirmation_authority_locks_workline_before_task(rack_database):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, task = await seed_line(db)
    lock_attempted = asyncio.Event()
    waiter_pid = None

    class CoordinatedWorkLines(WorkLineRepository):
        async def get_for_authority_update(self, db, workline_id, *, populate_existing=False):
            nonlocal waiter_pid
            waiter_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            lock_attempted.set()
            return await super().get_for_authority_update(db, workline_id, populate_existing=populate_existing)

    owner = PickingTaskConfirmationOwnerService(worklines=CoordinatedWorkLines())
    owner_task = None

    async def lock_owner():
        async with sessions.begin() as owner_db:
            return await owner.lock_authority_root(db=owner_db, picking_task_id=task.id)

    try:
        async with asyncio.timeout(10):
            async with sessions.begin() as db:
                await locked_line(db, line.id)
                blocker = await db.scalar(text("SELECT pg_backend_pid()"))
                owner_task = asyncio.create_task(lock_owner())
                await lock_attempted.wait()
                await wait_for_block(sessions, waiter_pid, blocker)
                locked_task = await db.scalar(select(PickingTask).where(PickingTask.id == task.id).with_for_update())
                assert locked_task is not None
            assert owner_task is not None
            assert await owner_task
    finally:
        if owner_task is not None and not owner_task.done():
            owner_task.cancel()
            await asyncio.gather(owner_task, return_exceptions=True)


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
async def test_next_task_claim_and_first_drain_keep_independent_lifecycles(rack_database, queued_first):
    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
        if queued_first:
            queued = await seed_task(db, line, queued=True)
    async with runtime_for(sessions, "http://127.0.0.1:9") as (runtime, _transport):
        driver = runtime.plugins[0].picking_task_batch_driver

        async def action(db, line):
            return await driver.advance_completed_in_session(db, line)

        counts = await serialized_pair(sessions, line.id, action)
        assert counts == ((1, 1) if queued_first else (1, 0))
        async with sessions.begin() as db:
            locked = await locked_line(db, line.id)
            assert await action(db, locked) == 0
        if not queued_first:
            async with sessions.begin() as db:
                queued = await seed_task(db, line, queued=True)
            result = await driver._drain._prepare.prepare_next_for_workline(line.id)
            assert result.prepared
        async with sessions() as db:
            task = await db.get(type(queued), queued.id)
            rows = (
                await db.scalars(
                    select(WmsConfirmation).where(
                        (WmsConfirmation.workline_id == line.id) | (WmsConfirmation.picking_task_id == task.id)
                    )
                )
            ).all()
            assert len(rows) == 2
            assert task.status == "PREPARING"
            expected_operations = ["outbound.picking_task.prepare@v1", "workline.return_buffer.drain_rack_decide@v1"]
            assert sorted(row.operation for row in rows) == sorted(expected_operations)
            bindings = (
                await db.scalars(
                    select(TransportDecisionBinding).where(TransportDecisionBinding.workline_id == line.id)
                )
            ).all()
            assert bindings == []
            assert (
                await driver._drain._bindings.list_active_window_for_target(
                    db,
                    workline_id=line.id,
                    target_location_code=line.position_bindings["FIVE_RACK"]["location_id"],
                )
            ) == []


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
            root_snapshot_read = asyncio.Event()
            response_ready = asyncio.Event()
            save_response = asyncio.Event()
            dispatcher_pid = None

            class CoordinatedRepository(WmsConfirmationRepository):
                calls = 0

                async def get_by_id(self, db, confirmation_id):
                    nonlocal dispatcher_pid
                    confirmation = await super().get_by_id(db, confirmation_id)
                    self.calls += 1
                    if self.calls == (1 if phase == "pre_dispatch" else 2):
                        dispatcher_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                        root_snapshot_read.set()
                    return confirmation

            class CoordinatedOwner(ReturnBufferDrainOwnerService):
                calls = 0

                async def validate_owner(self, db, **kwargs):
                    self.calls += 1
                    return await super().validate_owner(db, **kwargs)

            class CoordinatedAdapter(WmsConfirmationAdapter):
                async def dispatch(self, **kwargs):
                    result = await super().dispatch(**kwargs)
                    response_ready.set()
                    await save_response.wait()
                    return result

            owner = CoordinatedOwner()
            dispatcher = WmsConfirmationService(
                repository=CoordinatedRepository(),
                session_factory=sessions,
                adapter=(
                    CoordinatedAdapter(transport.client)
                    if phase == "response_save"
                    else WmsConfirmationAdapter(transport.client)
                ),
                workline_owner=owner,
            )
            task = None
            try:
                async with asyncio.timeout(10):
                    if phase == "response_save":
                        task = asyncio.create_task(dispatcher.dispatch_batch())
                        await response_ready.wait()
                    async with sessions.begin() as db:
                        await locked_line(db, line.id)
                        blocker = await db.scalar(text("SELECT pg_backend_pid()"))
                        if phase == "pre_dispatch":
                            task = asyncio.create_task(dispatcher.dispatch_batch())
                        else:
                            save_response.set()
                        await root_snapshot_read.wait()
                        assert owner.calls == (0 if phase == "pre_dispatch" else 1)
                        await wait_for_block(sessions, dispatcher_pid, blocker)
                        current = await DrainRepository().current(db, line.id)
                        assert current is not None and current.result is None
                    assert task is not None
                    assert await task == 1
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            async with sessions() as db:
                row = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.workline_id == line.id))
                assert row.status == "COMPLETED"
                assert row.response_evidence_id is not None
                assert len(server.requests) == 1
    finally:
        server.close()


async def test_ctu01_serialized_submission_respects_capacity(rack_database):
    from src.app.execution.models import TransportDecisionBinding
    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, picking = await seed_line(db, bins=0, capacity=1)
            picking.status = "EXECUTING"
            picking.last_applied_plan_revision = 1
            picking.target_rack_id = f"TARGET-{line.id}"
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
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            driver = runtime.plugins[0].picking_task_batch_driver
            window = RackInboundWindowService()

            async def action(db, line):
                return await driver._submit_source_racks(db, line, picking)

            for index in range(3):
                assert await serialized_pair(sessions, line.id, action) == (1, 0)
                async with sessions() as db:
                    bindings = (
                        await db.scalars(
                            select(TransportDecisionBinding).where(
                                TransportDecisionBinding.workline_id == line.id,
                                TransportDecisionBinding.picking_task_id == picking.id,
                            )
                        )
                    ).all()
                    assert {binding.resource_fence_id for binding in bindings} == {
                        f"R{rack_index}-{line.id}" for rack_index in range(index + 1)
                    }
                    assert [
                        binding.resource_fence_id for binding in bindings if binding.window_released_at is None
                    ] == [f"R{index}-{line.id}"]
                if index < 2:
                    async with sessions.begin() as db:
                        departure_id = f"departure-{index}-{line.id}"
                        assert await window.attach_departure(
                            db, workline_id=line.id, rack_id=f"R{index}-{line.id}", client_request_id=departure_id
                        )
                        assert await window.release_on_departure_accepted(db, client_request_id=departure_id)
            assert await serialized_pair(sessions, line.id, action) == (0, 0)
    finally:
        server.close()


async def test_source_rack_priority_matches_spec_a_through_e_scenario(rack_database):
    """R3 端到端：capacity=3 时 A~E 场景走真实容量窗口，D/E 首次候选始终优先于 B 的重试候选。

    对照 docs/superpowers/specs/2026-09-25-rack-cancel-retry-capacity-design.md 第 3 节场景基准。
    """
    from manual_picking.application.batch_driver import pending_first_source_candidates

    from src.app.execution.models import TransportDecisionBinding
    from src.app.execution.services.rack_inbound_window import RackInboundWindowService
    from src.app.transport.contracts import TransportHandle
    from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack

    _, sessions = rack_database
    server = BusinessServer()
    server.start()

    def rack(name):
        return f"{name}-{line.id}"

    try:
        async with sessions.begin() as db:
            line, picking = await seed_line(db, bins=0, capacity=3)
            picking.status = "EXECUTING"
            picking.last_applied_plan_revision = 1
            picking.target_rack_id = f"TARGET-{line.id}"
            picking.target_rack_face = "90"
            picking.initial_plan_evidence_id = picking.issued_evidence_id
            picking.last_plan_evidence_id = picking.issued_evidence_id
            for name in ("A", "B", "C", "D", "E"):
                db.add(
                    PickingTaskBinSourceRack(
                        picking_task_id=picking.id,
                        rack_id=rack(name),
                        rack_face="90",
                        plan_revision=1,
                        source_evidence_id=picking.issued_evidence_id,
                    )
                )
        async with runtime_for(sessions, server.url) as (runtime, _transport):
            driver = runtime.plugins[0].picking_task_batch_driver
            window = RackInboundWindowService()

            async def pending_names(db):
                rows = await pending_first_source_candidates(
                    db, driver._plans, driver._bindings, workline_id=line.id, task=picking
                )
                return sorted(row.rack_id for row in rows)

            async def release(db, name):
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.workline_id == line.id,
                        TransportDecisionBinding.resource_fence_id == rack(name),
                    )
                )
                assert await window.release_unarrived_terminal(
                    db, Mock(client_request_id=binding.client_request_id, status="REJECTED")
                )

            # 初始 | A/B/C 首次候选进场，D/E 因容量已满暂缓。
            async with sessions.begin() as db:
                assert await driver._submit_source_racks(db, line, picking) == 3

            # B 取消并释放：终位不在目标点。B 已有绑定，不再是首次候选，变成重试候选。
            async with sessions.begin() as db:
                await release(db, "B")
                assert await pending_names(db) == [rack("D"), rack("E")]  # 重试候选 B 不在其中
                assert await driver._submit_source_racks(db, line, picking) == 1  # 名额先给未决首次候选 D

            # B 的重试此时必须让路：还有未决首次候选 E。
            async with sessions.begin() as db:
                assert await pending_names(db) == [rack("E")]

            # C 释放名额：轮到 E 补位；此后已无未决首次候选，B 的重试才轮得到。
            async with sessions.begin() as db:
                await release(db, "C")
                assert await driver._submit_source_racks(db, line, picking) == 1
            async with sessions.begin() as db:
                assert await pending_names(db) == []  # 首次候选耗尽，B 的重试不再被挡

            # A 释放，B（重试候选）终于可以补位——镜像 _retry_terminal_rack 的窗口准入调用。
            async with sessions.begin() as db:
                await release(db, "A")

                async def create():
                    request_id = f"retry-{rack('B')}"
                    db.add(
                        TransportDecisionBinding(
                            workline_id=line.id,
                            picking_task_id=picking.id,
                            source_evidence_id=picking.issued_evidence_id,
                            correlation_id=f"retry:1:{rack('B')}",
                            step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                            resource_fence_id=rack("B"),
                            client_request_id=request_id,
                        )
                    )
                    await db.flush()
                    return TransportHandle(f"transport:{request_id}", request_id)

                assert (
                    await window.admit(
                        db,
                        workline_id=line.id,
                        workline_code=line.line_code,
                        target_location_code=line.position_bindings["FIVE_RACK"]["location_id"],
                        rack_id=rack("B"),
                        picking_task_id=picking.id,
                        create=create,
                    )
                    == "CREATED"
                )
    finally:
        server.close()


async def test_due_source_retry_order_uses_active_member_order(rack_database):
    from manual_picking.application.batch_repository import BatchRepository

    from src.app.execution.models import InboundEvidenceApplyStatus, TransportDecisionBinding
    from src.app.transport.models import TransportTask
    from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack

    _, sessions = rack_database
    now = timezone.now_for_db()
    async with sessions.begin() as db:
        line, picking = await seed_line(db, bins=0, capacity=1)
        picking.status = "EXECUTING"
        picking.last_applied_plan_revision = 1
        source_evidence_id = picking.issued_evidence_id
        picking.target_rack_id = f"TARGET-{line.id}"
        picking.target_rack_face = "90"
        picking.initial_plan_evidence_id = source_evidence_id
        picking.last_plan_evidence_id = source_evidence_id
        target = line.position_bindings["FIVE_RACK"]["location_id"]
        for rank, rack_id in enumerate((f"A-{line.id}", f"B-{line.id}"), start=1):
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=picking.id,
                    rack_id=rack_id,
                    rack_face="90",
                    plan_revision=1,
                    source_evidence_id=source_evidence_id,
                )
            )
            client_request_id = f"retry-order-{rank}-{line.id}"
            transport_task_id = f"retry-order-task-{rank}-{line.id}"
            db.add(
                TransportDecisionBinding(
                    workline_id=line.id,
                    picking_task_id=picking.id,
                    correlation_id=f"pt:{picking.id}:rack:{rack_id}",
                    step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                    resource_fence_id=rack_id,
                    source_evidence_id=source_evidence_id,
                    client_request_id=client_request_id,
                    window_target_location_code=target,
                    window_released_at=now,
                )
            )
            db.add(
                TransportTask(
                    transport_task_id=transport_task_id,
                    client_request_id=client_request_id,
                    request_digest="a" * 64,
                    kind="RACK_MOVE",
                    caller_json={"workline_id": str(line.id)},
                    request_json={"rack_id": rack_id, "target": {"kind": "RACK_POSITION", "location_code": target}},
                    submit_operation_id=f"submit-{rank}-{line.id}",
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="b" * 64,
                    status="FAILED",
                    authority_workline_id=line.id,
                    outcome_version=1,
                    published_outcome_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                InboundEvidence(
                    kind="TRANSPORT_RESULT",
                    source_identity=f"transport:{transport_task_id}:outcome:1",
                    payload_digest="c" * 64,
                    normalized_payload={"status": "FAILED"},
                    received_at=now,
                    workline_id=line.id,
                    transport_task_id=transport_task_id,
                    apply_status=InboundEvidenceApplyStatus.APPLIED,
                    decision_next_attempt_at=now - timedelta(seconds=1),
                )
            )
        await db.flush()
        repository = BatchRepository()

        assert await repository.has_earlier_due_source_retry(
            db,
            workline_id=line.id,
            picking_task_id=picking.id,
            source_evidence_id=source_evidence_id,
            rack_id=f"B-{line.id}",
            last_applied_plan_revision=1,
            now=now,
        )
        assert not await repository.has_earlier_due_source_retry(
            db,
            workline_id=line.id,
            picking_task_id=picking.id,
            source_evidence_id=source_evidence_id,
            rack_id=f"A-{line.id}",
            last_applied_plan_revision=1,
            now=now,
        )

        db.add(
            TransportDecisionBinding(
                workline_id=line.id,
                picking_task_id=picking.id,
                correlation_id=f"active:{line.id}",
                step="PICKING_TASK_BIN_SOURCE_RACK_IN",
                resource_fence_id=f"A-{line.id}",
                source_evidence_id=source_evidence_id,
                client_request_id=f"active-window-{line.id}",
                window_target_location_code=target,
            )
        )
        await db.flush()
        assert not await repository.has_earlier_due_source_retry(
            db,
            workline_id=line.id,
            picking_task_id=picking.id,
            source_evidence_id=source_evidence_id,
            rack_id=f"B-{line.id}",
            last_applied_plan_revision=1,
            now=now,
        )


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


async def _active_drain(runtime, sessions, line, *, racks=None):
    from test_drain_execution_postgresql import seed_ready

    async with sessions.begin() as db:
        locked = await locked_line(db, line.id)
        driver = runtime.plugins[0].picking_task_batch_driver
        decision = await seed_ready(db, locked, racks=racks)
        execution = await driver._drain._drain_executions.ensure_ready_in_session(db, decision=decision)
        await driver._submit_drain_racks(db, locked, decision)
    return decision, execution


async def _drain_transports(db, line_id, evidence_id):
    from src.app.transport.models import TransportTask

    return tuple(
        (
            await db.scalars(
                select(TransportTask)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportDecisionBinding.workline_id == line_id,
                    TransportDecisionBinding.source_evidence_id == evidence_id,
                )
                .order_by(TransportTask.client_request_id)
            )
        ).all()
    )


async def test_independent_task_prepares_while_old_drain_is_active(rack_database):
    from manual_picking.application.drain_execution_model import ManualPickingDrainExecution

    _, sessions = rack_database
    async with sessions.begin() as db:
        line, _ = await seed_line(db)
    async with runtime_for(sessions, "http://127.0.0.1:9") as (runtime, _transport):
        _, execution = await _active_drain(runtime, sessions, line)
        async with sessions.begin() as db:
            queued = await seed_task(db, line, queued=True)
        runtime.picking_task_prepare_service._task_queue = Mock()
        assert await runtime.picking_task_prepare_service.prepare_batch() == 1
        async with sessions() as db:
            assert (await db.get(PickingTask, queued.id)).status == "PREPARING"
            assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "ACTIVE"
            assert await db.scalar(
                select(WmsConfirmation.id).where(
                    WmsConfirmation.picking_task_id == queued.id,
                    WmsConfirmation.operation == "outbound.picking_task.prepare@v1",
                )
            )


async def test_plan_and_facts_are_not_blocked_by_manual_drain(rack_database):
    from manual_picking.application.drain_execution_model import ManualPickingDrainExecution

    from src.app.transport.models import TransportTask
    from src.app.wms_integration.outbound_picking.models.plan_members import PickingTaskBinSourceRack
    from src.core.uuid7 import new_uuid7
    from src.utils.canonical_json import canonical_json_digest
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, picking = await seed_line(db, bins=0, capacity=2)
            identity, now = new_uuid7(), timezone.now_for_db()
            payload = {"data": {"task_id": picking.task_id}}
            plan = InboundEvidence(
                kind="WMS_EVENT",
                source_identity=f"outbound.picking_task.plan_delta@v1:{identity}",
                operation="outbound.picking_task.plan_delta@v1",
                operation_id=identity,
                workline_id=line.id,
                normalized_payload=payload,
                payload_digest=canonical_json_digest(payload),
                received_at=now,
                processed_at=now,
                published_at=now,
                decision_digest=canonical_json_digest({"plan": identity}),
                apply_status="APPLIED",
            )
            db.add(plan)
            await db.flush()
            picking.status, picking.last_applied_plan_revision = "EXECUTING", 1
            picking.initial_plan_evidence_id = picking.last_plan_evidence_id = plan.id
            source_rack = f"INDEPENDENT-{line.id}"
            db.add(
                PickingTaskBinSourceRack(
                    picking_task_id=picking.id,
                    rack_id=source_rack,
                    rack_face="90",
                    plan_revision=1,
                    source_evidence_id=plan.id,
                )
            )
        async with runtime_for(sessions, server.url) as (runtime, transport):
            _, execution = await _active_drain(runtime, sessions, line)
            assert await runtime.picking_task_plan_activation_service.activate_batch() == 1
            async with sessions() as db:
                source = await db.scalar(
                    select(TransportTask)
                    .join(
                        TransportDecisionBinding,
                        TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                    )
                    .where(
                        TransportDecisionBinding.picking_task_id == picking.id,
                        TransportDecisionBinding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                    )
                )
                assert source is not None
            assert await transport.service.submit_pending_tasks(100) == 2
            ack = await record_valid_callback(
                transport.service,
                operation_id=new_uuid7(),
                transport_task_id=source.transport_task_id,
                operation="transport.task.resulted@v1",
                timestamp=1,
                payload={
                    "kind": "RACK_MOVE",
                    "rack_id": source_rack,
                    "outcome_revision": 1,
                    "status": "SUCCEEDED",
                    "arrival_face": "90",
                    "final_position": source.request_json["target"],
                },
            )
            assert ack["code"] == "RECEIVED"
            assert await transport.service.process_pending_evidence(100) == 1
            runtime.transport_outcome_publisher._queue = Mock()
            assert await transport.service.publish_pending_outcomes(100, runtime.transport_outcome_publisher) >= 1
            await runtime.execution.fact_processor.process_batch(100)
            async with sessions() as db:
                evidence = await db.scalar(
                    select(InboundEvidence).where(
                        InboundEvidence.transport_task_id == source.transport_task_id,
                        InboundEvidence.kind == "TRANSPORT_RESULT",
                    )
                )
                assert evidence is not None and evidence.published_at is not None
                assert evidence.apply_status == "APPLIED"
                assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "ACTIVE"
    finally:
        server.close()


@pytest.mark.parametrize("change", ["cancelled", "arriving_result"])
async def test_retirement_rechecks_cancelled_task_and_arriving_result(rack_database, change):
    from manual_picking.application.drain_execution_model import ManualPickingDrainExecution

    from src.core.uuid7 import new_uuid7
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db)
            queued = await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            decision, execution = await _active_drain(runtime, sessions, line)
            assert await transport.service.submit_pending_tasks(100) == 1
            async with sessions() as db:
                original = (await _drain_transports(db, line.id, decision.evidence_id))[0]
                overdue = original.result_deadline_at + timedelta(microseconds=1)
            waiting, waiter_pid = asyncio.Event(), None
            flow = runtime.plugins[0].picking_task_batch_driver._drain

            async def scanner():
                nonlocal waiter_pid
                async with sessions.begin() as db:
                    waiter_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                    waiting.set()
                    locked = await locked_line(db, line.id)
                    return await flow.retire_exception_in_session(db, locked, overdue)

            async with sessions.begin() as db:
                await locked_line(db, line.id)
                blocker = await db.scalar(text("SELECT pg_backend_pid()"))
                scan = asyncio.create_task(scanner())
                try:
                    await waiting.wait()
                    await wait_for_block(sessions, waiter_pid, blocker)
                    if change == "cancelled":
                        task = await db.get(PickingTask, queued.id)
                        task.status = "CANCELLED"
                    else:
                        ack = await record_valid_callback(
                            transport.service,
                            operation_id=new_uuid7(),
                            transport_task_id=original.transport_task_id,
                            operation="transport.task.resulted@v1",
                            timestamp=1,
                            payload={
                                "kind": "RACK_MOVE",
                                "rack_id": original.request_json["rack_id"],
                                "outcome_revision": 1,
                                "status": "SUCCEEDED",
                                "arrival_face": "90",
                                "final_position": original.request_json["target"],
                            },
                        )
                        assert ack["code"] == "RECEIVED"
                except BaseException:
                    scan.cancel()
                    await asyncio.gather(scan, return_exceptions=True)
                    raise
            assert await scan is False
            if change == "arriving_result":
                # 宿主应用确定事实，现有持久扫描再重验；不会凭旧 deadline 退役。
                assert await transport.service.process_pending_evidence(100) == 1
                assert await runtime.picking_task_plan_activation_service.activate_batch() >= 0
            async with sessions() as db:
                assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "ACTIVE"
                original_now = (await _drain_transports(db, line.id, decision.evidence_id))[0]
                assert original_now.status == ("SUCCEEDED" if change == "arriving_result" else "ACCEPTED")
    finally:
        server.close()


async def test_duplicate_scans_close_once_with_stable_reason(rack_database):
    from manual_picking.application.drain_execution_model import ManualPickingDrainExecution
    from wes_plugin_sdk import RackFaceSequence

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db, capacity=2)
            queued = await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            decision, execution = await _active_drain(
                runtime,
                sessions,
                line,
                racks=(
                    RackFaceSequence("R1", ("90", "270")),
                    RackFaceSequence("R2", ("270", "90")),
                    RackFaceSequence("R3", ("90",)),
                ),
            )
            assert await transport.service.submit_pending_tasks(100) == 2
            async with sessions() as db:
                originals = await _drain_transports(db, line.id, decision.evidence_id)
                frozen = [
                    (row.transport_task_id, row.client_request_id, row.submit_request_body, row.status)
                    for row in originals
                ]
                overdue = max(row.result_deadline_at for row in originals) + timedelta(microseconds=1)
            flow = runtime.plugins[0].picking_task_batch_driver._drain

            async def action(db, locked):
                return await flow.retire_exception_in_session(db, locked, overdue)

            assert await serialized_pair(sessions, line.id, action) == (True, False)
            async with sessions() as db:
                retired = await db.get(ManualPickingDrainExecution, execution.id)
                assert retired.status == "EXCEPTION_RETIRED" and retired.closed_at == overdue
                assert retired.reason_code == "DRAIN_RESULT_OVERDUE"
                assert retired.blocker_client_request_id == min(row.client_request_id for row in originals)
                assert retired.trigger_picking_task_id == queued.id
                bindings = (
                    await db.scalars(
                        select(TransportDecisionBinding).where(
                            TransportDecisionBinding.source_evidence_id == decision.evidence_id,
                        )
                    )
                ).all()
                assert len(bindings) == 2 and all(row.window_released_at == overdue for row in bindings)
            # 重建真实装配后扫描不重开原 execution，也不补原 R3/旋转/返料/离场。
            async with runtime_for(sessions, server.url) as (restarted, _):
                await restarted.picking_task_plan_activation_service.activate_batch()
            async with sessions() as db:
                after = await _drain_transports(db, line.id, decision.evidence_id)
                assert [
                    (row.transport_task_id, row.client_request_id, row.submit_request_body, row.status) for row in after
                ] == frozen
                assert (await db.get(ManualPickingDrainExecution, execution.id)).reason_code == "DRAIN_RESULT_OVERDUE"
    finally:
        server.close()


@pytest.mark.parametrize("change", ["cancelled", "arriving_result"])
async def test_retirement_before_cancel_or_result_stays_closed(rack_database, change):
    from manual_picking.application.drain_execution_model import ManualPickingDrainExecution

    from src.core.uuid7 import new_uuid7
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db)
            queued = await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            decision, execution = await _active_drain(runtime, sessions, line)
            assert await transport.service.submit_pending_tasks(100) == 1
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                original = (await _drain_transports(db, line.id, decision.evidence_id))[0]
                assert await runtime.plugins[0].picking_task_batch_driver._drain.retire_exception_in_session(
                    db,
                    locked,
                    original.result_deadline_at + timedelta(microseconds=1),
                )
            if change == "cancelled":
                async with sessions.begin() as db:
                    await locked_line(db, line.id)
                    (await db.get(PickingTask, queued.id)).status = "CANCELLED"
            else:
                ack = await record_valid_callback(
                    transport.service,
                    operation_id=new_uuid7(),
                    transport_task_id=original.transport_task_id,
                    operation="transport.task.resulted@v1",
                    timestamp=1,
                    payload={
                        "kind": "RACK_MOVE",
                        "rack_id": original.request_json["rack_id"],
                        "outcome_revision": 1,
                        "status": "SUCCEEDED",
                        "arrival_face": "90",
                        "final_position": original.request_json["target"],
                    },
                )
                assert ack["code"] == "RECEIVED"
                assert await transport.service.process_pending_evidence(100) == 1
            await runtime.picking_task_plan_activation_service.activate_batch()
            async with sessions() as db:
                assert (await db.get(ManualPickingDrainExecution, execution.id)).status == "EXCEPTION_RETIRED"
                after = await _drain_transports(db, line.id, decision.evidence_id)
                assert len(after) == 1
                assert after[0].client_request_id == original.client_request_id
                assert after[0].submit_request_body == original.submit_request_body
    finally:
        server.close()


async def test_pre_return_membership_retirement_keeps_unknown_original_inbound_dependency(rack_database):
    from manual_picking.application.drain_execution_model import ManualPickingDrainReturnMember
    from test_drain_execution_postgresql import seed_ready
    from wes_plugin_sdk import RackFaceSequence

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db, capacity=2)
            queued = await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            old, execution = await _active_drain(runtime, sessions, line)
            assert await transport.service.submit_pending_tasks(100) == 1
            driver = runtime.plugins[0].picking_task_batch_driver
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                original = (await _drain_transports(db, line.id, old.evidence_id))[0]
                assert not await db.scalar(
                    select(ManualPickingDrainReturnMember.id).where(
                        ManualPickingDrainReturnMember.execution_id == execution.id,
                    )
                )
                assert await driver._drain.retire_exception_in_session(
                    db,
                    locked,
                    original.result_deadline_at + timedelta(microseconds=1),
                )
            runtime.picking_task_prepare_service._task_queue = Mock()
            assert await runtime.picking_task_prepare_service.prepare_batch() == 1
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                fresh = await seed_ready(
                    db,
                    locked,
                    racks=(
                        RackFaceSequence("R1", ("90",)),
                        RackFaceSequence("R2", ("90",)),
                    ),
                )
                await driver._drain._drain_executions.ensure_ready_in_session(db, decision=fresh)
                assert await driver._submit_drain_racks(db, locked, fresh) == 1
                assert await driver._submit_drain_racks(db, locked, fresh) == 0
            async with runtime_for(sessions, server.url) as (restarted, _):
                await restarted.picking_task_plan_activation_service.activate_batch()
            async with sessions() as db:
                current = await _drain_transports(db, line.id, fresh.evidence_id)
                assert [row.request_json["rack_id"] for row in current] == ["R2"]
                prior = await _drain_transports(db, line.id, old.evidence_id)
                assert len(prior) == 1 and prior[0].client_request_id == original.client_request_id
                assert prior[0].submit_request_body == original.submit_request_body and prior[0].status == "ACCEPTED"
                assert (await db.get(PickingTask, queued.id)).status == "PREPARING"
    finally:
        server.close()


@pytest.mark.parametrize("change", ["terminal", "target", "face", "unknown"])
async def test_fresh_drain_does_not_fence_definite_or_non_equivalent_inbound(rack_database, change):
    from test_drain_execution_postgresql import seed_ready
    from wes_plugin_sdk import RackFaceSequence

    from src.app.transport.models import TransportMember
    from src.app.wms_adapter.transport_wire import validate_callback_envelope
    from src.core.uuid7 import new_uuid7
    from tests.support.transport_callbacks import record_valid_callback

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db, capacity=2)
            await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            old, _execution = await _active_drain(runtime, sessions, line)
            assert await transport.service.submit_pending_tasks(100) == 1
            driver = runtime.plugins[0].picking_task_batch_driver
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                original = (await _drain_transports(db, line.id, old.evidence_id))[0]
                assert await driver._drain.retire_exception_in_session(
                    db,
                    locked,
                    original.result_deadline_at + timedelta(microseconds=1),
                )
            stopped_position = {"kind": "RACK_POSITION", "location_code": "STOPPED-R1"}
            if change in {"terminal", "unknown"}:
                # 权威失败结果明确停在原目标之外；未知位置继续保留原动作依赖。
                result_position = (
                    {"final_position": stopped_position}
                    if change == "terminal"
                    else {
                        "position_unknown": True,
                    }
                )
                message = validate_callback_envelope(
                    {
                        "operation_id": new_uuid7(),
                        "operation": "transport.task.resulted@v1",
                        "timestamp": 1,
                        "data": {
                            "transport_task_id": original.transport_task_id,
                            "kind": "RACK_MOVE",
                            "rack_id": "R1",
                            "outcome_revision": 1,
                            "status": "FAILED",
                            "failure_code": "RCS_EXECUTION_FAILED" if change == "terminal" else "POSITION_UNKNOWN",
                            **result_position,
                        },
                    }
                )
                ack = await record_valid_callback(
                    transport.service,
                    operation_id=message["operation_id"],
                    transport_task_id=message["data"]["transport_task_id"],
                    operation=message["operation"],
                    timestamp=message["timestamp"],
                    payload=message["data"],
                )
                assert ack["code"] == "RECEIVED"
                assert await transport.service.process_pending_evidence(100) == 1
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                after = (await _drain_transports(db, line.id, old.evidence_id))[0]
                if change in {"terminal", "unknown"}:
                    member = await db.scalar(
                        select(TransportMember).where(
                            TransportMember.transport_task_id == original.transport_task_id,
                            TransportMember.object_id == "R1",
                        )
                    )
                    assert member is not None and member.status == "FAILED"
                    assert member.failure_code == (
                        "RCS_EXECUTION_FAILED" if change == "terminal" else "POSITION_UNKNOWN"
                    )
                    if change == "terminal":
                        assert after.status == "FAILED" and after.reason_code == "RCS_EXECUTION_FAILED"
                        assert member.final_position_json == stopped_position and not member.position_unknown
                        assert member.final_position_json != original.request_json["target"]
                    else:
                        assert after.status == "RECONCILING" and after.reason_code == "TRANSPORT_POSITION_UNKNOWN"
                        assert member.final_position_json is None and member.position_unknown
                assert await driver._drain.repository.has_retired_unclosed_inbound(
                    db,
                    workline_id=line.id,
                    rack_id="R1",
                    target_location_code=original.request_json["target"]["location_code"],
                    target_face=original.request_json["target_face"],
                ) is (change != "terminal")
                if change == "target":
                    assert not await driver._drain.repository.has_retired_unclosed_inbound(
                        db,
                        workline_id=line.id,
                        rack_id="R1",
                        target_location_code="OTHER",
                        target_face="90",
                    )
                else:
                    fresh = await seed_ready(
                        db, locked, racks=(RackFaceSequence("R1", ("270" if change == "face" else "90",)),)
                    )
                    await driver._drain._drain_executions.ensure_ready_in_session(db, decision=fresh)
                    assert await driver._submit_drain_racks(db, locked, fresh) == (0 if change == "unknown" else 1)
                    assert await driver._submit_drain_racks(db, locked, fresh) == 0
                    fresh_actions = await _drain_transports(db, line.id, fresh.evidence_id)
                    assert len(fresh_actions) == (0 if change == "unknown" else 1)
                after = (await _drain_transports(db, line.id, old.evidence_id))[0]
                assert after.client_request_id == original.client_request_id
                assert after.submit_request_body == original.submit_request_body
    finally:
        server.close()


async def test_strict_source_plan_keeps_retired_unknown_inbound_identity_after_window_release(rack_database):
    from src.app.transport.models import TransportTask
    from src.app.wms_integration.outbound_picking.composition import build_outbound_picking_runtime
    from src.core.uuid7 import new_uuid7

    _, sessions = rack_database
    server = BusinessServer()
    server.start()
    try:
        async with sessions.begin() as db:
            line, _ = await seed_line(db, capacity=2)
            queued = await seed_task(db, line, queued=True)
        async with runtime_for(sessions, server.url) as (runtime, transport):
            old, execution = await _active_drain(runtime, sessions, line)
            assert await transport.service.submit_pending_tasks(100) == 1
            driver = runtime.plugins[0].picking_task_batch_driver
            async with sessions.begin() as db:
                locked = await locked_line(db, line.id)
                original = (await _drain_transports(db, line.id, old.evidence_id))[0]
                assert original.status == "ACCEPTED"
                assert await driver._drain.retire_exception_in_session(
                    db, locked, original.result_deadline_at + timedelta(microseconds=1)
                )
                binding = await db.scalar(
                    select(TransportDecisionBinding).where(
                        TransportDecisionBinding.client_request_id == original.client_request_id
                    )
                )
                assert binding.window_released_at is not None
            runtime.picking_task_prepare_service._task_queue = Mock()
            assert await runtime.picking_task_prepare_service.prepare_batch() == 1
            assert await runtime.execution.wms_confirmation_service.dispatch_batch() == 1
            async with sessions() as db:
                prepare = await db.scalar(
                    select(WmsConfirmation).where(
                        WmsConfirmation.picking_task_id == queued.id,
                        WmsConfirmation.operation == "outbound.picking_task.prepare@v1",
                    )
                )
                assert prepare.status == "COMPLETED"
                accepted = await db.get(InboundEvidence, prepare.response_evidence_id)
                assert accepted.normalized_payload["code"] == "PREPARE_ACCEPTED"
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
            plan_id = new_uuid7()
            ack = await outbound.picking_task_plan_delta_handler.handle(
                {
                    "operation_id": plan_id,
                    "operation": "outbound.picking_task.plan_delta@v1",
                    "timestamp": 1,
                    "data": {
                        "task_id": queued.task_id,
                        "plan_revision": 1,
                        "added_bin_source_racks": [{"rack_id": rack, "rack_face": ["90"]} for rack in ("R1", "R2")],
                    },
                }
            )
            assert (ack.http_status, ack.body["code"]) == (202, "RECEIVED")
            async with sessions() as db:
                planned = await db.get(PickingTask, queued.id)
                assert planned.status == "EXECUTING" and planned.last_applied_plan_revision == 1
                evidence = await db.get(InboundEvidence, planned.last_plan_evidence_id)
                assert evidence.operation_id == plan_id and evidence.apply_status == "APPLIED"
            await runtime.picking_task_plan_activation_service.activate_batch()
            async with runtime_for(sessions, server.url) as (restarted, _):
                await restarted.picking_task_plan_activation_service.activate_batch()
            async with sessions() as db:
                sources = (
                    await db.scalars(
                        select(TransportTask)
                        .join(
                            TransportDecisionBinding,
                            TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                        )
                        .where(
                            TransportDecisionBinding.picking_task_id == queued.id,
                            TransportDecisionBinding.step == "PICKING_TASK_BIN_SOURCE_RACK_IN",
                        )
                    )
                ).all()
                assert [row.request_json["rack_id"] for row in sources] == ["R2"]
                prior = await _drain_transports(db, line.id, old.evidence_id)
                assert len(prior) == 1 and prior[0].status == "ACCEPTED"
                assert prior[0].client_request_id == original.client_request_id
                assert prior[0].transport_task_id == original.transport_task_id
                assert prior[0].request_json == original.request_json
                assert prior[0].submit_request_body == original.submit_request_body
                retired = await db.get(type(execution), execution.id)
                assert retired.status == "EXCEPTION_RETIRED"
    finally:
        server.close()
