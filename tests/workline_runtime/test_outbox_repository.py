from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.outbox import DispatchType, OutboxStatus, TargetType, WorklineOutbox
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.repositories.outbox_repository import WorklineOutboxRepository


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _FakeDb:
    def __init__(self, value: object) -> None:
        self.value = value
        self.flush = AsyncMock()

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_mark_as_sent_clears_retry_error_projection() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.DISPATCHING,
        sent_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == OutboxStatus.SENT
    assert outbox.sent_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_sent_does_not_overwrite_cancelled_outbox() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.CANCELLED,
        sent_at=None,
        next_retry_at=None,
        last_error="CALLBACK_DEADLINE_EXPIRED",
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_sent(db, 1)  # type: ignore[arg-type]

    assert updated is None
    assert outbox.status == OutboxStatus.CANCELLED
    assert outbox.sent_at is None
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_as_blocked_by_workline_state_parks_without_retry() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.NEW,
        workline_id=45,
        finished_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=None,
        blocked_workline_id=None,
        blocked_reason=None,
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_blocked_by_workline_state(  # type: ignore[arg-type]
        db,
        1,
        owner_session_id=91,
        reason="CALLBACK_DEADLINE_EXPIRED",
        blocked_device_id=7,
        blocked_workline_id=45,
    )

    assert updated is outbox
    assert outbox.status == OutboxStatus.BLOCKED_RESOURCE
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    assert outbox.blocked_by_reconciliation_session_id == 91
    assert outbox.blocked_device_id == 7
    assert outbox.blocked_workline_id == 45
    assert outbox.blocked_reason == "CALLBACK_DEADLINE_EXPIRED"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_blocked_by_workline_estop_terminates_without_retry() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.NEW,
        finished_at=None,
        next_retry_at=object(),
        last_error=None,
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_blocked_by_workline_estop(db, 1)  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == OutboxStatus.FAILED
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error == "BLOCKED_BY_WORKLINE_ESTOP"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_as_failed_uses_three_retry_backoff_then_exhausts() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.DISPATCHING,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        finished_at=None,
    )
    db = _FakeDb(outbox)
    repo = WorklineOutboxRepository()

    first = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    first_retry = first.next_retry_at
    assert first.status == OutboxStatus.NEW
    assert first.attempt_count == 1
    assert first_retry is not None

    second = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    second_retry = second.next_retry_at
    assert second.status == OutboxStatus.NEW
    assert second.attempt_count == 2
    assert second_retry is not None
    assert second_retry > first_retry

    third = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    third_retry = third.next_retry_at
    assert third.status == OutboxStatus.NEW
    assert third.attempt_count == 3
    assert third_retry is not None
    assert third_retry > second_retry

    exhausted = await repo.mark_as_failed(db, 1, "OUTBOX_ACK_TIMEOUT")
    assert exhausted.status == OutboxStatus.FAILED
    assert exhausted.attempt_count == 4
    assert exhausted.next_retry_at is None
    assert exhausted.finished_at is not None


@pytest.mark.asyncio
async def test_mark_as_failed_does_not_overwrite_blocked_outbox() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.BLOCKED_RESOURCE,
        attempt_count=0,
        next_retry_at=None,
        last_error="CALLBACK_DEADLINE_EXPIRED",
        finished_at=None,
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_failed(db, 1, "Dispatch failed")  # type: ignore[arg-type]

    assert updated is None
    assert outbox.status == OutboxStatus.BLOCKED_RESOURCE
    assert outbox.attempt_count == 0
    assert outbox.last_error == "CALLBACK_DEADLINE_EXPIRED"
    assert outbox.finished_at is None
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_sandbox_pending_messages_excludes_terminal_sessions_and_keeps_sent_waiting_outbox(
    db_session,
) -> None:
    failed_session = WorklineSession(
        session_code="session-failed",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
    )
    waiting_session = WorklineSession(
        session_code="session-waiting",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
    )
    db_session.add(failed_session)
    db_session.add(waiting_session)
    await db_session.flush()

    failed_outbox = WorklineOutbox(
        session_id=failed_session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:failed",
        target_type=TargetType.DEVICE,
        target_code="ARM01",
        status=OutboxStatus.SENT,
    )
    waiting_outbox = WorklineOutbox(
        session_id=waiting_session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:waiting",
        target_type=TargetType.DEVICE,
        target_code="ARM02",
        status=OutboxStatus.SENT,
    )
    db_session.add(failed_outbox)
    db_session.add(waiting_outbox)
    await db_session.flush()

    pending = await WorklineOutboxRepository().get_sandbox_pending_messages(db_session, workline_id=45)

    assert [item.dispatch_key for item in pending] == ["device-command:waiting"]


