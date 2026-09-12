"""WorkLine 未闭合 execution owner 的单快照 PostgreSQL 合同。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    PositionProjection,
    WmsConfirmation,
)
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.resource.models import BinPlacement, RackPlacement, ResourceSourceSystem
from src.app.resource.repositories import bin_placement_repository, rack_placement_repository
from src.app.transport.contracts import TransportTaskStatus
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.workline_repository import WorkLineRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_snapshot_reports_execution_owners_and_positions_and_excludes_terminal_rows(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 29, 12)
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line = WorkLine(
                line_code=f"UNFINISHED-{identity[:20]}",
                line_name="Unfinished execution snapshot",
                line_type=LineType.AUTO,
            )
            db.add(line)
            await db.flush()
            device = Device(
                device_code=f"SNAPSHOT-DEVICE-{identity[:16]}",
                device_name="Snapshot device",
                work_line_id=line.id,
            )
            db.add(device)
            await db.flush()
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"UNFINISHED-EVIDENCE-{identity}",
                payload_digest="c" * 64,
                normalized_payload={"data": {}},
                received_at=now,
                device_code=device.device_code,
                workline_id=line.id,
                apply_status=InboundEvidenceApplyStatus.PENDING,
            )
            db.add(evidence)
            await db.flush()
            material = MaterialExecution(
                execution_code=f"UNFINISHED-MATERIAL-{identity}",
                material_trace_id=f"UNFINISHED-TRACE-{identity}",
                workline_id=line.id,
                admission_received_at=evidence.received_at,
                admission_evidence_id=evidence.id,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=evidence.id,
                status_changed_at=now,
            )
            projection = PositionProjection(
                object_type="BIN",
                object_id=f"BIN-{identity}",
                workline_id=line.id,
                position_json=None,
                position_unknown=True,
                source_operation_id=str(uuid4()),
                source_transport_task_id=f"MOVE-{identity}",
            )
            db.add_all([material, projection])
            await db.flush()
            command = DeviceCommand(
                command_code=f"UNFINISHED-COMMAND-{identity}",
                device_code=device.device_code,
                endpoint_base_url="http://snapshot-device:8080",
                command_timeout_ms=5000,
                status_max_age_ms=1000,
                workline_id=line.id,
                execution_ref_type="MATERIAL_EXECUTION",
                execution_ref_id=str(material.id),
                material_execution_id=material.id,
                contract_key="snapshot.contract",
                contract_version="1.0",
                task_type="PICK",
                params={},
                deadline_at=now + timedelta(minutes=1),
                payload_digest="d" * 64,
            )
            transport = TransportTask(
                transport_task_id=f"UNFINISHED-TRANSPORT-{identity}",
                client_request_id=f"UNFINISHED-REQUEST-{identity}",
                request_digest="e" * 64,
                kind="BIN_MOVE",
                caller_json={},
                request_json={},
                submit_operation_id="019d0000-0000-7000-8000-000000000001",
                submit_timestamp_ms=1,
                submit_request_body="{}",
                submit_request_body_digest="f" * 64,
                authority_workline_id=line.id,
                created_at=now,
                updated_at=now,
            )
            confirmation = WmsConfirmation(
                operation="snapshot.confirm@v1",
                operation_id=f"UNFINISHED-CONFIRMATION-{identity}",
                material_execution_id=material.id,
                request_digest="1" * 64,
                request_payload={},
                deadline_at=now + timedelta(minutes=1),
            )
            bin_placement = BinPlacement(
                bin_code=projection.object_id,
                position_type="WORKLINE_POSITION",
                position_code="TARGET-BIN-POSITION",
                workline_id=line.id,
                placement_status="ARRIVED",
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"TARGET-BIN-EVIDENCE-{identity}",
                started_at=now,
            )
            rack_placement = RackPlacement(
                rack_code=f"TARGET-RACK-{identity}",
                workline_id=line.id,
                position_code="TARGET-RACK-POSITION",
                placement_status="ARRIVED",
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"TARGET-RACK-EVIDENCE-{identity}",
                started_at=now,
            )
            db.add_all([command, transport, confirmation, bin_placement, rack_placement])
            await db.flush()

            repository = WorkLineRepository()
            summary = await repository.get_unfinished_workload_summary(db, line.id)

            assert summary["by_type"] == {
                "material_executions": 1,
                "device_commands": 1,
                "transport_tasks": 1,
                "inbound_evidences": 1,
                "wms_confirmations": 1,
                "picking_tasks": 0,
                "position_projections": 1,
            }
            assert summary["count"] == 6
            assert summary["sample"] == {
                "type": "material_execution",
                "id": str(material.id),
                "status": "CREATED",
                "identity": material.execution_code,
            }
            assert summary["samples"]["transport_tasks"] == {
                "type": "transport_task",
                "id": str(transport.id),
                "status": "PENDING",
                "identity": transport.transport_task_id,
            }
            second_bin_placement = BinPlacement(
                placeholder_key=f"TARGET-BIN-PLACEHOLDER-{identity}",
                position_type="WORKLINE_POSITION",
                position_code="TARGET-BIN-POSITION-2",
                workline_id=line.id,
                placement_status="UNKNOWN",
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"TARGET-BIN-EVIDENCE-2-{identity}",
                started_at=now,
            )
            second_rack_placement = RackPlacement(
                rack_code=f"TARGET-RACK-2-{identity}",
                workline_id=line.id,
                position_code="TARGET-RACK-POSITION-2",
                placement_status="UNKNOWN",
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"TARGET-RACK-EVIDENCE-2-{identity}",
                started_at=now,
            )
            db.add_all([second_bin_placement, second_rack_placement])
            await db.flush()
            assert await bin_placement_repository.get_active_workline_summary(db, line.id) == {
                "count": 2,
                "sample": {
                    "type": "bin_placement",
                    "id": str(bin_placement.id),
                    "status": "ARRIVED",
                    "identity": bin_placement.bin_code,
                },
            }
            assert await rack_placement_repository.get_active_workline_summary(db, line.id) == {
                "count": 2,
                "sample": {
                    "type": "rack_placement",
                    "id": str(rack_placement.id),
                    "status": "ARRIVED",
                    "identity": rack_placement.rack_code,
                },
            }
            second_bin_placement.ended_at = now
            second_rack_placement.ended_at = now
            await db.flush()
            active_objects = await repository.list_target_active_object_facts(db, workline_id=line.id)
            assert {(row["object_type"], row["object_key"]) for row in active_objects} == {
                ("MATERIAL_EXECUTION", material.material_trace_id),
                ("DEVICE_COMMAND", command.command_code),
                ("TRANSPORT_TASK", transport.transport_task_id),
                ("WMS_CONFIRMATION", confirmation.operation_id),
                ("BIN_RESOURCE", bin_placement.bin_code),
                ("RACK_RESOURCE", rack_placement.rack_code),
            }
            assert {row["owner_kind"] for row in active_objects} == {
                "MATERIAL_EXECUTION",
                "DEVICE_COMMAND",
                "TRANSPORT_TASK",
                "WMS_CONFIRMATION",
                "BIN_PLACEMENT",
                "RACK_PLACEMENT",
            }

            material.status = MaterialExecutionStatus.CLOSED
            material.closed_at = now
            projection.position_unknown = False
            projection.position_json = {"kind": "HANDOFF", "location_code": "OUTSIDE"}
            command.status = CommandStatus.SUCCEEDED
            command.completed_at = now
            transport.status = TransportTaskStatus.SUCCEEDED
            evidence.apply_status = InboundEvidenceApplyStatus.IGNORED
            confirmation.status = WmsConfirmationStatus.COMPLETED
            confirmation.completed_at = now
            await db.flush()

            terminal_summary = await repository.get_unfinished_workload_summary(db, line.id)
            assert terminal_summary["count"] == 0
            assert not any(terminal_summary["by_type"].values())
            assert terminal_summary["sample"] is None

            second_transport = TransportTask(
                transport_task_id=f"UNFINISHED-TRANSPORT-2-{identity}",
                client_request_id=f"UNFINISHED-REQUEST-2-{identity}",
                request_digest="2" * 64,
                kind="BIN_MOVE",
                caller_json={},
                request_json={},
                submit_operation_id="019d0000-0000-7000-8000-000000000002",
                submit_timestamp_ms=2,
                submit_request_body="{}",
                submit_request_body_digest="3" * 64,
                authority_workline_id=line.id,
                created_at=now,
                updated_at=now,
            )
            transport.status = TransportTaskStatus.PENDING
            db.add(second_transport)
            await db.flush()
            two_transport_summary = await repository.get_unfinished_workload_summary(db, line.id)
            assert two_transport_summary["count"] == 2
            assert two_transport_summary["by_type"]["transport_tasks"] == 2
            assert two_transport_summary["samples"]["transport_tasks"]["id"] == str(transport.id)
            transport.status = TransportTaskStatus.SUCCEEDED
            await db.delete(second_transport)
            await db.flush()

            async def assert_only(owner: str) -> None:
                current = await repository.get_unfinished_workload_summary(db, line.id)
                assert current["count"] == 1
                assert current["by_type"][owner] == 1
                assert not any(value for name, value in current["by_type"].items() if name != owner)

            for state in (value for value in MaterialExecutionStatus if value is not MaterialExecutionStatus.CLOSED):
                material.status = state
                material.closed_at = None
                await db.flush()
                await assert_only("material_executions")
            material.status = MaterialExecutionStatus.CLOSED
            material.closed_at = now

            projection.position_unknown = True
            projection.position_json = None
            await db.flush()
            await assert_only("position_projections")
            projection.position_unknown = False
            projection.position_json = {"kind": "HANDOFF", "location_code": "OUTSIDE"}

            for state in (
                CommandStatus.PENDING,
                CommandStatus.DISPATCHING,
                CommandStatus.ACKNOWLEDGED,
                CommandStatus.RECONCILING,
            ):
                command.status = state
                command.completed_at = None
                await db.flush()
                await assert_only("device_commands")
            command.status = CommandStatus.SUCCEEDED
            command.completed_at = now

            for state in (
                TransportTaskStatus.PENDING,
                TransportTaskStatus.ACCEPTED,
                TransportTaskStatus.RECONCILING,
            ):
                transport.status = state
                await db.flush()
                await assert_only("transport_tasks")
            transport.status = TransportTaskStatus.SUCCEEDED
            transport.outcome_version = 1
            transport.published_outcome_version = 0
            await db.flush()
            await assert_only("transport_tasks")
            transport.published_outcome_version = 1
            await db.flush()
            assert (await repository.get_unfinished_workload_summary(db, line.id))["count"] == 0

            for state in (InboundEvidenceApplyStatus.PENDING, InboundEvidenceApplyStatus.RECONCILING):
                evidence.apply_status = state
                await db.flush()
                await assert_only("inbound_evidences")
            evidence.apply_status = InboundEvidenceApplyStatus.APPLIED
            evidence.kind = InboundEvidenceKind.DEVICE_EVENT
            evidence.published_at = None
            await db.flush()
            await assert_only("inbound_evidences")
            evidence.kind = InboundEvidenceKind.DEVICE_RESULT
            evidence.material_execution_id = material.id
            await db.flush()
            await assert_only("inbound_evidences")
            evidence.apply_status = InboundEvidenceApplyStatus.IGNORED

            for state in (value for value in WmsConfirmationStatus if value is not WmsConfirmationStatus.COMPLETED):
                confirmation.status = state
                confirmation.completed_at = None
                await db.flush()
                await assert_only("wms_confirmations")
            confirmation.status = WmsConfirmationStatus.COMPLETED
            confirmation.completed_at = now
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_unbound_applied_device_result_is_diagnostic_not_unfinished_owner(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 29, 12)
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line = WorkLine(
                line_code=f"DIAGNOSTIC-{identity[:20]}",
                line_name="Diagnostic evidence snapshot",
                line_type=LineType.AUTO,
            )
            db.add(line)
            await db.flush()
            db.add(
                InboundEvidence(
                    kind=InboundEvidenceKind.DEVICE_RESULT,
                    source_identity=f"DIAGNOSTIC-EVIDENCE-{identity}",
                    payload_digest="c" * 64,
                    normalized_payload={"data": {}},
                    received_at=now,
                    workline_id=line.id,
                    device_code="SNAPSHOT-DEVICE",
                    command_code=f"DIAGNOSTIC-COMMAND-{identity}",
                    apply_status=InboundEvidenceApplyStatus.APPLIED,
                )
            )
            await db.flush()

            summary = await WorkLineRepository().get_unfinished_workload_summary(db, line.id)

            assert summary["by_type"]["inbound_evidences"] == 0
            assert summary["count"] == 0
            assert summary["sample"] is None
        finally:
            await transaction.rollback()


async def _seed_picking_snapshot(db, *, now: datetime):  # type: ignore[no-untyped-def]
    identity = uuid4().hex
    line = WorkLine(
        line_code=f"PICKING-SNAPSHOT-{identity[:16]}",
        line_name="Picking owner snapshot",
        line_type=LineType.MANUAL,
    )
    db.add(line)
    await db.flush()
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"PICKING-SNAPSHOT-EVIDENCE-{identity}",
        operation="outbound.picking_task.issued@v1",
        operation_id=identity,
        payload_digest="c" * 64,
        normalized_payload={"data": {}},
        received_at=now,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
        processed_at=now,
    )
    db.add(evidence)
    await db.flush()
    task = PickingTask(
        task_id=f"PICKING-SNAPSHOT-TASK-{identity}",
        task_type=PickingTaskType.MANUAL,
        status=PickingTaskStatus.EXECUTION_COMPLETED,
        queue_revision=1,
        dispatch_sequence=int(identity[:12], 16),
        issued_at_ms=1,
        issued_evidence_id=evidence.id,
        workline_id=line.id,
    )
    db.add(task)
    await db.flush()
    return line, task, evidence


@pytest.mark.asyncio
async def test_picking_snapshot_counts_bound_active_and_blocked_tasks_only(integration_session_factory) -> None:
    now = datetime(2026, 9, 6, 12)
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line, task, evidence = await _seed_picking_snapshot(db, now=now)
            other_line, other_task, _ = await _seed_picking_snapshot(db, now=now)
            other_task.status = PickingTaskStatus.PREPARING
            await db.flush()
            repository = WorkLineRepository()
            for state, blocked, expected in (
                (PickingTaskStatus.PREPARING, False, 1),
                (PickingTaskStatus.EXECUTING, False, 1),
                (PickingTaskStatus.EXECUTION_COMPLETED, False, 0),
                (PickingTaskStatus.EXECUTION_COMPLETED, True, 1),
            ):
                task.status = state
                task.plan_blocked_evidence_id = evidence.id if blocked else None
                await db.flush()
                summary = await repository.get_unfinished_workload_summary(db, line.id)
                assert summary["count"] == expected
                assert summary["by_type"]["picking_tasks"] == expected
                assert not any(value for owner, value in summary["by_type"].items() if owner != "picking_tasks")
                if expected:
                    assert summary["samples"]["picking_tasks"] == {
                        "type": "picking_task",
                        "id": str(task.id),
                        "status": state.value,
                        "identity": task.task_id,
                    }
                    assert summary["sample"] == summary["samples"]["picking_tasks"]
                else:
                    assert summary["sample"] is None
            task.status = PickingTaskStatus.QUEUED
            task.workline_id = None
            task.plan_blocked_evidence_id = None
            await db.flush()
            assert (await repository.get_unfinished_workload_summary(db, line.id))["count"] == 0
            other_summary = await repository.get_unfinished_workload_summary(db, other_line.id)
            assert other_summary["count"] == 1
            assert other_summary["samples"]["picking_tasks"]["id"] == str(other_task.id)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_picking_confirmation_snapshot_needs_no_material_owner_and_counts_each_identity_once(
    integration_session_factory,
) -> None:
    now = datetime(2026, 9, 6, 12)
    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            line, task, _ = await _seed_picking_snapshot(db, now=now)
            _, other_task, _ = await _seed_picking_snapshot(db, now=now)
            confirmation = WmsConfirmation(
                operation="outbound.picking_task.prepare@v1",
                operation_id=uuid4().hex,
                picking_task_id=task.id,
                request_digest="d" * 64,
                request_payload={},
                deadline_at=now + timedelta(minutes=1),
            )
            other_confirmation = WmsConfirmation(
                operation="outbound.picking_task.prepare@v1",
                operation_id=uuid4().hex,
                picking_task_id=other_task.id,
                request_digest="e" * 64,
                request_payload={},
                deadline_at=now + timedelta(minutes=1),
            )
            db.add_all([confirmation, other_confirmation])
            await db.flush()
            repository = WorkLineRepository()
            for state in WmsConfirmationStatus:
                confirmation.status = state
                confirmation.completed_at = now if state is WmsConfirmationStatus.COMPLETED else None
                await db.flush()
                summary = await repository.get_unfinished_workload_summary(db, line.id)
                expected = int(state is not WmsConfirmationStatus.COMPLETED)
                assert summary["count"] == expected
                assert summary["by_type"]["wms_confirmations"] == expected
                assert not any(value for owner, value in summary["by_type"].items() if owner != "wms_confirmations")
                if expected:
                    assert summary["samples"]["wms_confirmations"] == {
                        "type": "wms_confirmation",
                        "id": str(confirmation.id),
                        "status": state.value,
                        "identity": confirmation.operation_id,
                    }
                    assert summary["sample"] == summary["samples"]["wms_confirmations"]
                else:
                    assert summary["sample"] is None
        finally:
            await transaction.rollback()
