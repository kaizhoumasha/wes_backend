"""BinTransitMembership 队列投影与 transition 事件测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.handling.models import (
    BinTransitMembership,
    BinTransitMembershipStatus,
    BinTransitQueue,
    HandlingMove,
    HandlingMoveStatus,
    HandlingObjectType,
    HandlingOperation,
    HandlingOperationStatus,
    HandlingStep,
    HandlingStepKind,
    HandlingStepStatus,
)
from src.app.handling.repositories import BinTransitMembershipRepository
from src.app.handling.services import BinTransitMembershipService, HandlingOperationLifecycleService
from src.app.runtime.orchestration.models import ObjectTransitionDomain, ObjectTransitionEvent
from src.database.sqlite_schema import configure_sqlite_schemas


class FakeTransitionService:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record_transition(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.events.append(kwargs)
        return SimpleNamespace(id=len(self.events), **kwargs)


class RaceConflictMembershipRepository(BinTransitMembershipRepository):
    """模拟 active membership 读写竞态，覆盖真实 repository IntegrityError 路径。"""

    def __init__(self) -> None:
        super().__init__()
        self._conflict_inserted = False

    async def get_active_by_bin_code(self, db: AsyncSession, bin_code: str) -> BinTransitMembership | None:
        if bin_code != "BIN-MEMBERSHIP-FLUSH-FAILS":
            return await super().get_active_by_bin_code(db, bin_code)
        if self._conflict_inserted:
            return None
        self._conflict_inserted = True
        db.add(
            BinTransitMembership(
                bin_code=bin_code,
                current_queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
                membership_status=BinTransitMembershipStatus.ACTIVE,
            )
        )
        await db.flush()
        return None


@pytest_asyncio.fixture(scope="function")
async def membership_session():
    """独立内存 DB，只建 membership 与 transition 表。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    configure_sqlite_schemas(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                HandlingOperation.__table__,
                HandlingMove.__table__,
                HandlingStep.__table__,
                BinTransitMembership.__table__,
                ObjectTransitionEvent.__table__,
            ],
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.drop_all,
            tables=[
                ObjectTransitionEvent.__table__,
                BinTransitMembership.__table__,
                HandlingStep.__table__,
                HandlingMove.__table__,
                HandlingOperation.__table__,
            ],
        )
    await engine.dispose()


def test_bin_transit_membership_model_declares_queue_contract() -> None:
    """模型层声明完整队列枚举、active 唯一约束和证据关联字段。"""
    assert BinTransitMembership.__tablename__ == "bin_transit_memberships"
    assert {queue.value for queue in BinTransitQueue} == {
        "INFEED_BUFFER_QUEUE",
        "ENTRY_SCAN_QUEUE",
        "WORKSTATION_WAIT_QUEUE",
        "WORKSTATION_ACTIVE",
        "EXIT_ROUTING_SCAN_QUEUE",
        "RETURN_SCAN_QUEUE",
        "RETURN_WAIT_QUEUE",
        "NG_REJECT_QUEUE",
    }
    assert {status.value for status in BinTransitMembershipStatus} == {"ACTIVE", "LEFT", "RECONCILING"}

    for field_name in (
        "bin_code",
        "placeholder_key",
        "workline_id",
        "workline_code",
        "current_queue",
        "membership_status",
        "handling_operation_id",
        "handling_move_id",
        "trace_id",
        "workline_session_id",
        "entered_at",
        "left_at",
        "evidence_json",
    ):
        assert field_name in BinTransitMembership.model_fields

    actual_indexes = {
        (index.name, tuple(column.name for column in index.columns), bool(index.unique))
        for index in BinTransitMembership.__table__.indexes
    }
    assert (
        "ux_bin_transit_memberships_active_bin",
        ("bin_code",),
        True,
    ) in actual_indexes
    assert (
        "ux_bin_transit_memberships_active_placeholder",
        ("placeholder_key",),
        True,
    ) in actual_indexes
    assert (
        "ix_bin_transit_memberships_trace_entered",
        ("trace_id", "entered_at"),
        False,
    ) in actual_indexes

    active_bin = next(
        index
        for index in BinTransitMembership.__table__.indexes
        if index.name == "ux_bin_transit_memberships_active_bin"
    )
    assert str(active_bin.dialect_options["postgresql"]["where"]) == "bin_code IS NOT NULL AND left_at IS NULL"
    assert str(active_bin.dialect_options["sqlite"]["where"]) == "bin_code IS NOT NULL AND left_at IS NULL"


