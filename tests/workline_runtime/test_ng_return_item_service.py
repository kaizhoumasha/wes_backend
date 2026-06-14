import importlib
from typing import cast

import pytest
from sqlalchemy import select

from src.app.device.models import CommandStatus, Device, DeviceCommand
from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.inbox import InboxKind, SourceSystem, WorklineInbox
from src.app.workline.models.runtime_hold import NgReasonSource, NgReturnItem, NgReturnItemStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services.ng_return_item_service import NgMaterialConflictError, NgReturnItemService
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    NG_REASON_LOCAL_SORTING_NG,
    PHASE_WAITING_SOURCE_PICK,
    ROLE_SORTING_TARGET_ARM,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
    SORTING_CONTEXT_SCHEMA_VERSION,
)
from src.workline_runtime.material_identity import MaterialIdentity, MaterialIdentityResolutionStatus
from src.workline_runtime.ng_reason import NgReasonDefinition
from src.workline_runtime.ng_reason import NgReasonSource as RuntimeNgReasonSource

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("registered_test_workline_plugin")]


async def _create_scan_ng_fixture(db_session):
    workline = WorkLine(
        line_code="WL-NG-ITEM",
        line_name="WL-NG-ITEM",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
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
            "part_no": "PART-001",
            "vendor_part_no": "VENDOR-PART-001",
            "quantity": "7387",
            "production_date": "122625",
            "lot_no": "8904936031",
            "item_id": "ITEM-6",
        },
    }
    session = WorklineSession(
        session_code="SES-NG-ITEM",
        workline_id=cast("int", workline.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        context_json={
            "barcode": "ITEM-6",
            "barcodes": ["PART-001", "VENDOR-PART-001", "7387", "122625", "8904936031", "ITEM-6"],
            "initial_payload": initial_payload,
            "ng_reason": "SCAN_NG",
            "pick_place_reason": "SCAN_NG",
            "scan_ng_reason_code": "BARCODE_INVALID",
            "scan_ng_reason_message": "条码格式错误: ITEM-6",
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
        params={"barcode": "ITEM-6", "source_type": "INPUT_PLATFORM", "target_type": "NG_PLATFORM"},
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


async def _create_second_scan_ng_source(
    db_session, workline: WorkLine, source_session: WorklineSession, source_inbox: WorklineInbox
):
    session = WorklineSession(
        session_code="SES-NG-ITEM-OTHER",
        workline_id=cast("int", workline.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        context_json=dict(source_session.context_json),
        trace_id="trace-ng-item-other",
    )
    db_session.add(session)
    await db_session.flush()

    command = DeviceCommand(
        command_code="CMD-NG-ITEM-OTHER",
        device_id=cast("int", source_inbox.device_id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type="PICK_AND_PUT",
        params={"barcode": "ITEM-6", "source_type": "INPUT_PLATFORM", "target_type": "NG_PLATFORM"},
        status=CommandStatus.COMPLETED,
        trace_id=session.trace_id,
    )
    db_session.add(command)
    await db_session.flush()

    inbox = WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system=SourceSystem.MANUAL,
        source_message_id="sandbox:result:ng-item-other",
        workline_id=cast("int", workline.id),
        device_id=source_inbox.device_id,
        command_id=cast("int", command.id),
        session_id=cast("int", session.id),
        trace_id=session.trace_id,
        event_id="sandbox:result:CMD-NG-ITEM-OTHER",
        payload_json={
            "command_code": command.command_code,
            "device_code": "ARM03-NG-ITEM",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {"actual_qty": 1},
        },
    )
    db_session.add(inbox)
    await db_session.flush()
    return session, inbox


async def _create_smt_local_ng_fixture(db_session):
    workline = WorkLine(
        line_code="WL-SMT-NG-ITEM",
        line_name="WL-SMT-NG-ITEM",
        line_type=LineType.AUTO,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
    )
    db_session.add(workline)
    await db_session.flush()

    device = Device(
        device_code="SMT-TARGET-ARM-01",
        device_name="SMT TARGET ARM",
        work_line_id=workline.id,
        device_role=ROLE_SORTING_TARGET_ARM,
        role_index=1,
    )
    db_session.add(device)
    await db_session.flush()

    current_material = {
        "source_bin_code": "SRC-BIN-01",
        "source_cell_code": "A01",
        "material_identity_key": "mid:pkg-001",
        "actual_material_identity_key": "mid:actual-other",
        "pkg_code": "PKG-001",
        "reel_thickness_mm": "7.125",
        "ng_status": "MOVING_TO_NG",
    }
    ng_command_payload = {
        "command_code": "CMD-SMT-NG-ITEM",
        "device_code": device.device_code,
        "command_type": COMMAND_NG_PLACE,
        "result": "SUCCESS",
        "data": {"ng_location": "NG-01"},
    }
    session = WorklineSession(
        session_code="SES-SMT-NG-ITEM",
        workline_id=cast("int", workline.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "stations": {"scan_platform": "EMPTY"},
                "business_phase": PHASE_WAITING_SOURCE_PICK,
            },
            "ng_reason": NG_REASON_LOCAL_SORTING_NG,
            "pick_place_reason": NG_REASON_LOCAL_SORTING_NG,
            "scan_ng_reason_code": NG_REASON_LOCAL_SORTING_NG,
            "scan_ng_reason_message": "本地 NG 放置成功",
            "source_payload": {
                "ng_command_payload": ng_command_payload,
                "current_material": current_material,
            },
        },
        trace_id="trace-smt-ng-item",
    )
    db_session.add(session)
    await db_session.flush()

    command = DeviceCommand(
        command_code="CMD-SMT-NG-ITEM",
        device_id=cast("int", device.id),
        workline_id=cast("int", workline.id),
        session_id=session.session_code,
        session_id_int=cast("int", session.id),
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type=COMMAND_NG_PLACE,
        params={
            "material_identity_key": "mid:actual-other",
            "pkg_code": "PKG-001",
            "ng_reason_code": NG_REASON_LOCAL_SORTING_NG,
        },
        status=CommandStatus.COMPLETED,
        trace_id=session.trace_id,
    )
    db_session.add(command)
    await db_session.flush()

    inbox = WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system=SourceSystem.MANUAL,
        source_message_id="sandbox:result:smt-ng-item",
        workline_id=cast("int", workline.id),
        device_id=cast("int", device.id),
        command_id=cast("int", command.id),
        session_id=cast("int", session.id),
        trace_id=session.trace_id,
        event_id="sandbox:result:CMD-SMT-NG-ITEM",
        payload_json=ng_command_payload,
    )
    db_session.add(inbox)
    await db_session.flush()
    return workline, session, inbox


async def test_record_scan_ng_completion_uses_registry_helpers_and_creates_material_ng_item(
    db_session,
    monkeypatch,
) -> None:
    ng_item_module = importlib.import_module("src.app.workline.services.ng_return_item_service")

    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)
    material_calls: list[str | None] = []
    reason_calls: list[str | None] = []

    def _resolve_material(plugin_key, input_value):
        material_calls.append(plugin_key)
        assert input_value.session_context["scan_ng_reason_code"] == "BARCODE_INVALID"
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
            idempotency_key="test-material:ITEM-6",
            display={"item_id": "ITEM-6"},
            raw_evidence_hash="sha256:test",
        )

    def _list_reasons(plugin_key):
        reason_calls.append(plugin_key)
        return (
            NgReasonDefinition(
                canonical_code="BARCODE_INVALID",
                label="条码无效",
                source=RuntimeNgReasonSource.PLUGIN,
                plugin_key="test_workline_plugin",
                contract_version="1.0",
            ),
        )

    monkeypatch.setattr(ng_item_module, "resolve_workline_material_identity", _resolve_material, raising=False)
    monkeypatch.setattr(ng_item_module, "list_workline_ng_reasons", _list_reasons, raising=False)

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
    assert item.material_identity_key == "test-material:ITEM-6"
    assert item.material_identity_json["idempotency_key"] == "test-material:ITEM-6"
    assert item.material_identity_json["display"]["item_id"] == "ITEM-6"
    assert item.created_from_runtime_hold_id is None
    assert item.ng_reason_source == NgReasonSource.PLUGIN
    assert item.ng_reason_code == "BARCODE_INVALID"
    assert item.ng_reason_label == "条码无效"
    assert item.status == NgReturnItemStatus.WAITING_REWORK
    assert item.physical_handoff_evidence_json["source"] == "WORKFLOW_SCAN_NG"
    assert item.physical_handoff_evidence_json["source_inbox_id"] == inbox.id
    assert material_calls == ["test_workline_plugin"]
    assert reason_calls == ["test_workline_plugin"]


