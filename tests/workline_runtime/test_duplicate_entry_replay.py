from types import SimpleNamespace

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import SessionStatus
from src.app.workline.services.inbox_batch_processor import _is_duplicate_entry_event_for_session


def _entry_payload(**extra):
    payload = {
        "event_type": "SCAN_COMPLETED",
        "device_code": "ARM03",
        "data": {"item_id": "ITEM-001"},
    }
    payload.update(extra)
    return payload


def test_payload_invalid_replay_entry_can_be_reprocessed() -> None:
    inbox = SimpleNamespace(kind=InboxKind.DEVICE_EVENT)
    session = SimpleNamespace(
        status=SessionStatus.MANUAL_HOLD,
        awaiting_device_command_code=None,
        current_wait_type=None,
        failure_code="PAYLOAD_INVALID",
    )
    workline = SimpleNamespace(plugin_key="test_workline_plugin")

    assert (
        _is_duplicate_entry_event_for_session(
            inbox=inbox,
            payload=_entry_payload(replay_of_event_id="sandbox:SCAN_COMPLETED:original"),
            session=session,
            workline=workline,
        )
        is False
    )


def test_non_replay_manual_hold_entry_still_archives_as_duplicate() -> None:
    inbox = SimpleNamespace(kind=InboxKind.DEVICE_EVENT)
    session = SimpleNamespace(
        status=SessionStatus.MANUAL_HOLD,
        awaiting_device_command_code=None,
        current_wait_type=None,
        failure_code="PAYLOAD_INVALID",
    )
    workline = SimpleNamespace(plugin_key="test_workline_plugin")

    assert (
        _is_duplicate_entry_event_for_session(
            inbox=inbox,
            payload=_entry_payload(),
            session=session,
            workline=workline,
        )
        is True
    )


def test_replay_entry_for_busy_session_still_archives_as_duplicate() -> None:
    inbox = SimpleNamespace(kind=InboxKind.DEVICE_EVENT)
    session = SimpleNamespace(
        status=SessionStatus.WAITING_DEVICE_RESULT,
        awaiting_device_command_code=123,
        current_wait_type="DEVICE_CALLBACK",
        failure_code="PAYLOAD_INVALID",
    )
    workline = SimpleNamespace(plugin_key="test_workline_plugin")

    assert (
        _is_duplicate_entry_event_for_session(
            inbox=inbox,
            payload=_entry_payload(replay_of_event_id="sandbox:SCAN_COMPLETED:original"),
            session=session,
            workline=workline,
        )
        is True
    )