def test_bin_transit_membership_exports_are_declared() -> None:
    """handling model/repository/service 必须从对应 __init__ 导出。"""
    from src.app.handling import models, repositories, services

    for name in (
        "BinTransitMembership",
        "BinTransitMembershipBase",
        "BinTransitMembershipCreate",
        "BinTransitMembershipResponse",
        "BinTransitMembershipStatus",
        "BinTransitQueue",
    ):
        assert hasattr(models, name)
        assert name in models.__all__

    for name in ("BinTransitMembershipRepository", "bin_transit_membership_repository"):
        assert hasattr(repositories, name)
        assert name in repositories.__all__

    for name in ("BinTransitMembershipService", "bin_transit_membership_service"):
        assert hasattr(services, name)
        assert name in services.__all__


def test_bin_transit_membership_migration_declares_table_contract() -> None:
    """迁移源码必须声明 FK、partial unique 与 queue 枚举合同。"""
    migration_files = sorted(Path("migrations/versions").glob("*bin_transit_membership*.py"))
    assert migration_files, "缺少 bin_transit_membership migration"

    source = migration_files[-1].read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "CREATE TABLE IF NOT EXISTS wes_biz.bin_transit_memberships" in source
    assert "INFEED_BUFFER_QUEUE" in source
    assert "NG_REJECT_QUEUE" in source
    assert "fk_bin_transit_memberships_workline_id_work_lines" in source
    assert "fk_btm_workline_session" in source
    assert "fk_btm_handling_operation" in source
    assert "fk_btm_handling_move" in source
    assert all(
        len(name) <= 63
        for name in (
            "fk_bin_transit_memberships_workline_id_work_lines",
            "fk_btm_workline_session",
            "fk_btm_handling_operation",
            "fk_btm_handling_move",
        )
    )
    assert "ux_bin_transit_memberships_active_bin" in source
    assert "bin_code IS NOT NULL AND left_at IS NULL" in source
    assert "ux_bin_transit_memberships_active_placeholder" in source
    assert "placeholder_key IS NOT NULL AND left_at IS NULL" in source
    assert "workline_id, current_queue" in normalized
    assert "workline_session_id, entered_at" in normalized


@pytest.mark.asyncio
async def test_enter_queue_creates_active_membership_and_transition(membership_session: AsyncSession) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )

    membership = await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.INFEED_BUFFER_QUEUE,
        workline_id=45,
        workline_code="SMT_SORTER_01",
        workline_session_id=301,
        handling_operation_id=700,
        handling_move_id=801,
        trace_id="trace-bin-001",
        reason_code="MOVE_REQUESTED",
        source_event_id="handling:op-001:move:1",
        evidence_json={"target_code": "SORTER_01_INFEED"},
        auto_commit=True,
    )

    assert membership.bin_code == "BIN-001"
    assert membership.current_queue == BinTransitQueue.INFEED_BUFFER_QUEUE
    assert membership.membership_status == BinTransitMembershipStatus.ACTIVE
    assert membership.left_at is None
    assert len(transition.events) == 1
    event = transition.events[0]
    assert event["domain"] == ObjectTransitionDomain.HANDLING
    assert event["object_type"] == "BIN_TRANSIT"
    assert event["object_key"] == "BIN-001"
    assert event["projection_type"] == "QUEUE_MEMBERSHIP"
    assert event["from_state"] is None
    assert event["to_state"] == "INFEED_BUFFER_QUEUE"
    assert event["reason_code"] == "MOVE_REQUESTED"
    assert event["source_ref_json"] == {
        "handling_operation_id": 700,
        "handling_move_id": 801,
    }
    assert event["workline_session_id"] == 301
    assert event["trace_id"] == "trace-bin-001"


@pytest.mark.asyncio
async def test_idempotent_enter_same_queue_reuses_active_membership(membership_session: AsyncSession) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )

    first = await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="scan:BIN-001",
        trace_id="trace-bin-001",
        auto_commit=True,
    )
    replay = await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="scan:BIN-001",
        trace_id="trace-bin-001",
        auto_commit=True,
    )

    result = await membership_session.execute(select(BinTransitMembership))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert replay.id == first.id
    assert len(transition.events) == 1


