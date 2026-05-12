from typing import cast

import pytest
from sqlalchemy import select

from src.app.device.models import CommandStatus, Device, DeviceCommand
from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.inbox import InboxKind, SourceSystem, WorklineInbox
from src.app.workline.models.runtime_hold import NgReasonSource, NgReturnItem, NgReturnItemStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services.ng_return_item_service import NgReturnItemService
from src.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


async def _create_scan_ng_fixture(db_session):
    workline = WorkLine(
        line_code="WL-NG-ITEM",
        line_name="WL-NG-ITEM",
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
        contract_version="1.0",
    )
    db_session.add(workline)
    await db_session.flush()

    device = Device(
        device_code="ARM03-NG-ITEM",
        device_name="ARM03",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        role_index=3,
    )
    db_session.add(device)
    await db_session.flush()

    initial_payload = {
        "event_type": "SCAN_COMPLETED",
        "device_code": device.device_code,
        "data": {
            "location": "ARM01",
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "Qty": "7387",
            "DateCode": "122625",
            "LotCode": "8904936031",
            "PkgID": "6",
        },
    }
    session = WorklineSession(
        session_code="SES-NG-ITEM",
        workline_id=cast("int", workline.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        context_json={
            "barcode": "6",
            "barcodes": ["620100L00-011-G", "CC0402JRNPO9BN220", "7387", "122625", "8904936031", "6"],
            "initial_payload": initial_payload,
            "ng_reason": "SCAN_NG",
            "pick_place_reason": "SCAN_NG",
            "scan_ng_reason_code": "BARCODE_INVALID",
            "scan_ng_reason_message": "条码格式错误: 6",
        },
        trace_id="trace-ng-item",
    )
    db_session.add(session)
    await db_session.flush()

    command = DeviceCommand(
        command_code="CMD-NG-ITEM",
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="PICK_AND_PUT",
        params={"barcode": "6", "source_type": "INPUT_PLATFORM", "target_type": "NG_PLATFORM"},
        status=CommandStatus.COMPLETED,
        trace_id=session.trace_id,
    )
    db_session.add(command)
    await db_session.flush()

    inbox = WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system=SourceSystem.MANUAL,
        source_message_id="sandbox:result:ng-item",
        workline_id=cast("int", workline.id),
        device_id=cast("int", device.id),
        command_id=cast("int", command.id),
        session_id=cast("int", session.id),
        trace_id=session.trace_id,
        event_id="sandbox:result:CMD-NG-ITEM",
        payload_json={
            "command_code": command.command_code,
            "device_code": device.device_code,
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {"actual_qty": 1},
        },
    )
    db_session.add(inbox)
    await db_session.flush()
    return workline, session, inbox


async def test_record_scan_ng_completion_creates_material_ng_item(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)

    item = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )

    assert item is not None
    assert item.source_workline_id == workline.id
    assert item.source_session_id == session.id
    assert item.source_command_id == inbox.command_id
    assert item.source_event_id == inbox.event_id
    assert item.material_identity_key == "smt:6"
    assert item.material_identity_json["idempotency_key"] == "smt:6"
    assert item.material_identity_json["display"]["PkgID"] == "6"
    assert item.created_from_runtime_hold_id is None
    assert item.ng_reason_source == NgReasonSource.PLUGIN
    assert item.ng_reason_code == "BARCODE_INVALID"
    assert item.ng_reason_label == "条码无效"
    assert item.status == NgReturnItemStatus.WAITING_REWORK
    assert item.physical_handoff_evidence_json["source"] == "WORKFLOW_SCAN_NG"
    assert item.physical_handoff_evidence_json["source_inbox_id"] == inbox.id


async def test_record_scan_ng_completion_is_idempotent_for_same_session_and_command(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)

    first = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )
    second = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )

    rows = list((await db_session.execute(select(NgReturnItem))).scalars().all())
    assert first is not None
    assert second is not None
    assert second.id == first.id
    assert len(rows) == 1


async def test_record_scan_ng_completion_uses_session_identity_when_material_identity_is_missing(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)
    session.context_json = {
        **session.context_json,
        "barcode": "",
        "barcodes": ["620100L00-011-G", "CC0402JRNPO9BN220", "7387", "122625", "8904936031"],
        "initial_payload": {
            "event_type": "SCAN_COMPLETED",
            "device_code": "ARM03-NG-ITEM",
            "data": {
                "location": "ARM01",
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
            },
        },
        "scan_ng_reason_code": "BARCODE_INCOMPLETE",
        "scan_ng_reason_message": "条码不完整，缺失字段: PkgID",
    }
    await db_session.flush()

    item = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )

    assert item is not None
    assert item.material_identity_key == f"workflow-ng:smt_classifier:session:{session.id}"
    assert item.material_identity_json["resolution_status"] == "MISSING"
    assert item.material_identity_json["fallback_identity"] is True
    assert item.ng_reason_code == "BARCODE_INCOMPLETE"
