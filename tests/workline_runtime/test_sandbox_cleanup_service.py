"""沙箱工作线清理服务契约测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.app.device.models import CommandStatus, Device, DeviceCommand, DeviceStatus
from src.app.rack.models import RackTask, RackTaskStatus, RackTaskType
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode, WorkLineRuntimeStatus
from src.app.workline.models.bin_cell_reservation import WorklineBinCellReservation
from src.app.workline.models.diagnostic import DiagnosticStatus, WorklineDiagnostic
from src.app.workline.models.dispatch_attempt import DispatchAttemptStatus, WorklineDispatchAttempt
from src.app.workline.models.inbox import InboxKind, SourceSystem, WorklineInbox
from src.app.workline.models.operation import SandboxCleanupRequest, SandboxCleanupResponse
from src.app.workline.models.runtime_hold import NgReturnItem, RuntimeHold, RuntimeHoldStatus, RuntimeHoldType
from src.app.workline.models.safety import WorklineSafetyIncident
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.workline.services import SandboxCleanupService, sandbox_cleanup_service
from src.utils.timezone import timezone


async def _create_executable_cleanup_graph(db_session):
    simulation_workline = WorkLine(
        line_code="WL-SANDBOX-CLEANUP-EXEC",
        line_name="沙箱清理执行线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.SIMULATION,
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        stopped_at=timezone.now_for_db(),
        stopped_reason="SANDBOX_TEST_HOLD",
    )
    auto_workline = WorkLine(
        line_code="WL-AUTO-CLEANUP-EXEC",
        line_name="自动清理执行对照线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.AUTO,
    )
    db_session.add_all([simulation_workline, auto_workline])
    await db_session.flush()

    sandbox_device = Device(
        device_code="DEV-SANDBOX-CLEANUP-EXEC",
        device_name="沙箱清理执行设备",
        work_line_id=simulation_workline.id,
        device_role="ROBOT_ARM",
        device_status=DeviceStatus.RUNNING,
        error_code="SANDBOX_BUSY",
    )
    auto_device = Device(
        device_code="DEV-AUTO-CLEANUP-EXEC",
        device_name="自动清理执行对照设备",
        work_line_id=auto_workline.id,
        device_role="ROBOT_ARM",
        device_status=DeviceStatus.RUNNING,
        error_code=None,
    )
    db_session.add_all([sandbox_device, auto_device])
    await db_session.flush()

    sandbox_session = WorklineSession(
        session_code="SES-SANDBOX-CLEANUP-EXEC",
        workline_id=simulation_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        trace_id="trace-sandbox-cleanup-exec",
    )
    auto_session = WorklineSession(
        session_code="SES-AUTO-CLEANUP-EXEC",
        workline_id=auto_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.AUTO,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        trace_id="trace-auto-cleanup-exec",
    )
    db_session.add_all([sandbox_session, auto_session])
    await db_session.flush()

    sandbox_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.MANUAL,
        source_message_id="sandbox:event:cleanup-exec",
        workline_id=simulation_workline.id,
        session_id=sandbox_session.id,
        trace_id=sandbox_session.trace_id,
        event_id="sandbox:event:cleanup-exec",
        payload_json={"sandbox_mode": True},
    )
    auto_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:event:cleanup-exec",
        workline_id=auto_workline.id,
        session_id=auto_session.id,
        trace_id=auto_session.trace_id,
        event_id="device:event:cleanup-exec",
        payload_json={},
    )
    sandbox_outbox = SystemOutbox(
        session_id=sandbox_session.id,
        workline_id=simulation_workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="sandbox-cleanup-exec-outbox",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=sandbox_device.device_code,
        payload_json={"sandbox_mode": True},
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
    )
    auto_outbox = SystemOutbox(
        session_id=auto_session.id,
        workline_id=auto_workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="auto-cleanup-exec-outbox",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code=auto_device.device_code,
        payload_json={},
        status=SystemOutboxStatus.NEW,
    )
    db_session.add_all([sandbox_inbox, auto_inbox, sandbox_outbox, auto_outbox])
    await db_session.flush()

    sandbox_command = DeviceCommand(
        command_code="CMD-SANDBOX-CLEANUP-EXEC",
        device_id=sandbox_device.id,
        workline_id=simulation_workline.id,
        session_id=sandbox_session.session_code,
        session_id_int=sandbox_session.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={},
        status=CommandStatus.PENDING,
        trace_id=sandbox_session.trace_id,
    )
    auto_command = DeviceCommand(
        command_code="CMD-AUTO-CLEANUP-EXEC",
        device_id=auto_device.id,
        workline_id=auto_workline.id,
        session_id=auto_session.session_code,
        session_id_int=auto_session.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={},
        status=CommandStatus.PENDING,
        trace_id=auto_session.trace_id,
    )
    db_session.add_all([sandbox_command, auto_command])
    await db_session.flush()

    command_linked_inbox = WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:result:cleanup-exec",
        workline_id=simulation_workline.id,
        device_id=sandbox_device.id,
        command_id=sandbox_command.id,
        trace_id=sandbox_session.trace_id,
        event_id="device:result:cleanup-exec",
        payload_json={},
    )
    db_session.add(command_linked_inbox)
    await db_session.flush()

    sandbox_device.current_command_id = sandbox_command.id
    auto_device.current_command_id = auto_command.id
    sandbox_session.awaiting_command_id = sandbox_command.id
    auto_session.awaiting_command_id = auto_command.id

    sandbox_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.OPEN,
        workline_id=simulation_workline.id,
        session_id=sandbox_session.id,
        trace_id=sandbox_session.trace_id,
        source_kind="TEST",
        source_reason="SANDBOX_CLEANUP_EXEC",
        source_idempotency_key="sandbox-cleanup-exec-hold",
        source_inbox_id=sandbox_inbox.id,
        source_outbox_id=sandbox_outbox.id,
        source_command_id=sandbox_command.id,
    )
    auto_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.OPEN,
        workline_id=auto_workline.id,
        session_id=auto_session.id,
        trace_id=auto_session.trace_id,
        source_kind="TEST",
        source_reason="AUTO_CLEANUP_EXEC",
        source_idempotency_key="auto-cleanup-exec-hold",
    )
    db_session.add_all([sandbox_hold, auto_hold])
    await db_session.flush()
    auto_outbox.blocked_by_runtime_hold_id = sandbox_hold.id
    auto_hold.reopened_from_hold_id = sandbox_hold.id

    reopened_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.REOPENED,
        workline_id=simulation_workline.id,
        session_id=sandbox_session.id,
        trace_id=sandbox_session.trace_id,
        source_kind="TEST",
        source_reason="SANDBOX_CLEANUP_EXEC_REOPENED",
        source_idempotency_key="sandbox-cleanup-exec-hold-reopened",
        reopened_from_hold_id=sandbox_hold.id,
    )
    sandbox_outbox.blocked_by_runtime_hold_id = sandbox_hold.id
    db_session.add(reopened_hold)
    await db_session.flush()

    sandbox_ng_item = NgReturnItem(
        source_workline_id=simulation_workline.id,
        source_session_id=sandbox_session.id,
        source_command_id=sandbox_command.id,
        material_identity_key="MAT-SANDBOX-CLEANUP-EXEC",
        material_identity_json={"pkg_code": "PKG-SANDBOX-CLEANUP-EXEC"},
        created_from_runtime_hold_id=sandbox_hold.id,
    )
    sandbox_reservation = WorklineBinCellReservation(
        reservation_key="sandbox-cleanup-exec-reservation",
        workline_id=simulation_workline.id,
        workline_code=simulation_workline.line_code,
        session_id=sandbox_session.id,
        pkg_code="PKG-SANDBOX-CLEANUP-EXEC",
        bin_code="BIN-SANDBOX-CLEANUP-EXEC",
        bin_cell_index="1",
        reserved_at=timezone.now_for_db(),
    )
    sandbox_timeline = WorklineTimeline(
        session_id=sandbox_session.id,
        workline_id=simulation_workline.id,
        trace_id=sandbox_session.trace_id,
        seq_no=1,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_RECEIVED,
        actor_type=TimelineActorType.DEVICE,
        status=TimelineStatus.SUCCESS,
        related_inbox_id=sandbox_inbox.id,
    )
    sandbox_diagnostic = WorklineDiagnostic(
        diagnostic_key="sandbox-cleanup-exec-diagnostic",
        session_id=sandbox_session.id,
        inbox_id=sandbox_inbox.id,
        outbox_id=sandbox_outbox.id,
        workline_id=simulation_workline.id,
        diagnostic_code="SANDBOX_CLEANUP_EXEC",
        error_domain="TEST",
        severity="WARN",
        recoverability="MANUAL",
        problem_class="SOFTWARE",
        owner="WES",
        status=DiagnosticStatus.ACTIVE,
        message="sandbox cleanup execute diagnostic",
    )
    sandbox_dispatch_attempt = WorklineDispatchAttempt(
        outbox_id=sandbox_outbox.id,
        dispatch_key=sandbox_outbox.dispatch_key,
        attempt_no=1,
        lease_token="sandbox-cleanup-exec-lease",
        status=DispatchAttemptStatus.DISPATCHING,
        target_type="DEVICE",
        target_code=sandbox_outbox.target_code,
        started_at=timezone.now_for_db(),
    )
    sandbox_rack_task = RackTask(
        task_key="sandbox-cleanup-exec-rack-task",
        operation_key="sandbox-cleanup-exec-rack-op",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        workline_id=simulation_workline.id,
        workline_code=simulation_workline.line_code,
        material_session_id=sandbox_session.id,
        outbox_id=sandbox_outbox.id,
    )
    sandbox_safety_incident = WorklineSafetyIncident(
        workline_id=simulation_workline.id,
        reason="SANDBOX_CLEANUP_EXEC",
        trigger_payload_json={"source": "sandbox"},
        source_inbox_id=sandbox_inbox.id,
        source_command_id=sandbox_command.id,
    )
    source_ref_safety_incident = WorklineSafetyIncident(
        workline_id=simulation_workline.id,
        reason="SANDBOX_CLEANUP_EXEC_SOURCE_REF",
        trigger_payload_json={},
        source_command_id=sandbox_command.id,
    )
    auto_safety_incident = WorklineSafetyIncident(
        workline_id=auto_workline.id,
        reason="AUTO_CLEANUP_EXEC",
        trigger_payload_json={"source": "device"},
    )
    db_session.add_all(
        [
            sandbox_ng_item,
            sandbox_reservation,
            sandbox_timeline,
            sandbox_diagnostic,
            sandbox_dispatch_attempt,
            sandbox_rack_task,
            sandbox_safety_incident,
            source_ref_safety_incident,
            auto_safety_incident,
        ]
    )
    await db_session.flush()
    simulation_workline.active_safety_incident_id = sandbox_safety_incident.id
    await db_session.flush()

    return {
        "simulation_workline_id": simulation_workline.id,
        "auto_workline_id": auto_workline.id,
        "sandbox_device_id": sandbox_device.id,
        "auto_device_id": auto_device.id,
        "sandbox_ids": {
            WorklineSession: sandbox_session.id,
            WorklineInbox: sandbox_inbox.id,
            SystemOutbox: sandbox_outbox.id,
            DeviceCommand: sandbox_command.id,
            RuntimeHold: sandbox_hold.id,
            NgReturnItem: sandbox_ng_item.id,
            RackTask: sandbox_rack_task.id,
            WorklineBinCellReservation: sandbox_reservation.id,
            WorklineTimeline: sandbox_timeline.id,
            WorklineDiagnostic: sandbox_diagnostic.id,
            WorklineDispatchAttempt: sandbox_dispatch_attempt.id,
            WorklineSafetyIncident: sandbox_safety_incident.id,
        },
        "command_linked_inbox_id": command_linked_inbox.id,
        "reopened_hold_id": reopened_hold.id,
        "source_ref_safety_incident_id": source_ref_safety_incident.id,
        "auto_ids": {
            WorklineSession: auto_session.id,
            WorklineInbox: auto_inbox.id,
            SystemOutbox: auto_outbox.id,
            DeviceCommand: auto_command.id,
            RuntimeHold: auto_hold.id,
            WorklineSafetyIncident: auto_safety_incident.id,
        },
    }


def test_sandbox_cleanup_request_defaults_to_dry_run() -> None:
    request = SandboxCleanupRequest()

    assert request.dry_run is True
    assert request.confirmation is None


def test_sandbox_cleanup_response_shape_accepts_counts() -> None:
    response = SandboxCleanupResponse(
        workline_id=45,
        dry_run=True,
        deleted=False,
        counts={"sessions": 1, "inboxes": 2, "outboxes": 1},
        affected_session_ids=[93],
        message="dry-run only",
    )

    assert response.counts["sessions"] == 1
    assert response.counts["inboxes"] == 2
    assert response.deleted is False
    assert response.dry_run is True
    assert response.affected_session_ids == [93]


@pytest.mark.asyncio
async def test_preview_cleanup_counts_only_simulation_workline_sandbox_data(db_session) -> None:
    simulation_workline = WorkLine(
        line_code="WL-SANDBOX-CLEANUP",
        line_name="沙箱清理线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.SIMULATION,
    )
    auto_workline = WorkLine(
        line_code="WL-AUTO-CLEANUP",
        line_name="自动清理对照线",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.AUTO,
    )
    db_session.add_all([simulation_workline, auto_workline])
    await db_session.flush()

    sandbox_device = Device(
        device_code="DEV-SANDBOX-CLEANUP",
        device_name="沙箱清理设备",
        work_line_id=simulation_workline.id,
        device_role="ROBOT_ARM",
    )
    auto_device = Device(
        device_code="DEV-AUTO-CLEANUP",
        device_name="自动清理对照设备",
        work_line_id=auto_workline.id,
        device_role="ROBOT_ARM",
    )
    db_session.add_all([sandbox_device, auto_device])
    await db_session.flush()

    sandbox_session = WorklineSession(
        session_code="SES-SANDBOX-CLEANUP",
        workline_id=simulation_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.RUNNING,
        trace_id="trace-sandbox-cleanup",
    )
    ignored_session = WorklineSession(
        session_code="SES-SANDBOX-CLEANUP-IGNORED",
        workline_id=simulation_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        trace_id="trace-sandbox-cleanup-ignored",
    )
    auto_session = WorklineSession(
        session_code="SES-AUTO-CLEANUP",
        workline_id=auto_workline.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        trace_id="trace-auto-cleanup",
    )
    db_session.add_all([sandbox_session, ignored_session, auto_session])
    await db_session.flush()

    sandbox_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.MANUAL,
        source_message_id="sandbox:event:cleanup",
        workline_id=simulation_workline.id,
        session_id=sandbox_session.id,
        trace_id=sandbox_session.trace_id,
        event_id="sandbox:event:cleanup",
        payload_json={"sandbox_mode": True},
    )
    auto_inbox = WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system=SourceSystem.DEVICE,
        source_message_id="device:event:cleanup",
        workline_id=auto_workline.id,
        session_id=auto_session.id,
        trace_id=auto_session.trace_id,
        event_id="device:event:cleanup",
        payload_json={},
    )
    sandbox_outbox = SystemOutbox(
        session_id=sandbox_session.id,
        workline_id=simulation_workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="sandbox-cleanup-outbox",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="SANDBOX-DEVICE",
        payload_json={"sandbox_mode": True},
        status=SystemOutboxStatus.NEW,
    )
    auto_outbox = SystemOutbox(
        session_id=auto_session.id,
        workline_id=auto_workline.id,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        dispatch_key="auto-cleanup-outbox",
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="AUTO-DEVICE",
        payload_json={},
        status=SystemOutboxStatus.NEW,
    )
    sandbox_command = DeviceCommand(
        command_code="CMD-SANDBOX-CLEANUP",
        device_id=sandbox_device.id,
        workline_id=simulation_workline.id,
        session_id=sandbox_session.session_code,
        session_id_int=sandbox_session.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={},
        status=CommandStatus.PENDING,
        trace_id=sandbox_session.trace_id,
    )
    auto_command = DeviceCommand(
        command_code="CMD-AUTO-CLEANUP",
        device_id=auto_device.id,
        workline_id=auto_workline.id,
        session_id=auto_session.session_code,
        session_id_int=auto_session.id,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={},
        status=CommandStatus.PENDING,
        trace_id=auto_session.trace_id,
    )
    sandbox_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.OPEN,
        workline_id=simulation_workline.id,
        session_id=sandbox_session.id,
        trace_id=sandbox_session.trace_id,
        source_kind="TEST",
        source_reason="SANDBOX_CLEANUP",
        source_idempotency_key="sandbox-cleanup-hold",
    )
    auto_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.OPEN,
        workline_id=auto_workline.id,
        session_id=auto_session.id,
        trace_id=auto_session.trace_id,
        source_kind="TEST",
        source_reason="AUTO_CLEANUP",
        source_idempotency_key="auto-cleanup-hold",
    )
    sandbox_rack_task = RackTask(
        task_key="sandbox-cleanup-rack-task",
        operation_key="sandbox-cleanup-rack-op",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        workline_id=simulation_workline.id,
        workline_code=simulation_workline.line_code,
        material_session_id=sandbox_session.id,
    )
    auto_rack_task = RackTask(
        task_key="auto-cleanup-rack-task",
        operation_key="auto-cleanup-rack-op",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        workline_id=auto_workline.id,
        workline_code=auto_workline.line_code,
        material_session_id=auto_session.id,
    )
    db_session.add_all(
        [
            sandbox_inbox,
            auto_inbox,
            sandbox_outbox,
            auto_outbox,
            sandbox_command,
            auto_command,
            sandbox_hold,
            auto_hold,
            sandbox_rack_task,
            auto_rack_task,
        ]
    )
    await db_session.flush()

    source_ref_hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        status=RuntimeHoldStatus.OPEN,
        workline_id=simulation_workline.id,
        session_id=None,
        trace_id=sandbox_session.trace_id,
        source_kind="TEST",
        source_reason="SANDBOX_SOURCE_REF",
        source_idempotency_key="sandbox-cleanup-hold-source-ref",
        source_inbox_id=sandbox_inbox.id,
        source_outbox_id=sandbox_outbox.id,
        source_command_id=sandbox_command.id,
    )
    sandbox_ng_item = NgReturnItem(
        source_workline_id=simulation_workline.id,
        source_session_id=sandbox_session.id,
        source_command_id=sandbox_command.id,
        material_identity_key="MAT-SANDBOX-CLEANUP",
        material_identity_json={"pkg_code": "PKG-SANDBOX-CLEANUP"},
        created_from_runtime_hold_id=sandbox_hold.id,
    )
    sandbox_reservation = WorklineBinCellReservation(
        reservation_key="sandbox-cleanup-reservation",
        workline_id=simulation_workline.id,
        workline_code=simulation_workline.line_code,
        session_id=sandbox_session.id,
        pkg_code="PKG-SANDBOX-CLEANUP",
        bin_code="BIN-SANDBOX-CLEANUP",
        bin_cell_index="1",
        reserved_at=timezone.now_for_db(),
    )
    sandbox_timeline = WorklineTimeline(
        session_id=sandbox_session.id,
        workline_id=simulation_workline.id,
        trace_id=sandbox_session.trace_id,
        seq_no=1,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.INGEST,
        action_type=TimelineActionType.EVENT_RECEIVED,
        actor_type=TimelineActorType.DEVICE,
        status=TimelineStatus.SUCCESS,
        related_inbox_id=sandbox_inbox.id,
    )
    source_ref_timeline = WorklineTimeline(
        session_id=ignored_session.id,
        workline_id=simulation_workline.id,
        trace_id=sandbox_session.trace_id,
        seq_no=1,
        occurred_at=timezone.now_for_db(),
        stage=TimelineStage.DISPATCH_PREPARE,
        action_type=TimelineActionType.COMMAND_SENT,
        actor_type=TimelineActorType.ORCHESTRATOR,
        status=TimelineStatus.SUCCESS,
        related_command_id=sandbox_command.id,
    )
    sandbox_diagnostic = WorklineDiagnostic(
        diagnostic_key="sandbox-cleanup-diagnostic",
        session_id=None,
        inbox_id=sandbox_inbox.id,
        outbox_id=sandbox_outbox.id,
        workline_id=simulation_workline.id,
        diagnostic_code="SANDBOX_CLEANUP",
        error_domain="TEST",
        severity="WARN",
        recoverability="MANUAL",
        problem_class="SOFTWARE",
        owner="WES",
        status=DiagnosticStatus.ACTIVE,
        message="sandbox cleanup diagnostic",
    )
    sandbox_dispatch_attempt = WorklineDispatchAttempt(
        outbox_id=sandbox_outbox.id,
        dispatch_key=sandbox_outbox.dispatch_key,
        attempt_no=1,
        lease_token="sandbox-cleanup-lease",
        status=DispatchAttemptStatus.DISPATCHING,
        target_type="DEVICE",
        target_code=sandbox_outbox.target_code,
        started_at=timezone.now_for_db(),
    )
    sandbox_rack_task_by_outbox = RackTask(
        task_key="sandbox-cleanup-rack-task-outbox",
        operation_key="sandbox-cleanup-rack-op-outbox",
        operation_type="REPLACE_CLASSIFIER_WORK_RACK",
        sequence_no=1,
        task_type=RackTaskType.ALLOCATE_AND_MOVE_RACK,
        task_status=RackTaskStatus.REQUESTED,
        workline_id=simulation_workline.id,
        workline_code=simulation_workline.line_code,
        material_session_id=None,
        outbox_id=sandbox_outbox.id,
    )
    sandbox_safety_incident = WorklineSafetyIncident(
        workline_id=simulation_workline.id,
        reason="SANDBOX_CLEANUP",
        trigger_payload_json={"source": "sandbox"},
    )
    ignored_safety_incident = WorklineSafetyIncident(
        workline_id=simulation_workline.id,
        reason="NOT_SANDBOX",
        trigger_payload_json={"source": "device"},
    )
    db_session.add_all(
        [
            source_ref_hold,
            sandbox_ng_item,
            sandbox_reservation,
            sandbox_timeline,
            source_ref_timeline,
            sandbox_diagnostic,
            sandbox_dispatch_attempt,
            sandbox_rack_task_by_outbox,
            sandbox_safety_incident,
            ignored_safety_incident,
        ]
    )
    await db_session.flush()

    result = await sandbox_cleanup_service.preview_cleanup(db_session, workline_id=simulation_workline.id)

    assert result.dry_run is True
    assert result.deleted is False
    assert result.affected_session_ids == [sandbox_session.id]
    assert result.message == "已预览工作线沙箱清理范围，未删除数据"
    assert set(result.counts) == {
        "sessions",
        "inboxes",
        "outboxes",
        "commands",
        "runtime_holds",
        "ng_return_items",
        "rack_tasks",
        "bin_cell_reservations",
        "timelines",
        "diagnostics",
        "dispatch_attempts",
        "safety_incidents",
    }
    assert result.counts["sessions"] == 1
    assert result.counts["inboxes"] == 1
    assert result.counts["outboxes"] == 1
    assert result.counts["commands"] == 1
    assert result.counts["runtime_holds"] == 2
    assert result.counts["ng_return_items"] == 1
    assert result.counts["rack_tasks"] == 2
    assert result.counts["bin_cell_reservations"] == 1
    assert result.counts["timelines"] == 2
    assert result.counts["diagnostics"] == 1
    assert result.counts["dispatch_attempts"] == 1
    assert result.counts["safety_incidents"] == 1


@pytest.mark.asyncio
async def test_preview_cleanup_rejects_non_simulation_workline(db_session) -> None:
    auto_workline = WorkLine(
        line_code="WL-AUTO-CLEANUP-REJECT",
        line_name="自动线拒绝清理",
        line_type=LineType.AUTO,
        plugin_key="test_workline_plugin",
        contract_version="1.0",
        run_mode=WorkLineRunMode.AUTO,
    )
    db_session.add(auto_workline)
    await db_session.flush()

    with pytest.raises(ValueError, match="仅允许 SIMULATION 工作线"):
        await sandbox_cleanup_service.preview_cleanup(db_session, workline_id=auto_workline.id)


@pytest.mark.asyncio
async def test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state(db_session) -> None:
    graph = await _create_executable_cleanup_graph(db_session)
    simulation_workline = await db_session.get(WorkLine, graph["simulation_workline_id"])
    simulation_workline_id = simulation_workline.id
    simulation_workline_code = simulation_workline.line_code

    result = await sandbox_cleanup_service.cleanup_workline(
        db_session,
        workline_id=simulation_workline_id,
        confirmation=simulation_workline_code,
    )
    db_session.expire_all()

    assert result.dry_run is False
    assert result.deleted is True
    assert result.workline_id == simulation_workline_id
    assert result.message == "已清理该 SIMULATION 工作线的沙箱运行时数据，并重置工作线运行状态"
    assert result.counts["sessions"] == 1
    assert result.counts["inboxes"] == 2
    assert result.counts["outboxes"] == 1
    assert result.counts["commands"] == 1
    assert result.counts["runtime_holds"] == 2
    assert result.counts["safety_incidents"] == 2

    for model, item_id in graph["sandbox_ids"].items():
        assert await db_session.get(model, item_id) is None
    assert await db_session.get(RuntimeHold, graph["reopened_hold_id"]) is None
    assert await db_session.get(WorklineInbox, graph["command_linked_inbox_id"]) is None
    assert await db_session.get(WorklineSafetyIncident, graph["source_ref_safety_incident_id"]) is None

    for model, item_id in graph["auto_ids"].items():
        assert await db_session.get(model, item_id) is not None
    auto_outbox = await db_session.get(SystemOutbox, graph["auto_ids"][SystemOutbox])
    assert auto_outbox.blocked_by_runtime_hold_id is None
    auto_hold = await db_session.get(RuntimeHold, graph["auto_ids"][RuntimeHold])
    assert auto_hold.reopened_from_hold_id is None

    refreshed_workline = await db_session.get(WorkLine, graph["simulation_workline_id"])
    assert refreshed_workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    assert refreshed_workline.active_safety_incident_id is None
    assert refreshed_workline.stopped_at is None
    assert refreshed_workline.stopped_reason is None
    assert refreshed_workline.resumed_at is None
    assert refreshed_workline.start_admission_status is None
    assert refreshed_workline.start_admission_message is None
    assert refreshed_workline.start_admission_failed_device_code is None
    assert refreshed_workline.start_admission_checked_at is None

    sandbox_device = await db_session.get(Device, graph["sandbox_device_id"])
    assert sandbox_device.current_command_id is None
    assert sandbox_device.device_status == DeviceStatus.IDLE
    assert sandbox_device.error_code is None

    auto_device = await db_session.get(Device, graph["auto_device_id"])
    auto_command_id = graph["auto_ids"][DeviceCommand]
    assert auto_device.current_command_id == auto_command_id
    assert auto_device.device_status == DeviceStatus.RUNNING
    auto_session = await db_session.get(WorklineSession, graph["auto_ids"][WorklineSession])
    assert auto_session.awaiting_command_id == auto_command_id


@pytest.mark.asyncio
async def test_cleanup_workline_rejects_wrong_confirmation_without_deleting_data(db_session) -> None:
    graph = await _create_executable_cleanup_graph(db_session)
    simulation_workline = await db_session.get(WorkLine, graph["simulation_workline_id"])
    simulation_workline_id = simulation_workline.id

    with pytest.raises(ValueError, match="清理确认失败：confirmation 必须等于工作线编码"):
        await sandbox_cleanup_service.cleanup_workline(
            db_session,
            workline_id=simulation_workline_id,
            confirmation="WRONG-CODE",
        )
    db_session.expire_all()

    for model, item_id in graph["sandbox_ids"].items():
        assert await db_session.get(model, item_id) is not None
    assert await db_session.get(RuntimeHold, graph["reopened_hold_id"]) is not None
    assert await db_session.get(WorklineInbox, graph["command_linked_inbox_id"]) is not None
    assert await db_session.get(WorklineSafetyIncident, graph["source_ref_safety_incident_id"]) is not None
    auto_outbox = await db_session.get(SystemOutbox, graph["auto_ids"][SystemOutbox])
    assert auto_outbox.blocked_by_runtime_hold_id == graph["sandbox_ids"][RuntimeHold]
    auto_hold = await db_session.get(RuntimeHold, graph["auto_ids"][RuntimeHold])
    assert auto_hold.reopened_from_hold_id == graph["sandbox_ids"][RuntimeHold]

    sandbox_device = await db_session.get(Device, graph["sandbox_device_id"])
    sandbox_command_id = graph["sandbox_ids"][DeviceCommand]
    assert sandbox_device.current_command_id == sandbox_command_id
    assert sandbox_device.device_status == DeviceStatus.RUNNING
    assert sandbox_device.error_code == "SANDBOX_BUSY"

    remaining_sandbox_sessions = (
        (
            await db_session.execute(
                select(WorklineSession.id).where(WorklineSession.workline_id == simulation_workline_id)
            )
        )
        .scalars()
        .all()
    )
    assert remaining_sandbox_sessions == [graph["sandbox_ids"][WorklineSession]]


def test_sandbox_cleanup_service_exports_are_available() -> None:
    assert isinstance(sandbox_cleanup_service, SandboxCleanupService)