@pytest.mark.asyncio
async def test_switch_queue_closes_old_membership_opens_new_one_and_records_transition(
    membership_session: AsyncSession,
) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )
    old = await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="scan:BIN-001",
        auto_commit=True,
    )

    new = await service.switch_queue(
        membership_session,
        bin_code="BIN-001",
        to_queue=BinTransitQueue.WORKSTATION_WAIT_QUEUE,
        reason_code="SCAN_ACCEPTED",
        source_event_id="scan:BIN-001:accepted",
        handling_operation_id=700,
        handling_move_id=801,
        trace_id="trace-bin-001",
        auto_commit=True,
    )

    await membership_session.refresh(old)
    assert old.membership_status == BinTransitMembershipStatus.LEFT
    assert old.left_at is not None
    assert new.current_queue == BinTransitQueue.WORKSTATION_WAIT_QUEUE
    assert new.membership_status == BinTransitMembershipStatus.ACTIVE
    assert len(transition.events) == 2
    switch_event = transition.events[-1]
    assert switch_event["from_state"] == "ENTRY_SCAN_QUEUE"
    assert switch_event["to_state"] == "WORKSTATION_WAIT_QUEUE"
    assert switch_event["reason_code"] == "SCAN_ACCEPTED"
    assert switch_event["source_ref_json"] == {
        "handling_operation_id": 700,
        "handling_move_id": 801,
    }


@pytest.mark.asyncio
async def test_leave_queue_closes_active_membership_and_records_exit_transition(
    membership_session: AsyncSession,
) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )
    membership = await service.enter_queue(
        membership_session,
        placeholder_key="trace-001:slot-01",
        queue=BinTransitQueue.INFEED_BUFFER_QUEUE,
        source_event_id="placeholder:enter",
        auto_commit=True,
    )

    left = await service.leave_queue(
        membership_session,
        placeholder_key="trace-001:slot-01",
        reason_code="BIN_DEPARTED_QUEUE",
        source_event_id="placeholder:leave",
        trace_id="trace-bin-001",
        auto_commit=True,
    )

    assert left.id == membership.id
    assert left.membership_status == BinTransitMembershipStatus.LEFT
    assert left.left_at is not None
    assert len(transition.events) == 2
    exit_event = transition.events[-1]
    assert exit_event["object_key"] == "trace-001:slot-01"
    assert exit_event["from_state"] == "INFEED_BUFFER_QUEUE"
    assert exit_event["to_state"] == "LEFT"
    assert exit_event["reason_code"] == "BIN_DEPARTED_QUEUE"


@pytest.mark.asyncio
async def test_resolve_placeholder_moves_active_membership_to_real_bin(
    membership_session: AsyncSession,
) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )
    placeholder = await service.enter_queue(
        membership_session,
        placeholder_key="trace-001:slot-01",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="placeholder:enter",
        auto_commit=True,
    )

    resolved = await service.resolve_placeholder(
        membership_session,
        placeholder_key="trace-001:slot-01",
        bin_code="BIN-001",
        reason_code="ENTRY_SCAN_RESOLVED",
        source_event_id="scan:BIN-001",
        handling_operation_id=700,
        handling_move_id=801,
        trace_id="trace-bin-001",
        evidence_json={"scan_code": "BIN-001"},
        auto_commit=True,
    )

    assert resolved.id == placeholder.id
    assert resolved.bin_code == "BIN-001"
    assert resolved.placeholder_key is None
    assert resolved.current_queue == BinTransitQueue.ENTRY_SCAN_QUEUE
    assert resolved.evidence_json["resolved_from_placeholder_key"] == "trace-001:slot-01"
    assert resolved.evidence_json["scan_code"] == "BIN-001"
    assert len(transition.events) == 2
    resolve_event = transition.events[-1]
    assert resolve_event["object_key"] == "BIN-001"
    assert resolve_event["from_state"] == "ENTRY_SCAN_QUEUE"
    assert resolve_event["to_state"] == "ENTRY_SCAN_QUEUE"
    assert resolve_event["reason_code"] == "ENTRY_SCAN_RESOLVED"
    assert resolve_event["evidence_json"]["previous_object_key"] == "trace-001:slot-01"


@pytest.mark.asyncio
async def test_resolve_placeholder_replay_after_success_returns_resolved_bin_membership(
    membership_session: AsyncSession,
) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )
    await service.enter_queue(
        membership_session,
        placeholder_key="trace-001:slot-01",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="placeholder:enter",
        auto_commit=True,
    )
    resolved = await service.resolve_placeholder(
        membership_session,
        placeholder_key="trace-001:slot-01",
        bin_code="BIN-001",
        reason_code="ENTRY_SCAN_RESOLVED",
        source_event_id="scan:BIN-001",
        auto_commit=True,
    )

    replay = await service.resolve_placeholder(
        membership_session,
        placeholder_key="trace-001:slot-01",
        bin_code="BIN-001",
        reason_code="ENTRY_SCAN_RESOLVED",
        source_event_id="scan:BIN-001",
        auto_commit=True,
    )

    assert replay.id == resolved.id
    assert replay.bin_code == "BIN-001"
    assert len(transition.events) == 2


