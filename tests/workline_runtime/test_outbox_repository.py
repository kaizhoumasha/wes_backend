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
async def test_mark_as_acked_clears_retry_error_projection() -> None:
    outbox = SimpleNamespace(
        status=OutboxStatus.SENT,
        finished_at=None,
        next_retry_at=object(),
        last_error="Dispatch failed",
    )
    db = _FakeDb(outbox)

    updated = await WorklineOutboxRepository().mark_as_acked_by_dispatch_key(db, "device-command:CMD-1")  # type: ignore[arg-type]

    assert updated is outbox
    assert outbox.status == OutboxStatus.ACKED
    assert outbox.finished_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
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
async def test_get_sandbox_pending_messages_excludes_terminal_sessions_and_keeps_acked_waiting_outbox(
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
        status=OutboxStatus.ACKED,
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
    acked_outbox = WorklineOutbox(
        session_id=session.id,
        workline_id=45,
        dispatch_type=DispatchType.DEVICE_COMMAND,
        dispatch_key="device-command:acked",
        target_type=TargetType.DEVICE,
        target_code="ARM02",
        status=OutboxStatus.ACKED,
    )
    db_session.add_all([active_outbox, acked_outbox])
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
    assert acked_outbox.status == OutboxStatus.ACKED


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
