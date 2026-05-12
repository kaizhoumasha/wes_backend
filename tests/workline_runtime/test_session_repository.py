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
        plugin_key="smt_classifier",
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
