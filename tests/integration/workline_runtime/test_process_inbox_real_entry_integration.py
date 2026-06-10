from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from src.app.device.models.device import Device
from src.app.workline.models.inbox import InboxStatus, WorklineInbox
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.inbox_service import inbox_service
from src.celery_app.tasks.workline import process_inbox_batch
from src.workline_runtime.orchestrator import set_allow_null_plugin


@pytest.mark.asyncio
async def test_process_inbox_batch_entry_marks_message_processed(
    eager_celery: None,
    integration_session_factory,
    isolated_workline_inbox_queue: None,
    test_prefix: str,
) -> None:
    trace_id = f"{test_prefix}_process_entry"
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

    set_allow_null_plugin(True)
    try:
        result = await asyncio.to_thread(process_inbox_batch, 1)
    finally:
        set_allow_null_plugin(False)
    assert result["processed"] == 1
    assert result["success"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["resource_wait"] == 0

    async with integration_session_factory() as verify_db:
        query = select(WorklineInbox).where(WorklineInbox.id == inbox_id)  # type: ignore[arg-type]
        db_inbox = (await verify_db.execute(query)).scalar_one()
        db_session = (
            await verify_db.execute(
                select(WorklineSession).where(WorklineSession.trace_id == trace_id)  # type: ignore[arg-type]
            )
        ).scalar_one()

    assert db_inbox.status == InboxStatus.PROCESSED
    assert db_inbox.processor_token is None
    assert db_inbox.processed_at is not None
    assert db_inbox.workline_id == line.id
    assert db_session is not None
    assert db_session.workline_id == line.id
    assert db_session.plugin_key == line_code
