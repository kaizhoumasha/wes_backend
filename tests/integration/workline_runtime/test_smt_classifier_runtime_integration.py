from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceType
from src.app.workline.models.inbox import InboxStatus, WorklineInbox
from src.app.workline.models.outbox import WorklineOutbox
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.inbox_service import WorklineInboxService, inbox_service
from src.celery_app.tasks.workline import process_inbox_batch


@pytest.mark.asyncio
async def test_smt_classifier_scan_ng_then_command_result_completes_session(
    eager_celery: None,
    inline_task_runner: None,
    integration_session_factory,
    test_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    correlation_id = f"{test_prefix}_smt_classifier_flow"
    line_code = f"{test_prefix}_line"

    async with integration_session_factory() as setup_db:
        workline = WorkLine(
            line_code=line_code,
            line_name=f"{test_prefix}-line",
            line_type=LineType.AUTO,
        )
        setup_db.add(workline)
        await setup_db.flush()

        input_arm = Device(
            device_code=f"{test_prefix}_INPUT_ARM",
            device_name="进料机械臂",
            device_type=DeviceType.ROBOTIC_ARM,
            work_line_id=workline.id,
            device_role="INPUT_ARM",
            role_index=1,
        )
        setup_db.add(input_arm)
        await setup_db.flush()

        conveyor = Device(
            device_code=f"{test_prefix}_CONVEYOR",
            device_name="流水线",
            device_type=DeviceType.CONVEYOR,
            work_line_id=workline.id,
            device_role="CONVEYOR",
            role_index=1,
            upstream_device_id=input_arm.id,
        )
        setup_db.add(conveyor)
        await setup_db.flush()

        output_arm = Device(
            device_code=f"{test_prefix}_OUTPUT_ARM",
            device_name="出料机械臂",
            device_type=DeviceType.ROBOTIC_ARM,
            work_line_id=workline.id,
            device_role="OUTPUT_ARM",
            role_index=1,
            upstream_device_id=conveyor.id,
        )
        setup_db.add(output_arm)
        await setup_db.flush()

        workline.plugin_key = "smt_classifier"

        created = await inbox_service.create_device_event_inbox(
            db=setup_db,
            device_code=input_arm.device_code,
            event_type="SCAN_COMPLETED",
            timestamp=1710000000000,
            data={
                "location_id": "LEFT_STATION_INPUT",
                "barcode": "BC-NG-001",
                "scan_result": "NG",
            },
            correlation_id=correlation_id,
        )
        await setup_db.commit()
        first_inbox_id = created.id

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

    first_result = await process_inbox_batch(20)
    assert first_result["processed"] == 1
    assert first_result["success"] == 1
    assert first_result["failed"] == 0

    async with integration_session_factory() as verify_db:
        event_inbox = (
            await verify_db.execute(
                select(WorklineInbox).where(WorklineInbox.id == first_inbox_id)  # type: ignore[arg-type]
            )
        ).scalar_one()
        session = await verify_db.get(WorklineSession, event_inbox.session_id)
        command = (
            await verify_db.execute(
                select(DeviceCommand).where(DeviceCommand.correlation_id == correlation_id)  # type: ignore[arg-type]
            )
        ).scalar_one()
        outbox = (
            await verify_db.execute(
                select(WorklineOutbox).where(WorklineOutbox.session_id == session.id)  # type: ignore[arg-type]
            )
        ).scalar_one()

        assert event_inbox.status == InboxStatus.PROCESSED
        assert session is not None
        assert session.status == "WAITING_DEVICE_RESULT"
        assert session.current_wait_type == "COMMAND_RESULT"
        assert session.awaiting_command_id == command.id
        assert session.context_json["stage"] == "WAITING_PICK_PLACE"
        assert session.context_json["pick_place_reason"] == "SCAN_NG"
        assert command.params["action"] == "PICK_AND_PUT"
        assert outbox.target_code == f"{test_prefix}_INPUT_ARM"

        _ = await inbox_service.create_command_result_inbox(
            db=verify_db,
            command_code=command.command_code,
            device_code=input_arm.device_code,
            result="SUCCESS",
            finish_time=1710000005000,
            data={},
            command_type="PICK_AND_PUT",
            correlation_id=correlation_id,
        )
        await verify_db.commit()

    second_result = await process_inbox_batch(20)
    assert second_result["processed"] == 1
    assert second_result["success"] == 1
    assert second_result["failed"] == 0

    async with integration_session_factory() as verify_db:
        session = (
            await verify_db.execute(
                select(WorklineSession).where(WorklineSession.correlation_id == correlation_id)  # type: ignore[arg-type]
            )
        ).scalar_one()
        inboxes = list(
            (
                await verify_db.execute(
                    select(WorklineInbox).where(WorklineInbox.correlation_id == correlation_id)  # type: ignore[arg-type]
                )
            ).scalars()
        )

    assert session.status == "COMPLETED"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.context_json["stage"] == "COMPLETED"
    assert session.context_json["ng_handled"] is True
    assert all(inbox.status == InboxStatus.PROCESSED for inbox in inboxes)
