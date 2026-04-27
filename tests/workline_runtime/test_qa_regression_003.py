from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.callback.models import CallbackEventRequest
from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService


class _DuplicateInboxService:
    async def create_device_event_inbox(self, *_args: object, **_kwargs: object) -> object:
        error = ValueError("设备事件已存在（幂等键重复）: duplicate")
        error.existing_inbox = SimpleNamespace(
            id=42,
            trace_id="trace-original",
            event_id="event-original",
            causation_id=None,
            source_message_id="req-original",
            payload_json={
                "device_code": "ARM01",
                "event_type": "SCAN_COMPLETED",
                "canonical_event_type": "SCAN_COMPLETED",
            },
        )
        raise error


@pytest.mark.asyncio
async def test_duplicate_event_ack_uses_existing_inbox_trace_id() -> None:
    """duplicate ACK 应指向原业务 trace，避免调用方拿到只有重复日志的新 trace。"""

    # Regression: ISSUE-003 — duplicate callback/event generated a fresh trace_id in ACK.
    # Found by curl QA on 2026-04-27
    db = SimpleNamespace(commit=AsyncMock())
    service = CallbackOrchestrationService()

    with patch(
        "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        outcome = await service.process_event(
            db,
            event_request=CallbackEventRequest(
                device_code="ARM01",
                event_type="SCAN_COMPLETED",
                timestamp=1777277303000,
                event_id="event-original",
                data={"PkgID": "SVYU00125TP4LCR04_1"},
            ),
            request_id="req-duplicate",
            is_workline_event=True,
            canonical_event_type="SCAN_COMPLETED",
            inbox_service=_DuplicateInboxService(),  # type: ignore[arg-type]
            event_id="event-original",
            enqueue_processing=lambda: None,
        )

    assert outcome.is_duplicate is True
    assert outcome.trace_id == "trace-original"