async def test_record_scan_ng_completion_unknown_plugin_uses_registry_fallbacks(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)
    initial_payload = session.context_json["initial_payload"]
    workline.plugin_key = "missing_plugin"
    workline.contract_version = "missing.v1"
    session.plugin_key = "missing_plugin"
    session.contract_version = "missing.v1"
    session.context_json = {
        "initial_payload": initial_payload,
        "ng_reason": "UNKNOWN_PHYSICAL_STATE",
    }

    item = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )

    assert item is not None
    assert item.material_identity_key == f"workflow-ng:missing_plugin:session:{session.id}"
    assert item.material_identity_json["resolution_status"] == "MISSING"
    assert item.material_identity_json["fallback_identity"] is True
    assert item.material_identity_json["fallback_source"] == "SESSION"
    assert item.ng_reason_source == NgReasonSource.RUNTIME
    assert item.ng_reason_code == "UNKNOWN_PHYSICAL_STATE"


async def test_record_smt_local_ng_completion_without_legacy_transition_creates_material_ng_item(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_smt_local_ng_fixture(db_session)

    item = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition=None,
        occurred_at=timezone.now_for_db(),
    )

    assert item is not None
    assert item.source_workline_id == workline.id
    assert item.source_session_id == session.id
    assert item.source_command_id == inbox.command_id
    assert item.source_event_id == inbox.event_id
    assert item.material_identity_key == f"workflow-ng:{SMT_SORTING_INBOUND_PLUGIN_KEY}:session:{session.id}"
    assert item.material_identity_json["resolution_status"] == "MISSING"
    assert item.material_identity_json["fallback_source"] == "SESSION"
    assert item.created_from_runtime_hold_id is None
    assert item.ng_reason_source == NgReasonSource.PLUGIN
    assert item.ng_reason_code == NG_REASON_LOCAL_SORTING_NG
    assert item.ng_reason_label == "本地分拣 NG"
    assert item.status == NgReturnItemStatus.WAITING_REWORK
    assert item.physical_handoff_evidence_json["source"] == "WORKFLOW_SCAN_NG"
    assert item.physical_handoff_evidence_json["source_inbox_id"] == inbox.id


