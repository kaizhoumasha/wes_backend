from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.session_repository import WorklineSessionRepository
from src.celery_app.tasks.workline import scan_timeouts_batch
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_scan_timeouts_batch_creates_timeout_inbox_for_expired_session(
    eager_celery: None,
    integration_session_factory,
    isolated_workline_timeout_queue: None,
    test_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_id = f"{test_prefix}_timeout_chain"
    session_code = f"{test_prefix}_session"
    line_code = f"{test_prefix}_line"
    device_code = f"{test_prefix}_device"
    command_code = f"{test_prefix}_command"
    expired_deadline = timezone.now_for_db() - timedelta(minutes=10)

    async with integration_session_factory() as setup_db:
        line = WorkLine(
            line_code=line_code,
            line_name=f"{test_prefix}-line",
            line_type=LineType.AUTO,
        )
        setup_db.add(line)
        await setup_db.flush()

        device = Device(
            device_code=device_code,
            device_name=f"{test_prefix}-device",
            work_line_id=line.id,
            device_role="ROBOT_ARM",
        )
        setup_db.add(device)
        await setup_db.flush()

        workline_session = WorklineSession(
            session_code=session_code,
            workline_id=line.id,
            plugin_key="smt",
            status=SessionStatus.WAITING_DEVICE_RESULT,
            current_wait_type="DEVICE_CALLBACK",
            trace_id=trace_id,
            deadline_at=expired_deadline,
        )
        setup_db.add(workline_session)
        await setup_db.flush()

        command = DeviceCommand(
            device_id=device.id,
            command_code=command_code,
            task_type="TEST_TIMEOUT",
            status=CommandStatus.ACK_RECEIVED,
            ack_received_at=timezone.now_for_db() - timedelta(minutes=9),
            trace_id=trace_id,
            workline_id=line.id,
            correlation_id=workline_session.session_code,
        )
        setup_db.add(command)
        await setup_db.flush()

        workline_session.awaiting_device_command_code = command.command_code
        await setup_db.commit()
        session_id = workline_session.id

    async def get_target_timed_out_sessions(
        self: WorklineSessionRepository,
        db: Any,
        limit: int = 100,
    ) -> list[WorklineSession]:
        now = timezone.now_for_db()
        query = (
            select(WorklineSession)
            .where(
                WorklineSession.trace_id == trace_id,  # type: ignore[arg-type]
                WorklineSession.status == SessionStatus.WAITING_DEVICE_RESULT,  # type: ignore[arg-type]
                WorklineSession.deadline_at.is_not(None),  # type: ignore[arg-type]
                WorklineSession.deadline_at < now,  # type: ignore[arg-type]
            )
            .limit(limit)
        )
        return list((await db.execute(query)).scalars().all())

    monkeypatch.setattr(
        WorklineSessionRepository,
        "get_timed_out_sessions",
        get_target_timed_out_sessions,
    )

    result = await asyncio.to_thread(scan_timeouts_batch, 1)
    assert result["scanned"] == 1
    assert result["timeouts_created"] == 1

    async with integration_session_factory() as verify_db:
        query = select(WorklineInbox).where(
            WorklineInbox.trace_id == trace_id,  # type: ignore[arg-type]
            WorklineInbox.kind == InboxKind.TIMER_TIMEOUT,  # type: ignore[arg-type]
        )
        created_items = list((await verify_db.execute(query)).scalars().all())

    assert created_items, "未找到为超时 Session 生成的 TIMER_TIMEOUT Inbox"

    timeout_inbox = created_items[0]
    assert timeout_inbox.session_id == session_id
    assert timeout_inbox.status == InboxStatus.NEW
    assert timeout_inbox.source_system == SourceSystem.SYSTEM
    assert timeout_inbox.payload_json.get("message_type") == "TIMEOUT"
    assert timeout_inbox.payload_json.get("session_id") == session_id
