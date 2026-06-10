from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.sys.services.event_stream_service import (
    DEVICE_STATUS_CHANGED_EVENT,
    WORKLINE_RUNTIME_CHANGED_EVENT,
)
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_device_status_changed_sse_uses_canonical_envelope() -> None:
    from src.app.device.services.device_service import device_service

    db = SimpleNamespace(info={})
    device = SimpleNamespace(
        id=101,
        device_code="ARM-01",
        work_line_id=45,
        device_status="RUNNING",
        current_command_id=505,
        error_code=None,
        maintenance_mode=False,
        version=1,
        last_heartbeat_at=timezone.now(),
    )

    old_state = {"device_status": "IDLE"}
    changed_fields = ["device_status"]

    with patch("src.app.device.services.device_service.defer_sse_event") as mock_defer:
        device_service._defer_device_status_event(db, device=device, old_state=old_state, changed_fields=changed_fields)

    mock_defer.assert_called_once()
    event_type, payload = mock_defer.call_args.args[1], mock_defer.call_args.args[2]

    assert event_type == DEVICE_STATUS_CHANGED_EVENT
    assert payload["domain"] == "workline_runtime"
    assert payload["entity"] == "device"
    assert payload["action"] == "updated"
    assert payload["keys"] == {
        "workline_id": 45,
        "device_id": 101,
    }
    assert payload["device_id"] == 101
    assert payload["device_code"] == "ARM-01"


@pytest.mark.asyncio
async def test_session_updated_sse_uses_canonical_envelope() -> None:
    from src.app.sys.services.event_stream_service import defer_sse_event
    from src.app.workline.services.inbox_batch_processor import build_workline_runtime_session_updated_event_payload

    db = SimpleNamespace(info={})
    payload = build_workline_runtime_session_updated_event_payload(workline_id=45, session_id=99)

    defer_sse_event(
        db,
        WORKLINE_RUNTIME_CHANGED_EVENT,
        payload,
    )

    events = db.info.get("_deferred_sse_events_after_commit", [])
    assert len(events) == 1
    event_type, payload = events[0]

    assert event_type == WORKLINE_RUNTIME_CHANGED_EVENT
    assert payload["domain"] == "workline_runtime"
    assert payload["entity"] == "session"
    assert payload["action"] == "updated"
    assert payload["keys"] == {
        "workline_id": 45,
        "session_id": 99,
    }
