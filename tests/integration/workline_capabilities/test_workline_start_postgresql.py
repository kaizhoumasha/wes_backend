"""WorkLine START 的 PostgreSQL 事务、锁与直接替换证据。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.device import Device
from src.app.effect_ledger_status import SystemOutboxStatus
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldStatus, RuntimeHoldType
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
)
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.workline_start_service import (
    WorkLineStartIdempotencyConflictError,
    WorkLineStartService,
)
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

pytestmark = pytest.mark.integration

RETIRED_COLUMNS = {
    "start_admission_status",
    "start_admission_message",
    "start_admission_failed_device_code",
    "start_admission_checked_at",
    "last_start_request_id",
    "last_start_trace_id",
}


@dataclass
class Builder:
    device_by_workline: dict[int, int]
    calls: list[int] = field(default_factory=list)

    async def build(self, _db: object, workline: WorkLine) -> WorkLineEpochActivationPlan:
        assert workline.id is not None
        self.calls.append(workline.id)
        device_id = self.device_by_workline[workline.id]
        return WorkLineEpochActivationPlan(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            flow_mode="GENERIC_FLOW",
            configuration_snapshot={"workline_id": workline.id},
            device_bindings=(
                LineRunEpochDeviceBindingInput(
                    device_id=device_id,
                    device_code=f"START-PG-DEVICE-{workline.id}",
                    device_role="DEVICE_ROLE",
                    endpoint_base_url="http://ecs-start-pg:8080",
                    contract_key="generic.contract",
                    contract_version="1.0",
                    status_max_age_ms=1_000,
                    command_timeout_ms=5_000,
                ),
            ),
            position_bindings=(
                LineRunEpochPositionBindingInput(
                    position_role="INPUT_POSITION",
                    location_id=f"LOCATION-{workline.id}",
                    location_type="RACK_CELL",
                ),
            ),
        )


async def _seed_workline(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> tuple[int, int]:
    async with session_factory.begin() as db:
        workline = WorkLine(
            line_code=f"START-PG-{suffix}",
            line_name=f"START PostgreSQL {suffix}",
            line_type=LineType.AUTO,
            is_active=True,
        )
        db.add(workline)
        await db.flush()
        assert workline.id is not None
        device = Device(
            device_code=f"START-PG-DEVICE-{workline.id}",
            device_name=f"START PostgreSQL Device {suffix}",
            work_line_id=workline.id,
            device_role="DEVICE_ROLE",
        )
        db.add(device)
        await db.flush()
        assert device.id is not None
        await workline_runtime_status_projection_service.ensure_default(db, workline_id=workline.id)
        return workline.id, device.id


def _parked_outbox(
    *,
    dispatch_key: str,
    workline_id: int,
    blocked_reason: str = "WORKLINE_STOPPED_WAITING_START",
    reconciliation_session_id: int | None = None,
    runtime_hold_id: int | None = None,
) -> SystemOutbox:
    return SystemOutbox(
        workline_id=workline_id,
        operation_domain="WORKLINE",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="START-PG",
        provider_profile_identity="internal.start-pg.v1",
        operation_identity="workline.start-pg@v1",
        payload_json={},
        status=SystemOutboxStatus.RETRY_WAIT,
        blocked_workline_id=workline_id,
        blocked_reason=blocked_reason,
        blocked_by_reconciliation_session_id=reconciliation_session_id,
        blocked_by_runtime_hold_id=runtime_hold_id,
    )


def test_workline_start_migration_drops_legacy_columns_from_package_one_head() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "53e560430c1a", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            try:
                async with engine.connect() as connection:
                    package_one_version = await connection.scalar(
                        text("SELECT version_num FROM wes_sys.alembic_version")
                    )
                    package_one_columns = set(
                        await connection.scalars(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'wes_biz' AND table_name = 'work_lines'"
                            )
                        )
                    )
                assert package_one_version == "53e560430c1a"
                assert package_one_columns >= RETIRED_COLUMNS

                run_alembic("upgrade", "a05b2676f681", database_url=database_url)
                async with engine.connect() as connection:
                    package_two_version = await connection.scalar(
                        text("SELECT version_num FROM wes_sys.alembic_version")
                    )
                    package_two_columns = set(
                        await connection.scalars(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'wes_biz' AND table_name = 'work_lines'"
                            )
                        )
                    )
                assert package_two_version == "a05b2676f681"
                assert RETIRED_COLUMNS.isdisjoint(package_two_columns)
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_workline_start_is_atomic_replay_first_and_serialized_by_request_identity() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    columns = set(
                        await db.scalars(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'wes_biz' AND table_name = 'work_lines'"
                            )
                        )
                    )
                assert RETIRED_COLUMNS.isdisjoint(columns)

                workline_id, device_id = await _seed_workline(session_factory, "LIFECYCLE")
                builder = Builder({workline_id: device_id})
                service = WorkLineStartService(plan_builder=builder)
                async with session_factory.begin() as db:
                    resolved_hold = RuntimeHold(
                        hold_type=RuntimeHoldType.MANUAL_HOLD,
                        status=RuntimeHoldStatus.RESOLVED,
                        blocking=False,
                        workline_id=workline_id,
                        source_kind="START_PG_TEST",
                        source_reason="resolved before START",
                        source_idempotency_key="START-PG-RESOLVED-HOLD",
                    )
                    db.add(resolved_hold)
                    await db.flush()
                    assert resolved_hold.id is not None
                    db.add_all(
                        [
                            _parked_outbox(dispatch_key="start-pg-eligible", workline_id=workline_id),
                            _parked_outbox(
                                dispatch_key="start-pg-reconciliation",
                                workline_id=workline_id,
                                reconciliation_session_id=91,
                            ),
                            _parked_outbox(
                                dispatch_key="start-pg-runtime-hold",
                                workline_id=workline_id,
                                runtime_hold_id=resolved_hold.id,
                            ),
                            _parked_outbox(
                                dispatch_key="start-pg-other-reason",
                                workline_id=workline_id,
                                blocked_reason="DEVICE_BUSY",
                            ),
                        ]
                    )

                async with session_factory.begin() as db:
                    started = await service.start(db, workline_id=workline_id, request_id="START-PG-LIFECYCLE")
                assert started.created is True
                assert started.released_outbox_count == 1
                assert started.current_workline_runtime_status == "READY"

                async with session_factory() as db:
                    outboxes = {
                        item.dispatch_key: item
                        for item in await db.scalars(
                            select(SystemOutbox).where(SystemOutbox.workline_id == workline_id)
                        )
                    }
                    assert outboxes["start-pg-eligible"].blocked_reason is None
                    assert outboxes["start-pg-reconciliation"].blocked_reason == "WORKLINE_STOPPED_WAITING_START"
                    assert outboxes["start-pg-runtime-hold"].blocked_reason == "WORKLINE_STOPPED_WAITING_START"
                    assert outboxes["start-pg-runtime-hold"].blocked_by_runtime_hold_id == resolved_hold.id
                    assert outboxes["start-pg-other-reason"].blocked_reason == "DEVICE_BUSY"
                    snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
                        db,
                        workline_id=workline_id,
                    )
                    assert snapshot.runtime_status == "READY"

                async with session_factory.begin() as db:
                    epoch = await db.scalar(
                        select(LineRunEpoch).where(LineRunEpoch.epoch_code == "START-PG-LIFECYCLE").with_for_update()
                    )
                    workline = await db.get(WorkLine, workline_id, with_for_update=True)
                    assert epoch is not None and workline is not None
                    epoch.status = LineRunEpochStatus.CLOSED
                    epoch.closed_at = datetime(2026, 8, 19, 12)
                    workline.is_deleted = True

                async with session_factory.begin() as db:
                    replay = await service.start(db, workline_id=workline_id, request_id="START-PG-LIFECYCLE")
                assert replay.created is False
                assert replay.epoch.status == LineRunEpochStatus.CLOSED.value
                assert builder.calls == [workline_id]

                rollback_line_id, rollback_device_id = await _seed_workline(session_factory, "ROLLBACK")
                rollback_service = WorkLineStartService(plan_builder=Builder({rollback_line_id: rollback_device_id}))
                async with session_factory.begin() as db:
                    db.add(_parked_outbox(dispatch_key="start-pg-rollback", workline_id=rollback_line_id))
                with pytest.raises(RuntimeError, match="rollback marker"):
                    async with session_factory.begin() as db:
                        await rollback_service.start(
                            db,
                            workline_id=rollback_line_id,
                            request_id="START-PG-ROLLBACK",
                        )
                        raise RuntimeError("rollback marker")
                async with session_factory() as db:
                    assert (
                        await db.scalar(select(LineRunEpoch).where(LineRunEpoch.epoch_code == "START-PG-ROLLBACK"))
                        is None
                    )
                    assert (await db.scalar(select(func.count()).select_from(LineRunEpochDeviceBinding))) == 1
                    assert (await db.scalar(select(func.count()).select_from(LineRunEpochPositionBinding))) == 1
                    rollback_outbox = await db.scalar(
                        select(SystemOutbox).where(SystemOutbox.dispatch_key == "start-pg-rollback")
                    )
                    assert rollback_outbox is not None
                    assert rollback_outbox.status == SystemOutboxStatus.RETRY_WAIT.value
                    assert rollback_outbox.blocked_reason == "WORKLINE_STOPPED_WAITING_START"
                    assert rollback_outbox.blocked_workline_id == rollback_line_id
                    snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
                        db,
                        workline_id=rollback_line_id,
                    )
                    assert snapshot.runtime_status == "STOPPED"

                serial_line_id, serial_device_id = await _seed_workline(session_factory, "SERIAL")
                serial_builder = Builder({serial_line_id: serial_device_id})
                serial_service = WorkLineStartService(plan_builder=serial_builder)

                async def wait_for_lock(backend_pid: int, task: asyncio.Task[object]) -> None:
                    deadline = asyncio.get_running_loop().time() + 10
                    last_wait_state: dict[str, object] | None = None
                    async with session_factory() as observer_db:
                        while True:
                            wait_state = (
                                (
                                    await observer_db.execute(
                                        text(
                                            "SELECT state, wait_event_type, wait_event "
                                            "FROM pg_stat_activity WHERE pid = :backend_pid"
                                        ),
                                        {"backend_pid": backend_pid},
                                    )
                                )
                                .mappings()
                                .one_or_none()
                            )
                            last_wait_state = dict(wait_state) if wait_state is not None else None
                            if last_wait_state is not None and last_wait_state["wait_event_type"] == "Lock":
                                return
                            if task.done():
                                pytest.fail(
                                    "competing START completed before PostgreSQL lock wait; "
                                    f"last_wait_state={last_wait_state!r}, exception={task.exception()!r}"
                                )
                            if asyncio.get_running_loop().time() >= deadline:
                                pytest.fail(
                                    "competing START did not enter PostgreSQL lock wait before deadline; "
                                    f"last_wait_state={last_wait_state!r}"
                                )
                            await observer_db.rollback()
                            await asyncio.sleep(0.01)

                async with session_factory() as first_db:
                    await first_db.begin()
                    first_serial = await serial_service.start(
                        first_db,
                        workline_id=serial_line_id,
                        request_id="START-PG-SERIAL",
                    )
                    second_serial_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

                    async def second_same_line_start() -> bool:
                        async with session_factory.begin() as db:
                            backend_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                            assert isinstance(backend_pid, int)
                            second_serial_pid.set_result(backend_pid)
                            result = await serial_service.start(
                                db,
                                workline_id=serial_line_id,
                                request_id="START-PG-SERIAL",
                            )
                            return result.created

                    second_serial_task = asyncio.create_task(second_same_line_start())
                    await wait_for_lock(
                        await asyncio.wait_for(second_serial_pid, timeout=2),
                        second_serial_task,
                    )
                    assert not second_serial_task.done()
                    await first_db.commit()
                    second_serial = await asyncio.wait_for(second_serial_task, timeout=2)

                assert sorted([first_serial.created, second_serial]) == [False, True]
                assert serial_builder.calls == [serial_line_id]

                left_id, left_device_id = await _seed_workline(session_factory, "CONFLICT-LEFT")
                right_id, right_device_id = await _seed_workline(session_factory, "CONFLICT-RIGHT")
                conflict_builder = Builder({left_id: left_device_id, right_id: right_device_id})
                conflict_service = WorkLineStartService(plan_builder=conflict_builder)

                async with session_factory() as first_db:
                    await first_db.begin()
                    first_conflict = await conflict_service.start(
                        first_db,
                        workline_id=left_id,
                        request_id="START-PG-CONFLICT",
                    )
                    second_conflict_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

                    async def second_cross_line_start() -> str:
                        async with session_factory.begin() as db:
                            backend_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                            assert isinstance(backend_pid, int)
                            second_conflict_pid.set_result(backend_pid)
                            try:
                                result = await conflict_service.start(
                                    db,
                                    workline_id=right_id,
                                    request_id="START-PG-CONFLICT",
                                )
                            except WorkLineStartIdempotencyConflictError:
                                return "CONFLICT"
                            return "CREATED" if result.created else "REPLAY"

                    second_conflict_task = asyncio.create_task(second_cross_line_start())
                    await wait_for_lock(
                        await asyncio.wait_for(second_conflict_pid, timeout=2),
                        second_conflict_task,
                    )
                    assert not second_conflict_task.done()
                    await first_db.commit()
                    second_conflict = await asyncio.wait_for(second_conflict_task, timeout=2)

                outcomes = ["CREATED" if first_conflict.created else "REPLAY", second_conflict]
                assert sorted(outcomes) == ["CONFLICT", "CREATED"]
                assert len(conflict_builder.calls) == 1
            finally:
                await engine.dispose()

    asyncio.run(scenario())
