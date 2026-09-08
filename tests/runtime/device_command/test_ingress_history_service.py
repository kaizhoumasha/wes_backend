"""诊断历史投影、持久化边界和游标的 FAST owner。"""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.device.contracts import DeviceIngressAttempt
from src.app.device.services.device_ingress_history_service import DeviceIngressHistoryService

NOW = datetime(2026, 9, 7, 10)


def _attempt(request_id="request-1", evidence_id=7):
    return DeviceIngressAttempt(
        request_id=request_id,
        kind="DEVICE_EVENT",
        path="/api/v1/callback/event",
        received_at="2026-09-07T10:00:00Z",
        disposition="ACCEPTED",
        status_code=200,
        evidence_id=evidence_id,
        source_event_id="evt-1",
        device_code="D" * 100,
        apply_status="PENDING",
        observed_body_bytes=32,
        raw_payload={"secret": "[REDACTED]"},
    )


@asynccontextmanager
async def _session():
    yield object()


async def test_history_keeps_each_attempt_and_projects_current_state_without_fabricating_legacy_receipt():
    attempt = _attempt()
    evidence = SimpleNamespace(
        id=7,
        kind="DEVICE_EVENT",
        source_identity="evt-1",
        device_code="D" * 100,
        command_code=None,
        normalized_payload={"event_type": "SCAN_COMPLETED"},
        apply_status="IGNORED",
        processed_at=NOW,
    )
    old = SimpleNamespace(
        **{**vars(evidence), "id": 8, "source_identity": "legacy", "apply_status": "PENDING", "processed_at": None}
    )
    repo = SimpleNamespace(
        page_keys=AsyncMock(
            return_value=[
                {"id": 10, "source_rank": 1, "recorded_at": NOW},
                {"id": 8, "source_rank": 0, "recorded_at": NOW},
            ]
        ),
        load_logs=AsyncMock(return_value={10: SimpleNamespace(request_body=attempt.model_dump(mode="json"))}),
        load_evidences=AsyncMock(return_value={7: evidence, 8: old}),
    )
    service = DeviceIngressHistoryService(session_context=_session, repository=repo)
    page = await service.list_history()
    assert page.items[0].row_key == "attempt:request-1"
    assert page.items[0].attempt.apply_status == "PENDING"
    assert page.items[0].latest_update.apply_status == "IGNORED"
    assert page.items[1].row_key == "evidence:8"
    assert page.items[1].attempt is None
    assert page.items[1].latest_update.apply_status == "PENDING"
    assert page.items[1].latest_update.processed_at is None
    assert page.next_cursor is None
    repo.load_evidences.assert_awaited_once()


async def test_recording_uses_generic_callback_log_with_diagnostic_type_and_preserves_full_device_code():
    logs = SimpleNamespace(log_callback=AsyncMock())
    service = DeviceIngressHistoryService(session_context=_session, log_service=logs)
    attempt = _attempt()
    await service.record_attempt(attempt)
    args = logs.log_callback.call_args.kwargs
    assert args["callback_type"] == "device_ingress_attempt"
    assert args["subject_code"] == "DEVICE_INGRESS"
    assert args["request_id"] == attempt.request_id
    assert args["request_body"] == attempt.model_dump(mode="json")


@pytest.mark.parametrize("cursor", ["bad!", "", "WzEsMixudWxsXQ", "W10"])
async def test_invalid_cursor_is_rejected_before_query(cursor):
    repo = SimpleNamespace(page_keys=AsyncMock())
    service = DeviceIngressHistoryService(session_context=_session, repository=repo)
    with pytest.raises(ValueError, match="cursor"):
        await service.list_history(cursor=cursor)
    repo.page_keys.assert_not_called()
