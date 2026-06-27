from types import SimpleNamespace

import pytest

from src.app.workline.domain.services.session_lifecycle_service import (
    InvalidSessionTransition,
    WorklineSessionLifecycleService,
)
from src.app.workline.models.session import SessionStatus
from src.utils.timezone import timezone


def _session(**overrides):
    data = {
        "status": SessionStatus.RUNNING,
        "current_wait_type": "COMMAND_RESULT",
        "waiting_since": timezone.now_for_db(),
        "deadline_at": timezone.now_for_db(),
        "current_wait_timeout_seconds": 30,
        "awaiting_device_command_code": "CMD-00011",
        "ended_at": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_complete_clears_wait_fields_and_sets_end_time() -> None:
    service = WorklineSessionLifecycleService()
    occurred_at = timezone.now_for_db()
    session = _session(status=SessionStatus.WAITING_DEVICE_RESULT)

    service.complete(session, occurred_at=occurred_at)

    assert session.status == SessionStatus.COMPLETED
    assert session.ended_at == occurred_at
    assert session.current_wait_type is None
    assert session.waiting_since is None
    assert session.deadline_at is None
    assert session.current_wait_timeout_seconds is None
    assert session.awaiting_device_command_code is None


def test_fail_records_failure_and_clears_wait_fields() -> None:
    service = WorklineSessionLifecycleService()
    occurred_at = timezone.now_for_db()
    session = _session(status=SessionStatus.WAITING_EXTERNAL)

    service.fail(
        session,
        occurred_at=occurred_at,
        failure_domain="runtime",
        failure_code="TIMEOUT",
        failure_message="Callback timed out",
    )

    assert session.status == SessionStatus.FAILED
    assert session.ended_at == occurred_at
    assert session.failure_domain == "runtime"
    assert session.failure_code == "TIMEOUT"
    assert session.failure_message == "Callback timed out"
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None


def test_failed_resolution_preserves_existing_failure_fields() -> None:
    service = WorklineSessionLifecycleService()
    occurred_at = timezone.now_for_db()
    session = _session(
        status=SessionStatus.MANUAL_HOLD,
        failure_domain="BLOCK",
        failure_code="SCAN_NG",
        failure_message="原始阻塞原因",
    )

    service.resolve(session, resolution=SessionStatus.FAILED, occurred_at=occurred_at)

    assert session.status == SessionStatus.FAILED
    assert session.ended_at == occurred_at
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain == "BLOCK"
    assert session.failure_code == "SCAN_NG"
    assert session.failure_message == "原始阻塞原因"


def test_failed_resolution_is_idempotent_for_already_failed_session() -> None:
    service = WorklineSessionLifecycleService()
    occurred_at = timezone.now_for_db()
    session = _session(
        status=SessionStatus.FAILED,
        failure_domain="SAFETY",
        failure_code="WORKLINE_ESTOPPED",
        failure_message="WorkLine 急停冻结",
        current_wait_type="MANUAL",
        awaiting_device_command_code="CMD-00123",
    )

    service.resolve(session, resolution=SessionStatus.FAILED, occurred_at=occurred_at)

    assert session.status == SessionStatus.FAILED
    assert session.ended_at == occurred_at
    assert session.current_wait_type is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain == "SAFETY"
    assert session.failure_code == "WORKLINE_ESTOPPED"
    assert session.failure_message == "WorkLine 急停冻结"


def test_start_command_result_wait_keeps_deadline_empty_until_ack() -> None:
    service = WorklineSessionLifecycleService()
    occurred_at = timezone.now_for_db()
    session = _session(status=SessionStatus.RUNNING, current_wait_type=None, awaiting_device_command_code=None)

    service.start_wait(
        session,
        wait_type="COMMAND_RESULT",
        occurred_at=occurred_at,
        awaiting_device_command_code="CMD-00042",
        deadline_seconds=60,
    )

    assert session.status == SessionStatus.WAITING_DEVICE_RESULT
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.waiting_since == occurred_at
    assert session.awaiting_device_command_code == "CMD-00042"
    assert session.current_wait_timeout_seconds == 60
    assert session.deadline_at is None
    assert session.ended_at is None


def test_terminal_session_rejects_wait_transition() -> None:
    service = WorklineSessionLifecycleService()
    session = _session(status=SessionStatus.COMPLETED)

    with pytest.raises(InvalidSessionTransition):
        service.start_wait(session, wait_type="EXTERNAL_HTTP", occurred_at=timezone.now_for_db())