@pytest.mark.asyncio
async def test_cancel_active_by_session_closes_stale_sandbox_actions(db_session) -> None:
    session = WorklineSession(
        session_code="session-timeout",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
    )
    db_session.add(session)
    await db_session.flush()

    active_outbox = WorklineOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:active",
        target_type=TargetType.DEVICE,
        target_code="ARM01",
        status=OutboxStatus.SENT,
    )
    terminal_outbox = WorklineOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:terminal",
        target_type=TargetType.DEVICE,
        target_code="ARM02",
        status=OutboxStatus.FAILED,
    )
    db_session.add_all([active_outbox, terminal_outbox])
    await db_session.flush()

    closed = await WorklineOutboxRepository().cancel_active_by_session(
        db_session,
        session_id=session.id,
        reason="DEVICE_TIMEOUT",
    )

    assert closed == 1
    assert active_outbox.status == OutboxStatus.CANCELLED
    assert active_outbox.last_error == "DEVICE_TIMEOUT"
    assert active_outbox.finished_at is not None
    assert terminal_outbox.status == OutboxStatus.FAILED


@pytest.mark.asyncio
async def test_release_blocked_by_reconciliation_session_requeues_only_owner_blocked_outbox(db_session) -> None:
    owner_session = WorklineSession(
        session_code="session-owner-reconcile",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    other_session = WorklineSession(
        session_code="session-other-reconcile",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.MANUAL_HOLD,
    )
    db_session.add_all([owner_session, other_session])
    await db_session.flush()

    owner_blocked = WorklineOutbox(
        session_id=owner_session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:owner-blocked",
        target_type=TargetType.DEVICE,
        target_code="ARM01",
        status=OutboxStatus.BLOCKED_RESOURCE,
        attempt_count=2,
        last_error="CALLBACK_DEADLINE_EXPIRED",
        blocked_by_reconciliation_session_id=owner_session.id,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="CALLBACK_DEADLINE_EXPIRED",
    )
    other_blocked = WorklineOutbox(
        session_id=other_session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:other-blocked",
        target_type=TargetType.DEVICE,
        target_code="ARM02",
        status=OutboxStatus.BLOCKED_RESOURCE,
        blocked_by_reconciliation_session_id=other_session.id,
        blocked_reason="CALLBACK_DEADLINE_EXPIRED",
    )
    db_session.add_all([owner_blocked, other_blocked])
    await db_session.flush()

    released = await WorklineOutboxRepository().release_blocked_by_reconciliation_session(
        db_session,
        owner_session.id,
    )

    assert released == 1
    assert owner_blocked.status == OutboxStatus.NEW
    assert owner_blocked.attempt_count == 0
    assert owner_blocked.last_error is None
    assert owner_blocked.blocked_by_reconciliation_session_id is None
    assert owner_blocked.blocked_device_id is None
    assert owner_blocked.blocked_workline_id is None
    assert owner_blocked.blocked_reason is None
    assert other_blocked.status == OutboxStatus.BLOCKED_RESOURCE


@pytest.mark.asyncio
async def test_get_sandbox_completed_messages_includes_cancelled_terminal_outbox(db_session) -> None:
    session = WorklineSession(
        session_code="session-cancelled-outbox",
        workline_id=45,
        plugin_key="smt_classifier",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.FAILED,
    )
    db_session.add(session)
    await db_session.flush()

    outbox = WorklineOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:cancelled",
        target_type=TargetType.DEVICE,
        target_code="ARM01",
        status=OutboxStatus.CANCELLED,
    )
    db_session.add(outbox)
    await db_session.flush()

    completed = await WorklineOutboxRepository().get_sandbox_completed_messages(db_session, workline_id=45)

    assert completed[0]["session"]["id"] == session.id
    assert completed[0]["outbox_items"][0]["dispatch_key"] == "device-command:cancelled"