@pytest.mark.asyncio
async def test_resolve_placeholder_conflict_marks_placeholder_reconciling(
    membership_session: AsyncSession,
) -> None:
    transition = FakeTransitionService()
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=transition,
    )
    await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.WORKSTATION_ACTIVE,
        source_event_id="bin:active",
        auto_commit=True,
    )
    placeholder = await service.enter_queue(
        membership_session,
        placeholder_key="trace-001:slot-01",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="placeholder:enter",
        auto_commit=True,
    )

    reconciling = await service.resolve_placeholder(
        membership_session,
        placeholder_key="trace-001:slot-01",
        bin_code="BIN-001",
        reason_code="ENTRY_SCAN_CONFLICT",
        source_event_id="scan:BIN-001:conflict",
        trace_id="trace-bin-001",
        auto_commit=True,
    )

    assert reconciling.id == placeholder.id
    assert reconciling.placeholder_key == "trace-001:slot-01"
    assert reconciling.bin_code is None
    assert reconciling.membership_status == BinTransitMembershipStatus.RECONCILING
    assert reconciling.left_at is None
    assert reconciling.evidence_json["conflicting_bin_code"] == "BIN-001"
    assert len(transition.events) == 3
    conflict_event = transition.events[-1]
    assert conflict_event["object_key"] == "trace-001:slot-01"
    assert conflict_event["from_state"] == "ENTRY_SCAN_QUEUE"
    assert conflict_event["to_state"] == "RECONCILING"
    assert conflict_event["reason_code"] == "ENTRY_SCAN_CONFLICT"


@pytest.mark.asyncio
async def test_partial_unique_prevents_same_bin_active_in_two_queues(membership_session: AsyncSession) -> None:
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=FakeTransitionService(),
    )
    await service.enter_queue(
        membership_session,
        bin_code="BIN-001",
        queue=BinTransitQueue.ENTRY_SCAN_QUEUE,
        source_event_id="scan:BIN-001",
        auto_commit=True,
    )

    with pytest.raises(ValueError, match="已 active 于队列 ENTRY_SCAN_QUEUE"):
        await service.enter_queue(
            membership_session,
            bin_code="BIN-001",
            queue=BinTransitQueue.WORKSTATION_ACTIVE,
            source_event_id="scan:BIN-001:active",
            auto_commit=True,
        )

    with pytest.raises(IntegrityError):
        membership_session.add(
            BinTransitMembership(
                bin_code="BIN-001",
                current_queue=BinTransitQueue.NG_REJECT_QUEUE,
                membership_status=BinTransitMembershipStatus.ACTIVE,
            )
        )
        await membership_session.flush()
    await membership_session.rollback()

    count_result = await membership_session.execute(
        text("SELECT COUNT(*) FROM wes_biz.bin_transit_memberships WHERE bin_code = 'BIN-001' AND left_at IS NULL")
    )
    assert count_result.scalar_one() == 1


@pytest.mark.asyncio
async def test_leave_queue_can_be_called_as_idempotent_noop_for_missing_active_membership(
    membership_session: AsyncSession,
) -> None:
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=FakeTransitionService(),
    )

    result = await service.leave_queue(
        membership_session,
        bin_code="BIN-404",
        reason_code="HANDLING_CALLBACK_SUCCEEDED",
        source_event_id="handling:missing:SUCCEEDED",
        ignore_missing=True,
        auto_commit=True,
    )

    assert result is None


@pytest.mark.asyncio
async def test_mark_reconciling_can_be_called_as_idempotent_noop_for_missing_active_membership(
    membership_session: AsyncSession,
) -> None:
    service = BinTransitMembershipService(
        repository=BinTransitMembershipRepository(),
        transition_service=FakeTransitionService(),
    )

    result = await service.mark_reconciling(
        membership_session,
        bin_code="BIN-404",
        reason_code="RACK_RELEASE_ID_MISMATCH",
        source_event_id="handling:missing:RECONCILING",
        ignore_missing=True,
        auto_commit=True,
    )

    assert result is None


