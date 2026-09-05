"""WorkLine 未闭合 execution owner 的单快照 PostgreSQL 合同。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.execution.models import (
    BinExecution,
    BinExecutionStatus,
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    WmsConfirmation,
)
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.resource.models import BinPlacement, RackPlacement, ResourceSourceSystem
from src.app.transport.contracts import TransportTaskStatus
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask as _PickingTask
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding, LineRunEpochStatus
from src.app.workline.models.safety import WorklineSafetyIncident
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.workline_repository import WorkLineRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_snapshot_reports_all_seven_execution_owners_and_excludes_terminal_rows(
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
            epoch = LineRunEpoch(
                epoch_code=f"UNFINISHED-EPOCH-{identity}",
                workline_id=line.id,
                plugin_key="snapshot_test",
                plugin_version="1.0",
                flow_mode="GENERIC",
                topology_digest="a" * 64,
                configuration_digest="b" * 64,
                configuration_snapshot_json={},
                started_at=now,
            )
            db.add(epoch)
            await db.flush()
            device = Device(
                device_code=f"SNAPSHOT-DEVICE-{identity[:16]}",
                device_name="Snapshot device",
                work_line_id=line.id,
                device_role="SNAPSHOT_DEVICE",
            )
            db.add(device)
            await db.flush()
            binding = LineRunEpochDeviceBinding(
                line_run_epoch_id=epoch.id,
                device_id=device.id,
                device_code=device.device_code,
                device_role="SNAPSHOT_DEVICE",
                endpoint_base_url="http://snapshot-device:8080",
                contract_key="snapshot.contract",
                contract_version="1.0",
                status_max_age_ms=1_000,
                command_timeout_ms=5_000,
            )
            db.add(binding)
            await db.flush()
            evidence = InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"UNFINISHED-EVIDENCE-{identity}",
                payload_digest="c" * 64,
                normalized_payload={"data": {}},
                received_at=now,
                line_run_epoch_id=epoch.id,
                device_code=device.device_code,
                apply_status=InboundEvidenceApplyStatus.PENDING,
            )
            db.add(evidence)
            await db.flush()
            material = MaterialExecution(
                execution_code=f"UNFINISHED-MATERIAL-{identity}",
                material_trace_id=f"UNFINISHED-TRACE-{identity}",
                workline_id=line.id,
                line_run_epoch_id=epoch.id,
                admission_received_at=evidence.received_at,
                admission_evidence_id=evidence.id,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=evidence.id,
                status_changed_at=now,
            )
            bin_execution = BinExecution(
                execution_code=f"UNFINISHED-BIN-{identity}",
                bin_id=f"BIN-{identity}",
                workline_id=line.id,
                line_run_epoch_id=epoch.id,
                started_at=now,
            )
            db.add_all([material, bin_execution])
            await db.flush()
            command = DeviceCommand(
                command_code=f"UNFINISHED-COMMAND-{identity}",
                device_code=device.device_code,
                device_binding_id=binding.id,
                line_run_epoch_id=epoch.id,
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
                authority_line_run_epoch_id=epoch.id,
                authority_bin_execution_id=bin_execution.id,
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
            incident = WorklineSafetyIncident(workline_id=line.id)
            bin_placement = BinPlacement(
                bin_code=bin_execution.bin_id,
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
            db.add_all([command, transport, confirmation, incident, bin_placement, rack_placement])
            await db.flush()

            repository = WorkLineRepository()
            summary = await repository.get_unfinished_workload_summary(db, line.id)

            assert summary["by_type"] == {
                "line_run_epochs": True,
                "material_executions": True,
                "bin_executions": True,
                "device_commands": True,
                "transport_tasks": True,
                "inbound_evidences": True,
                "wms_confirmations": True,
            }
            assert summary["count"] == 7
            assert summary["sample"] == {
                "type": "line_run_epoch",
                "id": str(epoch.id),
                "status": "ACTIVE",
                "identity": epoch.epoch_code,
            }
            active_objects = await repository.list_target_active_object_facts(db, workline_id=line.id)
            assert {(row["object_type"], row["object_key"]) for row in active_objects} == {
                ("LINE_RUN_EPOCH", epoch.epoch_code),
                ("MATERIAL_EXECUTION", material.material_trace_id),
                ("BIN_EXECUTION", bin_execution.bin_id),
                ("DEVICE_COMMAND", command.command_code),
                ("TRANSPORT_TASK", transport.transport_task_id),
                ("WMS_CONFIRMATION", confirmation.operation_id),
                ("SAFETY_INCIDENT", str(incident.id)),
                ("BIN_RESOURCE", bin_placement.bin_code),
                ("RACK_RESOURCE", rack_placement.rack_code),
            }
            assert {row["owner_kind"] for row in active_objects} == {
                "LINE_RUN_EPOCH",
                "MATERIAL_EXECUTION",
                "BIN_EXECUTION",
                "DEVICE_COMMAND",
                "TRANSPORT_TASK",
                "WMS_CONFIRMATION",
                "SAFETY_INCIDENT",
                "BIN_PLACEMENT",
                "RACK_PLACEMENT",
            }

            epoch.status = LineRunEpochStatus.CLOSED
            epoch.closed_at = now
            material.status = MaterialExecutionStatus.CLOSED
            material.closed_at = now
            bin_execution.status = BinExecutionStatus.CLOSED
            bin_execution.closed_at = now
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

            async def assert_only(owner: str) -> None:
                current = await repository.get_unfinished_workload_summary(db, line.id)
                assert current["count"] == 1
                assert current["by_type"][owner] is True
                assert not any(value for name, value in current["by_type"].items() if name != owner)

            epoch.status = LineRunEpochStatus.ACTIVE
            epoch.closed_at = None
            await db.flush()
            await assert_only("line_run_epochs")
            epoch.status = LineRunEpochStatus.CLOSED
            epoch.closed_at = now

            for state in (value for value in MaterialExecutionStatus if value is not MaterialExecutionStatus.CLOSED):
                material.status = state
                material.closed_at = None
                await db.flush()
                await assert_only("material_executions")
            material.status = MaterialExecutionStatus.CLOSED
            material.closed_at = now

            bin_execution.status = BinExecutionStatus.ACTIVE
            bin_execution.closed_at = None
            await db.flush()
            await assert_only("bin_executions")
            bin_execution.status = BinExecutionStatus.CLOSED
            bin_execution.closed_at = now

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
            epoch = LineRunEpoch(
                epoch_code=f"DIAGNOSTIC-EPOCH-{identity}",
                workline_id=line.id,
                plugin_key="snapshot_test",
                plugin_version="1.0",
                flow_mode="GENERIC",
                topology_digest="a" * 64,
                configuration_digest="b" * 64,
                configuration_snapshot_json={},
                status=LineRunEpochStatus.CLOSED,
                started_at=now,
                closed_at=now,
            )
            db.add(epoch)
            await db.flush()
            db.add(
                InboundEvidence(
                    kind=InboundEvidenceKind.DEVICE_RESULT,
                    source_identity=f"DIAGNOSTIC-EVIDENCE-{identity}",
                    payload_digest="c" * 64,
                    normalized_payload={"data": {}},
                    received_at=now,
                    line_run_epoch_id=epoch.id,
                    device_code="SNAPSHOT-DEVICE",
                    command_code=f"DIAGNOSTIC-COMMAND-{identity}",
                    apply_status=InboundEvidenceApplyStatus.APPLIED,
                )
            )
            await db.flush()

            summary = await WorkLineRepository().get_unfinished_workload_summary(db, line.id)

            assert summary["by_type"]["inbound_evidences"] is False
            assert summary["count"] == 0
            assert summary["sample"] is None
        finally:
            await transaction.rollback()
