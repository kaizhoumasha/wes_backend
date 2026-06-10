from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.workline.models.inbox import InboxKind, InboxStatus, WorklineInbox
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.inbox_service import inbox_service
from src.celery_app.app import celery_app
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_process_inbox_batch_consumed_by_real_worker(
    celery_worker_process: dict[str, str],
    integration_session_factory,
    isolated_workline_inbox_queue: None,
    test_prefix: str,
) -> None:
    celery_app.loader.import_default_modules()
    trace_id = f"{test_prefix}_worker_process_inbox"
    line_code = f"{test_prefix}_line"
    device_code = f"{test_prefix}_DEVICE"

    async with integration_session_factory() as setup_db:
        line = WorkLine(
            line_code=line_code,
            line_name=f"{test_prefix}-line",
            line_type=LineType.AUTO,
            plugin_key=line_code,
            runtime_status=WorkLineRuntimeStatus.READY,
        )
        setup_db.add(line)
        await setup_db.flush()

        device = Device(
            device_code=device_code,
            device_name=f"{test_prefix}-scanner",
            work_line_id=line.id,
            device_role="SCANNER",
            host="127.0.0.1",
            port=18080,
        )
        setup_db.add(device)
        await setup_db.flush()

        created = await inbox_service.create_device_event_inbox(
            db=setup_db,
            device_code=device_code,
            event_type="MATERIAL_ARRIVED",
            timestamp=1710000000000,
            data={"event_id": f"{test_prefix}_evt"},
            trace_id=trace_id,
        )
        await setup_db.commit()
        inbox_id = created.id

    result = await asyncio.to_thread(
        lambda: celery_app.send_task(
            "src.celery_app.tasks.workline.process_inbox_batch",
            kwargs={"limit": 1},
            queue=celery_worker_process["queue"],
            routing_key=celery_worker_process["queue"],
        ).get(timeout=30, propagate=True)
    )
    assert result["processed"] == 1
    assert result["success"] == 1

    async with integration_session_factory() as verify_db:
        db_inbox = (
            await verify_db.execute(
                select(WorklineInbox).where(WorklineInbox.id == inbox_id)  # type: ignore[arg-type]
            )
        ).scalar_one()
        db_session = (
            await verify_db.execute(
                select(WorklineSession).where(WorklineSession.trace_id == trace_id)  # type: ignore[arg-type]
            )
        ).scalar_one()

    assert db_inbox.status == InboxStatus.PROCESSED
    assert db_inbox.workline_id == line.id
    assert db_session is not None
    assert db_session.workline_id == line.id
    assert db_session.plugin_key == line_code


@pytest.mark.asyncio
async def test_scan_timeouts_batch_consumed_by_real_worker(
    celery_worker_process: dict[str, str],
    integration_session_factory,
    isolated_workline_timeout_queue: None,
    test_prefix: str,
) -> None:
    celery_app.loader.import_default_modules()
    trace_id = f"{test_prefix}_worker_process_timeout"
    session_code = f"{test_prefix}_session"
    line_code = f"{test_prefix}_line_timeout"
    expired_deadline = timezone.now_for_db() - timedelta(minutes=10)

    async with integration_session_factory() as setup_db:
        line = WorkLine(
            line_code=line_code,
            line_name=f"{test_prefix}-timeout-line",
            line_type=LineType.AUTO,
        )
        setup_db.add(line)
        await setup_db.flush()

        device = Device(
            device_code=f"{test_prefix}_timeout_DEVICE",
            device_name=f"{test_prefix}-timeout-device",
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
            command_code=f"{test_prefix}_timeout_command",
            task_type="TEST_TIMEOUT",
            status=CommandStatus.ACK_RECEIVED,
            ack_received_at=timezone.now_for_db() - timedelta(minutes=9),
            trace_id=trace_id,
            workline_id=line.id,
            session_id_int=workline_session.id,
        )
        setup_db.add(command)
        await setup_db.flush()

        workline_session.awaiting_command_id = command.id
        await setup_db.commit()
        session_id = workline_session.id

    result = await asyncio.to_thread(
        lambda: celery_app.send_task(
            "src.celery_app.tasks.workline.scan_timeouts_batch",
            kwargs={"limit": 1},
            queue=celery_worker_process["queue"],
            routing_key=celery_worker_process["queue"],
        ).get(timeout=30, propagate=True)
    )
    assert result["scanned"] == 1
    assert result["timeouts_created"] == 1

    async with integration_session_factory() as verify_db:
        created_items = list(
            (
                await verify_db.execute(
                    select(WorklineInbox).where(
                        WorklineInbox.trace_id == trace_id,  # type: ignore[arg-type]
                        WorklineInbox.kind == InboxKind.TIMER_TIMEOUT,  # type: ignore[arg-type]
                    )
                )
            ).scalars()
        )

    assert created_items, "未找到由真实 worker 生成的 TIMER_TIMEOUT Inbox"
    timeout_inbox = created_items[0]
    assert timeout_inbox.session_id == session_id
    assert timeout_inbox.status == InboxStatus.NEW
    assert timeout_inbox.payload_json.get("message_type") == "TIMEOUT"
