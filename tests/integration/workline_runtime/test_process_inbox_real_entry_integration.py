from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from src.app.device.models.device import Device
from src.app.workline.models.inbox import InboxStatus, WorklineInbox
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.inbox_service import WorklineInboxService, inbox_service
from src.celery_app.tasks.workline import process_inbox_batch


@pytest.mark.asyncio
async def test_process_inbox_batch_entry_marks_message_processed(
    eager_celery: None,
    inline_task_runner: None,
    integration_session_factory,
    test_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    correlation_id = f"{test_prefix}_process_entry"
    line_code = f"{test_prefix}_line"
    device_code = f"{test_prefix}_DEVICE"

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
            correlation_id=correlation_id,
        )
        await setup_db.commit()
        inbox_id = created.id

    async def get_target_messages(
        self: WorklineInboxService,
        db: Any,
        limit: int = 10,
    ) -> list[WorklineInbox]:
        query = (
            select(WorklineInbox)
            .where(
                WorklineInbox.correlation_id == correlation_id,  # type: ignore[arg-type]
                WorklineInbox.status == InboxStatus.NEW,  # type: ignore[arg-type]
            )
            .order_by(WorklineInbox.received_at)
            .limit(limit)
        )
        return list((await db.execute(query)).scalars().all())

    monkeypatch.setattr(WorklineInboxService, "get_new_messages", get_target_messages)

    result = await process_inbox_batch(20)
    assert result["processed"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0

    async with integration_session_factory() as verify_db:
        query = select(WorklineInbox).where(WorklineInbox.id == inbox_id)  # type: ignore[arg-type]
        db_inbox = (await verify_db.execute(query)).scalar_one()
        db_session = await verify_db.get(WorklineSession, db_inbox.session_id)

    assert db_inbox.status == InboxStatus.PROCESSED
    assert db_inbox.processor_token
    assert db_inbox.processed_at is not None
    assert db_inbox.device_id == device.id
    assert db_inbox.session_id is not None
    assert db_session is not None
    assert db_session.workline_id == line.id
    assert db_session.plugin_key == line_code
