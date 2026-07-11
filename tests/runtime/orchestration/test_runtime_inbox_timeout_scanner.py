"""Timeout scanner 写入 RuntimeInbox 的 producer 合同测试。"""

from __future__ import annotations

import importlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created", "expected_timeouts_created"),
    [(True, 1), (False, 0)],
    ids=["new-runtime-inbox", "existing-runtime-inbox"],
)
async def test_timeout_scanner_uses_runtime_inbox_acceptor_and_keeps_batch_statistics(
    monkeypatch,
    created: bool,
    expected_timeouts_created: int,
) -> None:
    """Session timeout 扫描必须切到 RuntimeInbox，且保留统计与批次提交语义。"""

    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.device.repositories.device_repository import device_repository
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import runtime_inbox_service
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.app.runtime.orchestration.services.inbox.inbox_service import inbox_service
    from src.app.sys.repositories import SystemOutboxRepository
    from src.celery_app.tasks.workline import TimeoutScanner

    event_stream_service_module = importlib.import_module("src.app.sys.services.event_stream_service")

    deadline_at = datetime(2026, 7, 11, 8, 0, 0)
    ack_received_at = datetime(2026, 7, 11, 7, 59, 0)
    session = SimpleNamespace(
        id=101,
        workline_id=11,
        deadline_at=deadline_at,
        trace_id="trace-timeout-scan-001",
        awaiting_device_command_code="CMD-SCAN-001",
        current_wait_type="DEVICE_RESULT",
        status="WAITING_DEVICE_RESULT",
    )
    command = SimpleNamespace(
        id=301,
        command_code="CMD-SCAN-001",
        device_id=201,
        status="ACK_RECEIVED",
        ack_received_at=ack_received_at,
    )
    device = SimpleNamespace(id=201, device_code="ARM_201")

    monkeypatch.setattr(
        WorklineSessionRepository,
        "get_timed_out_sessions",
        AsyncMock(return_value=[session]),
    )
    monkeypatch.setattr(
        DeviceCommandRepository,
        "get_by_command_code",
        AsyncMock(return_value=command),
    )
    monkeypatch.setattr(
        DeviceCommandRepository,
        "get_ack_timed_out_commands",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(device_repository, "get_by_id", AsyncMock(return_value=device))
    monkeypatch.setattr(SystemOutboxRepository, "get_by_dispatch_key", AsyncMock(return_value=None))

    accept_timeout = AsyncMock(return_value=SimpleNamespace(created=created))
    monkeypatch.setattr(runtime_inbox_service, "accept_timer_timeout", accept_timeout, raising=False)
    legacy_create_timeout = AsyncMock(return_value=SimpleNamespace(id=999))
    monkeypatch.setattr(inbox_service, "create_timeout_inbox", legacy_create_timeout)
    publish = AsyncMock()
    monkeypatch.setattr(event_stream_service_module, "publish_deferred_sse_events", publish)

    db = SimpleNamespace(commit=AsyncMock())
    result = await TimeoutScanner._scan(db, limit=25)

    assert result == {
        "scanned": 1,
        "timeouts_created": expected_timeouts_created,
        "ack_timeouts_reconciled": 0,
        "errors": 0,
    }
    accept_timeout.assert_awaited_once_with(
        db,
        session_id=101,
        workline_id=11,
        deadline_at=deadline_at,
        trace_id="trace-timeout-scan-001",
        wait_token="CMD-SCAN-001",
        wait_type="DEVICE_RESULT",
        awaiting_device_command_code="CMD-SCAN-001",
        command_code="CMD-SCAN-001",
        device_id=201,
        device_code="ARM_201",
        command_id=301,
        command_status="ACK_RECEIVED",
        ack_received_at=ack_received_at,
        auto_commit=False,
    )
    legacy_create_timeout.assert_not_awaited()
    db.commit.assert_awaited_once_with()
    publish.assert_awaited_once_with(db)
