from datetime import timedelta

import pytest

from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.repositories.session_repository import WorklineSessionRepository
from src.utils.timezone import timezone


def test_open_session_business_key_guard_index_exists() -> None:
    index = next(
        item for item in WorklineSession.__table__.indexes if item.name == "uq_workline_sessions_open_business_key"
    )

    assert index.unique is True
    assert [column.name for column in index.columns] == ["workline_id", "business_key"]
    assert "WAITING_DEVICE_RESULT" in str(index.dialect_options["postgresql"]["where"])


@pytest.mark.asyncio
async def test_get_timed_out_sessions_includes_external_waits_without_command(db_session) -> None:
    expired_session = WorklineSession(
        session_code="session-external-timeout",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_type="EXTERNAL_HTTP",
        deadline_at=timezone.now_for_db() - timedelta(minutes=5),
        awaiting_command_id=None,
    )
    db_session.add(expired_session)
    await db_session.flush()

    timed_out = await WorklineSessionRepository().get_timed_out_sessions(db_session)

    assert expired_session.id in [session.id for session in timed_out]


@pytest.mark.asyncio
async def test_persist_external_wait_clears_command_wait_and_stores_context(db_session) -> None:
    session = WorklineSession(
        session_code="session-rack-wait",
        workline_id=45,
        plugin_key="test_workline_plugin",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        awaiting_command_id=88,
        context_json={},
    )
    db_session.add(session)
    await db_session.flush()

    occurred_at = timezone.now_for_db()
    await WorklineSessionRepository().persist_external_wait(
        db_session,
        session_id=session.id,
        wait_type="RACK_OPERATION",
        occurred_at=occurred_at,
        timeout_seconds=300,
        context_json={"waiting_rack_operation_key": "rack-operation:trace-runtime"},
    )
    await db_session.refresh(session)

    assert session.status == SessionStatus.WAITING_EXTERNAL
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.awaiting_command_id is None
    assert session.waiting_since == occurred_at
    assert session.deadline_at == occurred_at + timedelta(seconds=300)
    assert session.context_json["waiting_rack_operation_key"] == "rack-operation:trace-runtime"