@pytest.mark.asyncio
async def test_lifecycle_terminal_callback_does_not_fail_when_membership_is_missing(
    membership_session: AsyncSession,
) -> None:
    operation = HandlingOperation(
        operation_key="bin-operation:missing-membership",
        operation_type="SORTER_FEED_BIN",
        object_type=HandlingObjectType.BIN,
        operation_status=HandlingOperationStatus.REQUESTED,
        trace_id="trace-missing-membership",
    )
    membership_session.add(operation)
    await membership_session.flush()
    move = HandlingMove(
        operation_id=operation.id,
        operation_key=operation.operation_key,
        sequence_no=1,
        object_type=HandlingObjectType.BIN,
        move_status=HandlingMoveStatus.IN_PROGRESS,
        bin_code="BIN-MISSING-MEMBERSHIP",
        source_type="ENTRY_SCAN_QUEUE",
        source_code="ENTRY_SCAN_QUEUE",
        target_type="QUEUE",
        target_code="WORKSTATION_WAIT_QUEUE",
    )
    membership_session.add(move)
    await membership_session.flush()
    step = HandlingStep(
        operation_id=operation.id,
        operation_key=operation.operation_key,
        move_id=move.id,
        sequence_no=1,
        step_key="bin-operation:missing-membership:external:1",
        step_kind=HandlingStepKind.EXTERNAL_REQUEST,
        step_status=HandlingStepStatus.IN_PROGRESS,
        dispatch_key="handling:bin-operation:missing-membership:move:1",
    )
    membership_session.add(step)
    await membership_session.commit()

    result = await HandlingOperationLifecycleService().record_callback_from_external_http(
        membership_session,
        payload_json={
            "callback_type": "CTU_BIN_MOVE_COMPLETED",
            "dispatch_key": "handling:bin-operation:missing-membership:move:1",
            "status": "SUCCEEDED",
        },
        trace_id="trace-missing-membership",
    )

    assert result is not None
    await membership_session.refresh(step)
    await membership_session.refresh(move)
    await membership_session.refresh(operation)
    assert step.step_status == HandlingStepStatus.SUCCEEDED
    assert move.move_status == HandlingMoveStatus.SUCCEEDED
    assert operation.operation_status == HandlingOperationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_lifecycle_keeps_main_state_when_real_membership_create_hits_integrity_error(
    membership_session: AsyncSession,
) -> None:
    operation = HandlingOperation(
        operation_key="bin-operation:membership-flush-fails",
        operation_type="SORTER_FEED_BIN",
        object_type=HandlingObjectType.BIN,
        operation_status=HandlingOperationStatus.REQUESTED,
        trace_id="trace-membership-flush-fails",
    )
    membership_session.add(operation)
    await membership_session.flush()
    move = HandlingMove(
        operation_id=operation.id,
        operation_key=operation.operation_key,
        sequence_no=1,
        object_type=HandlingObjectType.BIN,
        move_status=HandlingMoveStatus.REQUESTED,
        bin_code="BIN-MEMBERSHIP-FLUSH-FAILS",
        source_type="ENTRY_SCAN_QUEUE",
        source_code="ENTRY_SCAN_QUEUE",
        target_type="QUEUE",
        target_code="WORKSTATION_WAIT_QUEUE",
    )
    membership_session.add(move)
    await membership_session.flush()
    step = HandlingStep(
        operation_id=operation.id,
        operation_key=operation.operation_key,
        move_id=move.id,
        sequence_no=1,
        step_key="bin-operation:membership-flush-fails:external:1",
        step_kind=HandlingStepKind.EXTERNAL_REQUEST,
        step_status=HandlingStepStatus.REQUESTED,
        dispatch_key="handling:bin-operation:membership-flush-fails:move:1",
    )
    membership_session.add(step)
    await membership_session.commit()

    result = await HandlingOperationLifecycleService(
        membership_service=BinTransitMembershipService(
            repository=RaceConflictMembershipRepository(),
            transition_service=FakeTransitionService(),
        ),
    ).record_callback_from_external_http(
        membership_session,
        payload_json={
            "callback_type": "CTU_BIN_MOVE_PROGRESS",
            "dispatch_key": "handling:bin-operation:membership-flush-fails:move:1",
            "status": "IN_PROGRESS",
            "target_queue": "WORKSTATION_WAIT_QUEUE",
        },
        trace_id="trace-membership-flush-fails",
    )
    assert result is not None

    await membership_session.commit()
    await membership_session.refresh(step)
    await membership_session.refresh(move)
    await membership_session.refresh(operation)

    assert step.step_status == HandlingStepStatus.IN_PROGRESS
    assert move.move_status == HandlingMoveStatus.IN_PROGRESS
    assert operation.operation_status == HandlingOperationStatus.IN_PROGRESS
