"""非生产调试过程数据清理服务契约测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.app.callback.models.callback_log import CallbackLog
from src.app.device.models import CommandStatus, Device, DeviceCommand, DeviceStatus
from src.app.handling.models import (
    BinTransitMembership,
    BinTransitMembershipStatus,
    BinTransitQueue,
)
from src.app.handling.models.operation import (
    HandlingMove,
    HandlingObjectType,
    HandlingOperation,
    HandlingStep,
    HandlingStepKind,
)
from src.app.rack.models import RackOperation, RackTask, RackTaskStatus, RackTaskType
from src.app.resource.models.resource import (
    BinCellOccupancy,
    BinCellOccupancyStatus,
    BinContentSnapshot,
    BinContentSnapshotItem,
    BinMaterialMount,
    BinMaterialMountStatus,
    RackBinMount,
    RackBinMountStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
    ResourceType,
    WmsConfirmationStatus,
)
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.wms_integration.models.evidence import WmsCallEvidence
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode, WorkLineRuntimeStatus
from src.app.workline.models.inbox import InboxKind, SourceSystem, WorklineInbox
from src.app.workline.models.object_transition_event import ObjectTransitionDomain, ObjectTransitionEvent
from src.app.workline.models.operation import DebugDataCleanupRequest, DebugDataCleanupResponse
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.services import DebugDataCleanupService, debug_data_cleanup_service
from src.utils.timezone import timezone


async def _create_debug_cleanup_graph(db_session):
    workline = WorkLine(
        line_code="WL-DEBUG-CLEANUP-AUTO",
        line_name="调试清理自动线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.AUTO,
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        stopped_at=timezone.now_for_db(),
        stopped_reason="DEBUG_DATA_TEST",
    )
    other_workline = WorkLine(
        line_code="WL-DEBUG-CLEANUP-OTHER",
        line_name="调试清理对照线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.AUTO,
    )
    db_session.add_all([workline, other_workline])
    await db_session.flush()

    device = Device(
        device_code="DEV-DEBUG-CLEANUP-AUTO",
        device_name="调试清理设备",
        work_line_id=workline.id,
        device_role="ROBOT_ARM",
        device_status=DeviceStatus.MAINTENANCE,
        error_code="MANUAL_MAINTENANCE",
        maintenance_mode=True,
    )
    db_session.add(device)
    await db_session.flush()

    session = WorklineSession(
        session_code="SES-DEBUG-CLEANUP-AUTO",
        workline_id=workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.AUTO,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        trace_id="trace-debug-cleanup-auto",
    )
    other_session = WorklineSession(
        session_code="SES-DEBUG-CLEANUP-OTHER",
        workline_id=other_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        trace_id="trace-debug-cleanup-other",
    )
    db_session.add_all([session, other_session])
    await db_session.flush()

    request_id = "req-debug-cleanup-auto"
    inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:event:debug-cleanup-auto",
        workline_id=workline.id,
        session_id=session.id,
        trace_id=session.trace_id,
        event_id="event-debug-cleanup-auto",
        payload_json={"source": "test"},
    )
    pending_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:event:debug-cleanup-pending",
        workline_id=None,
        session_id=None,
        trace_id="trace-debug-cleanup-pending",
        event_id="event-debug-cleanup-pending",
        payload_json={"source": "test"},
        status="RETRY",
        error_message=(
            f"Workline entry admission blocked by busy session: workline_id={workline.id}, business_key=DEBUG-PENDING"
        ),
    )
    outbox = SystemOutbox(
        session_id=session.id,
        workline_id=workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="debug-cleanup-auto-outbox",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=device.device_code,
        payload_json={"source": "test"},
        status=SystemOutboxStatus.NEW,
        trace_id=session.trace_id,
    )
    db_session.add_all([inbox, outbox])
    await db_session.flush()

    command = DeviceCommand(
        command_code="CMD-DEBUG-CLEANUP-AUTO",
        device_id=device.id,
        workline_id=workline.id,
        correlation_id=session.trace_id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={},
        status=CommandStatus.PENDING,
        trace_id=session.trace_id,
    )
    db_session.add(command)
    await db_session.flush()
    device.current_command_id = command.id
    session.awaiting_device_command_code = command.command_code

    rack_operation = RackOperation(
        operation_key="debug-cleanup-rack-op",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        workline_id=workline.id,
        workline_code=workline.line_code,
        material_session_id=session.id,
        trace_id=session.trace_id,
    )
    db_session.add(rack_operation)
    await db_session.flush()
    rack_task = RackTask(
        task_key="debug-cleanup-rack-task",
        operation_id=rack_operation.id,
        operation_key=rack_operation.operation_key,
        operation_type=rack_operation.operation_type,
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        workline_id=workline.id,
        workline_code=workline.line_code,
        material_session_id=session.id,
        outbox_id=outbox.id,
        dispatch_key="debug-cleanup-rack-dispatch",
        trace_id=session.trace_id,
    )

    handling_operation = HandlingOperation(
        operation_key="debug-cleanup-handling-op",
        operation_type="MOVE_BIN",
        object_type=HandlingObjectType.BIN,
        workline_id=workline.id,
        workline_code=workline.line_code,
        material_session_id=session.id,
        trace_id=session.trace_id,
    )
    db_session.add_all([rack_task, handling_operation])
    await db_session.flush()
    handling_move = HandlingMove(
        operation_id=handling_operation.id,
        operation_key=handling_operation.operation_key,
        sequence_no=1,
        object_type=HandlingObjectType.BIN,
        bin_code="BIN-DEBUG-CLEANUP",
        source_type="WORKLINE",
        source_code=workline.line_code,
        target_type="DEVICE",
        target_code=device.device_code,
    )
    handling_step = HandlingStep(
        operation_id=handling_operation.id,
        operation_key=handling_operation.operation_key,
        sequence_no=1,
        step_key="debug-cleanup-handling-step",
        step_kind=HandlingStepKind.DEVICE_COMMAND,
        outbox_id=outbox.id,
        command_id=command.id,
        dispatch_key="debug-cleanup-handling-dispatch",
        target_code=device.device_code,
        trace_id=session.trace_id,
    )
    db_session.add_all([handling_move, handling_step])
    await db_session.flush()

    bin_transit_membership = BinTransitMembership(
        bin_code="BIN-DEBUG-CLEANUP",
        workline_id=workline.id,
        workline_code=workline.line_code,
        current_queue=BinTransitQueue.WORKSTATION_WAIT_QUEUE,
        membership_status=BinTransitMembershipStatus.ACTIVE,
        handling_operation_id=handling_operation.id,
        handling_move_id=handling_move.id,
        trace_id=session.trace_id,
        workline_session_id=session.id,
        entered_at=timezone.now_for_db(),
        evidence_json={"source": "debug-cleanup-test"},
    )
    object_transition_event = ObjectTransitionEvent(
        domain=ObjectTransitionDomain.HANDLING,
        object_type="BIN_TRANSIT",
        object_key="BIN-DEBUG-CLEANUP",
        projection_type="QUEUE_MEMBERSHIP",
        from_state=None,
        to_state=BinTransitQueue.WORKSTATION_WAIT_QUEUE.value,
        reason_code="DEBUG_CLEANUP_TEST",
        source_event_id="debug-cleanup-transition",
        source_ref_json={"handling_operation_id": handling_operation.id, "handling_move_id": handling_move.id},
        evidence_json={"trace_id": session.trace_id},
        workline_session_id=session.id,
        trace_id=session.trace_id,
        occurred_at=timezone.now_for_db(),
        idempotency_key="debug-cleanup-transition-key",
    )

    resource_event = ResourceStateEvent(
        event_code="debug-cleanup-resource-event",
        event_type=ResourceStateEventType.BIN_ARRIVED,
        resource_type=ResourceType.BIN,
        resource_code="BIN-DEBUG-CLEANUP",
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="resource-event-debug-cleanup",
        trace_id=session.trace_id,
        workline_session_id=session.id,
        workline_id=workline.id,
        workline_code=workline.line_code,
        payload_json={},
        occurred_at=timezone.now_for_db(),
        received_at=timezone.now_for_db(),
    )
    db_session.add(resource_event)
    await db_session.flush()
    resource_transition_event = ObjectTransitionEvent(
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="BIN",
        object_key="BIN-DEBUG-CLEANUP",
        projection_type="BIN_PLACEMENT",
        from_state=None,
        to_state="ARRIVED",
        reason_code=ResourceStateEventType.BIN_ARRIVED.value,
        source_event_id="resource-event-debug-cleanup",
        source_ref_json={"resource_state_event_type": ResourceStateEventType.BIN_ARRIVED.value},
        evidence_json={"source": "debug-cleanup-resource-transition"},
        workline_session_id=None,
        trace_id=None,
        occurred_at=timezone.now_for_db(),
        idempotency_key="debug-cleanup-resource-transition-key",
    )
    rack_bin_mount = RackBinMount(
        rack_code="RACK-DEBUG-CLEANUP",
        rack_slot_code="A",
        bin_code="BIN-DEBUG-CLEANUP",
        mount_status=RackBinMountStatus.MOUNTED,
        source_system=ResourceSourceSystem.WMS,
        source_event_id="resource-rack-bin-mount-debug-cleanup",
        trace_id=session.trace_id,
        workline_session_id=session.id,
        started_at=timezone.now_for_db(),
    )
    bin_cell_occupancy = BinCellOccupancy(
        bin_code="BIN-DEBUG-CLEANUP",
        bin_cell_index="1",
        material_identity_key="CAP001|LOT-A|DC-A",
        material_code="CAP001",
        lot_code="LOT-A",
        reel_count=1,
        used_depth_mm=15,
        capacity_depth_mm=80,
        remaining_depth_mm=65,
        occupancy_status=BinCellOccupancyStatus.OCCUPIED,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="resource-bin-cell-occupancy-debug-cleanup",
        trace_id=session.trace_id,
        workline_session_id=session.id,
        started_at=timezone.now_for_db(),
    )
    bin_material_mount = BinMaterialMount(
        bin_code="BIN-DEBUG-CLEANUP",
        bin_cell_index="1",
        material_identity_key="CAP001|LOT-A|DC-A",
        pkg_code="PKG-DEBUG-CLEANUP-MOUNT",
        material_code="CAP001",
        lot_code="LOT-A",
        wms_confirmation_status=WmsConfirmationStatus.CONFIRMED,
        mount_status=BinMaterialMountStatus.OCCUPIED,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="resource-bin-material-mount-debug-cleanup",
        trace_id=session.trace_id,
        workline_session_id=session.id,
        started_at=timezone.now_for_db(),
    )
    snapshot = BinContentSnapshot(
        snapshot_id="debug-cleanup-snapshot",
        bin_code="BIN-DEBUG-CLEANUP",
        source_session_id=session.id,
        source_event_id=inbox.event_id,
        captured_at=timezone.now_for_db(),
        snapshot_hash="debug-cleanup-snapshot-hash",
    )
    snapshot_item = BinContentSnapshotItem(
        snapshot_id=snapshot.snapshot_id,
        bin_cell_index="1",
        pkg_code="PKG-DEBUG-CLEANUP",
    )
    callback_log = CallbackLog(
        callback_type="event",
        subject_code=device.device_code,
        request_body={"event_id": inbox.event_id},
        request_id=request_id,
        trace_id=session.trace_id,
        event_id=inbox.event_id,
        response_status=200,
        response_time_ms=3,
    )
    pending_callback_log = CallbackLog(
        callback_type="event",
        subject_code=device.device_code,
        request_body={"event_id": pending_inbox.event_id},
        request_id="req-debug-cleanup-pending",
        trace_id=pending_inbox.trace_id,
        event_id=pending_inbox.event_id,
        response_status=200,
        response_time_ms=3,
    )
    wms_evidence = WmsCallEvidence(
        evidence_key="debug-cleanup-wms-evidence",
        operation_name="debug-cleanup",
        request_id=request_id,
        trace_id=session.trace_id,
        dispatch_key=outbox.dispatch_key,
        request_snapshot={},
        response_snapshot={},
        request_hash="a" * 64,
    )
    db_session.add_all(
        [
            pending_inbox,
            bin_transit_membership,
            object_transition_event,
            resource_transition_event,
            rack_bin_mount,
            bin_cell_occupancy,
            bin_material_mount,
            snapshot,
            snapshot_item,
            callback_log,
            pending_callback_log,
            wms_evidence,
        ]
    )
    await db_session.flush()

    return {
        "workline_id": workline.id,
        "workline_code": workline.line_code,
        "other_workline_id": other_workline.id,
        "device_id": device.id,
        "ids": {
            WorklineSession: session.id,
            WorklineInbox: inbox.id,
            SystemOutbox: outbox.id,
            DeviceCommand: command.id,
            RackOperation: rack_operation.id,
            RackTask: rack_task.id,
            HandlingOperation: handling_operation.id,
            HandlingMove: handling_move.id,
            HandlingStep: handling_step.id,
            BinTransitMembership: bin_transit_membership.id,
            ObjectTransitionEvent: object_transition_event.id,
            ResourceStateEvent: resource_event.id,
            RackBinMount: rack_bin_mount.id,
            BinMaterialMount: bin_material_mount.id,
            BinCellOccupancy: bin_cell_occupancy.id,
            BinContentSnapshot: snapshot.id,
            BinContentSnapshotItem: snapshot_item.id,
            CallbackLog: callback_log.id,
            WmsCallEvidence: wms_evidence.id,
        },
        "resource_transition_event_id": resource_transition_event.id,
        "pending_inbox_id": pending_inbox.id,
        "pending_callback_log_id": pending_callback_log.id,
        "other_session_id": other_session.id,
    }


def test_debug_data_cleanup_request_defaults_to_dry_run() -> None:
    request = DebugDataCleanupRequest()

    assert request.dry_run is True
    assert request.confirmation is None


def test_debug_data_cleanup_response_shape_accepts_all_scope() -> None:
    response = DebugDataCleanupResponse(
        scope="ALL",
        workline_id=None,
        dry_run=True,
        deleted=False,
        counts={"sessions": 2},
        affected_workline_ids=[1, 2],
        affected_session_ids=[10, 11],
        message="dry-run only",
    )

    assert response.scope == "ALL"
    assert response.workline_id is None
    assert response.counts["sessions"] == 2
    assert response.affected_workline_ids == [1, 2]


@pytest.mark.asyncio
async def test_preview_workline_counts_non_simulation_process_data(db_session) -> None:
    graph = await _create_debug_cleanup_graph(db_session)

    result = await debug_data_cleanup_service.preview_workline(db_session, workline_id=graph["workline_id"])

    assert result.dry_run is True
    assert result.deleted is False
    assert result.scope == "WORKLINE"
    assert result.workline_id == graph["workline_id"]
    assert result.affected_workline_ids == [graph["workline_id"]]
    assert result.affected_session_ids == [graph["ids"][WorklineSession]]
    assert result.counts["sessions"] == 1
    assert result.counts["inboxes"] == 2
    assert result.counts["outboxes"] == 1
    assert result.counts["commands"] == 1
    assert result.counts["rack_operations"] == 1
    assert result.counts["rack_tasks"] == 1
    assert result.counts["handling_operations"] == 1
    assert result.counts["handling_moves"] == 1
    assert result.counts["handling_steps"] == 1
    assert result.counts["bin_transit_memberships"] == 1
    assert result.counts["object_transition_events"] == 2
    assert result.counts["resource_state_events"] == 1
    assert result.counts["bin_content_snapshots"] == 1
    assert result.counts["bin_content_snapshot_items"] == 1
    assert result.counts["callback_logs"] == 2
    assert result.counts["wms_call_evidence"] == 1


@pytest.mark.asyncio
async def test_cleanup_workline_deletes_process_data_and_preserves_source_data(db_session) -> None:
    graph = await _create_debug_cleanup_graph(db_session)

    result = await debug_data_cleanup_service.cleanup_workline(
        db_session,
        workline_id=graph["workline_id"],
        confirmation=graph["workline_code"],
    )
    db_session.expire_all()

    assert result.deleted is True
    assert result.counts["sessions"] == 1
    assert await db_session.get(WorkLine, graph["workline_id"]) is not None
    assert await db_session.get(WorkLine, graph["other_workline_id"]) is not None
    for model, item_id in graph["ids"].items():
        assert await db_session.get(model, item_id) is None
    assert await db_session.get(ObjectTransitionEvent, graph["resource_transition_event_id"]) is None
    assert await db_session.get(WorklineInbox, graph["pending_inbox_id"]) is None
    assert await db_session.get(CallbackLog, graph["pending_callback_log_id"]) is None
    assert await db_session.get(WorklineSession, graph["other_session_id"]) is not None

    device = await db_session.get(Device, graph["device_id"])
    assert device.current_command_id is None
    assert device.device_status == DeviceStatus.MAINTENANCE
    assert device.error_code == "MANUAL_MAINTENANCE"
    assert device.maintenance_mode is True

    workline = await db_session.get(WorkLine, graph["workline_id"])
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_at is not None
    assert workline.stopped_reason == "DEBUG_DATA_TEST"
    assert workline.resumed_at is None


@pytest.mark.asyncio
async def test_cleanup_workline_rejects_wrong_confirmation_without_deleting(db_session) -> None:
    graph = await _create_debug_cleanup_graph(db_session)

    with pytest.raises(ValueError, match="confirmation 必须等于工作线编码"):
        await debug_data_cleanup_service.cleanup_workline(
            db_session,
            workline_id=graph["workline_id"],
            confirmation="WRONG",
        )

    for model, item_id in graph["ids"].items():
        assert await db_session.get(model, item_id) is not None


@pytest.mark.asyncio
async def test_cleanup_all_requires_fixed_confirmation_and_clears_all_worklines(db_session) -> None:
    graph = await _create_debug_cleanup_graph(db_session)

    with pytest.raises(ValueError, match="CLEAR-ALL-DEBUG-DATA"):
        await debug_data_cleanup_service.cleanup_all(db_session, confirmation="WRONG")

    result = await debug_data_cleanup_service.cleanup_all(db_session, confirmation="CLEAR-ALL-DEBUG-DATA")
    db_session.expire_all()

    assert result.scope == "ALL"
    assert result.deleted is True
    assert graph["workline_id"] in result.affected_workline_ids
    assert graph["other_workline_id"] in result.affected_workline_ids
    remaining_sessions = (await db_session.execute(select(WorklineSession.id))).scalars().all()
    assert remaining_sessions == []
    assert await db_session.get(WorkLine, graph["workline_id"]) is not None
    assert await db_session.get(WorkLine, graph["other_workline_id"]) is not None


@pytest.mark.asyncio
async def test_cleanup_all_clears_orphan_process_resource_projections(db_session) -> None:
    orphan_mount = RackBinMount(
        rack_code="RACK-DEBUG-CLEANUP-ORPHAN",
        rack_slot_code="A",
        bin_code="BIN-DEBUG-CLEANUP-ORPHAN",
        mount_status=RackBinMountStatus.MOUNTED,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="resource-rack-bin-mount-debug-cleanup-orphan",
        trace_id="trace-debug-cleanup-orphan",
        session_id="999",
        started_at=timezone.now_for_db(),
    )
    wms_orphan_mount = RackBinMount(
        rack_code="RACK-DEBUG-CLEANUP-WMS",
        rack_slot_code="A",
        bin_code="BIN-DEBUG-CLEANUP-WMS",
        mount_status=RackBinMountStatus.MOUNTED,
        source_system=ResourceSourceSystem.WMS,
        source_event_id="resource-rack-bin-mount-debug-cleanup-wms",
        trace_id="trace-debug-cleanup-wms",
        session_id="1000",
        started_at=timezone.now_for_db(),
    )
    orphan_callback = CallbackLog(
        callback_type="event",
        subject_code="RS-INPUT-ARM-01",
        request_body={"event_id": "event-debug-cleanup-orphan"},
        request_id="req-debug-cleanup-orphan",
        trace_id="trace-debug-cleanup-orphan",
        event_id="event-debug-cleanup-orphan",
        response_status=200,
        response_time_ms=3,
    )
    empty_callback = CallbackLog(
        callback_type="event",
        subject_code="UNKNOWN",
        request_body={},
        request_id="req-debug-cleanup-empty",
        response_status=200,
        response_time_ms=3,
    )
    db_session.add_all([orphan_mount, wms_orphan_mount, orphan_callback, empty_callback])
    await db_session.flush()
    orphan_mount_id = orphan_mount.id
    wms_orphan_mount_id = wms_orphan_mount.id
    orphan_callback_id = orphan_callback.id
    empty_callback_id = empty_callback.id

    result = await debug_data_cleanup_service.cleanup_all(db_session, confirmation="CLEAR-ALL-DEBUG-DATA")
    db_session.expire_all()

    assert result.counts["rack_bin_mounts"] == 1
    assert result.counts["callback_logs"] == 1
    assert await db_session.get(RackBinMount, orphan_mount_id) is None
    assert await db_session.get(RackBinMount, wms_orphan_mount_id) is not None
    assert await db_session.get(CallbackLog, orphan_callback_id) is None
    assert await db_session.get(CallbackLog, empty_callback_id) is not None


@pytest.mark.asyncio
async def test_cleanup_workline_does_not_match_pending_inbox_for_workline_id_prefix(db_session) -> None:
    graph = await _create_debug_cleanup_graph(db_session)
    confusing_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:event:debug-cleanup-confusing-workline",
        workline_id=None,
        session_id=None,
        trace_id="trace-debug-cleanup-confusing",
        event_id="event-debug-cleanup-confusing",
        payload_json={"source": "test"},
        status="RETRY",
        error_message=(
            f"Workline entry admission blocked by busy session: workline_id={graph['workline_id']}0, business_key=X"
        ),
    )
    db_session.add(confusing_inbox)
    await db_session.flush()
    confusing_inbox_id = confusing_inbox.id

    _ = await debug_data_cleanup_service.cleanup_workline(
        db_session,
        workline_id=graph["workline_id"],
        confirmation=graph["workline_code"],
    )
    db_session.expire_all()

    assert await db_session.get(WorklineInbox, confusing_inbox_id) is not None


def test_debug_data_cleanup_service_exports_are_available() -> None:
    assert isinstance(debug_data_cleanup_service, DebugDataCleanupService)