async def test_record_ng_completion_without_transition_or_handoff_evidence_is_ignored(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)

    item = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition=None,
        occurred_at=timezone.now_for_db(),
    )

    assert item is None


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


async def test_record_scan_ng_completion_conflict_is_structured_for_different_source(db_session) -> None:
    service = NgReturnItemService()
    workline, session, inbox = await _create_scan_ng_fixture(db_session)
    existing = await service.record_completed_ng_flow(
        db_session,
        session=session,
        workline=workline,
        inbox=inbox,
        transition="pick_ng",
        occurred_at=timezone.now_for_db(),
    )
    other_session, other_inbox = await _create_second_scan_ng_source(db_session, workline, session, inbox)

    with pytest.raises(NgMaterialConflictError) as exc_info:
        await service.record_completed_ng_flow(
            db_session,
            session=other_session,
            workline=workline,
            inbox=other_inbox,
            transition="pick_ng",
            occurred_at=timezone.now_for_db(),
        )

    assert existing is not None
    conflict = exc_info.value
    assert conflict.reason_code == "NG_MATERIAL_CONFLICT"
    assert conflict.material_identity_key == "test-material:ITEM-6"
    assert conflict.existing_item_id == existing.id
    assert conflict.evidence["existing_source_session_id"] == session.id
    assert conflict.evidence["existing_source_command_id"] == inbox.command_id
    assert conflict.evidence["new_source_session_id"] == other_session.id
    assert conflict.evidence["new_source_command_id"] == other_inbox.command_id
    assert conflict.evidence["new_source_event_id"] == other_inbox.event_id
    assert conflict.evidence["expected_material_identity_key"] == "test-material:ITEM-6"
    assert conflict.evidence["actual_material_identity_key"] == "test-material:ITEM-6"
    assert conflict.evidence["scan_event_type"] == "SCAN_COMPLETED"
    assert conflict.evidence["scan_event_payload"]["data"]["item_id"] == "ITEM-6"
    assert conflict.evidence["command_result_payload"]["command_code"] == "CMD-NG-ITEM-OTHER"


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
                "part_no": "PART-001",
                "vendor_part_no": "VENDOR-PART-001",
                "quantity": "7387",
                "production_date": "122625",
                "lot_no": "8904936031",
            },
        },
        "scan_ng_reason_code": "BARCODE_INCOMPLETE",
        "scan_ng_reason_message": "条码不完整，缺失字段: item_id",
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
    assert item.material_identity_key == f"workflow-ng:test_workline_plugin:session:{session.id}"
    assert item.material_identity_json["resolution_status"] == "MISSING"
    assert item.material_identity_json["fallback_identity"] is True
    assert item.ng_reason_code == "BARCODE_INCOMPLETE"
