"""Decision processing 的 PostgreSQL 唯一性与事务边界。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from deployment.plugin_composition import build_deployment_runtime
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.app.device.models import CommandStatus, Device, DeviceCommand
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services import DeviceCommandService
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    TransportDecisionBinding,
    WmsConfirmation,
)
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.execution.repositories import (
    inbound_evidence_repository,
    material_execution_repository,
    transport_decision_binding_repository,
)
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.execution.services import (
    FactProcessor,
    InboundEvidenceService,
)
from src.app.transport.contracts import (
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)
from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
    WorkLine,
)
from src.app.workline.models.workline import LineType
from wes_plugin_sdk import (
    TransportResultReadyFact,
    Wait,
    handler,
)

from rough_sorter.application.transport import RoughSorterTransportOutcomePublisher
from rough_sorter.application.wms_facts import rack_release_snapshot

pytest_plugins = ("tests.integration.conftest",)


async def _claim_epoch(db: Any, identity: str, now: datetime) -> tuple[WorkLine, LineRunEpoch]:
    suffix = identity.rsplit("-", maxsplit=1)[-1][:12]
    line = WorkLine(
        line_code=f"CL-{suffix}",
        line_name="Decision claim",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"CE-{suffix}",
        workline_id=line.id,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
        started_at=now,
    )
    db.add(epoch)
    await db.flush()
    return line, epoch


@pytest.mark.asyncio
async def test_concrete_rough_sorter_composition_correlates_first_scan_in_the_claim_transaction(
    integration_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_session_factory = async_sessionmaker(
        integration_session_factory.kw["bind"],
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 9)
    roles = (
        ("MEASUREMENT_DEVICE", "rough_sorter.measurement_device"),
        ("TRANSFER_DEVICE", "rough_sorter.transfer_device"),
        ("PLACEMENT_DEVICE", "rough_sorter.placement_device"),
    )
    position_values = (
        ("MEASUREMENT_POSITION", "MEASUREMENT-1"),
        ("PIPELINE_INLET", "INLET-1"),
        ("PIPELINE_OUTLET", "OUTLET-1"),
        ("NG_POSITION", "NG-1"),
    )
    async with production_session_factory.begin() as db:
        line = WorkLine(
            line_code=f"ROUGH-SCAN-{identity[:12]}",
            line_name="Rough sorter scan owner",
            line_type=LineType.AUTO,
        )
        db.add(line)
        await db.flush()
        devices = [
            Device(
                device_code=f"ROUGH-{role}-{identity[:12]}",
                device_name=role,
                work_line_id=line.id,
                device_role=role,
            )
            for role, _contract in roles
        ]
        db.add_all(devices)
        await db.flush()
        epoch = LineRunEpoch(
            epoch_code=f"ROUGH-EPOCH-{identity[:12]}",
            workline_id=line.id,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
            topology_digest="0" * 64,
            configuration_digest=configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND", {}),
            configuration_snapshot_json={},
            status=LineRunEpochStatus.ACTIVE,
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        device_bindings = [
            LineRunEpochDeviceBinding(
                line_run_epoch_id=epoch.id,
                device_id=device.id,
                device_code=device.device_code,
                device_role=role,
                endpoint_base_url="http://ecs-decision:8080",
                contract_key=contract,
                contract_version="1.0",
                status_max_age_ms=1_000,
                command_timeout_ms=5_000,
            )
            for device, (role, contract) in zip(devices, roles, strict=True)
        ]
        position_bindings = [
            LineRunEpochPositionBinding(
                line_run_epoch_id=epoch.id,
                position_role=role,
                location_id=location_id,
                location_type=role,
            )
            for role, location_id in position_values
        ]
        db.add_all([*device_bindings, *position_bindings])
        await db.flush()
        epoch.topology_digest = topology_digest(device_bindings, position_bindings)
        evidence = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"ROUGH-SCAN-{identity}",
            payload_digest="a" * 64,
            normalized_payload={
                "event_type": "SCAN_COMPLETED",
                "timestamp": 1_787_040_000_000,
                "data": {
                    "material_trace_id": f"TRACE-{identity}",
                    "LotCode": "LOT",
                    "DateCode": "DATE",
                    "Qty": "1",
                    "ProductNo": "PRODUCT",
                    "MfrPN": "MFR",
                    "PONumber": "PO",
                    "diameter_mm": "12.5",
                    "thickness_mm": "1.2",
                    "shape_result": "PASS",
                    "position": {
                        "location_id": "MEASUREMENT-1",
                        "location_type": "MEASUREMENT_POSITION",
                        "material_trace_id": f"TRACE-{identity}",
                    },
                },
            },
            received_at=now,
            line_run_epoch_id=epoch.id,
            device_code=devices[0].device_code,
            contract_key="rough_sorter.measurement_device",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add(evidence)
        await db.flush()
        evidence_id = evidence.id
        line_id = line.id
        epoch_id = epoch.id
        device_ids = tuple(device.id for device in devices)

    from src.core.task_queue_gateway import task_queue_gateway

    monkeypatch.setattr(task_queue_gateway, "enqueue_wms_confirmations", lambda: None)
    runtime = build_deployment_runtime(
        session_factory=production_session_factory,
        transport_runtime=SimpleNamespace(
            service=object(),
            repository=object(),
            client=object(),
            position_projection_service=object(),
        ),  # type: ignore[arg-type]
        device_command_service=DeviceCommandService(session_factory=production_session_factory, clock=lambda: now),
    )

    assert await runtime.execution.fact_processor.process_batch() == 1

    async with production_session_factory.begin() as db:
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        assert persisted_evidence is not None and persisted_evidence.material_execution_id is not None
        execution = await db.get(MaterialExecution, persisted_evidence.material_execution_id)
        assert execution is not None
        assert execution.material_trace_id == f"TRACE-{identity}"
        assert persisted_evidence.published_at is not None
        assert persisted_evidence.decision_digest is not None
        confirmations = list(
            (
                await db.execute(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == execution.id))
            ).scalars()
        )
        assert [item.operation for item in confirmations] == ["inbound.material.admission_decide@v1"]
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id == execution.id))
        persisted_evidence.material_execution_id = None
        await db.flush()
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution.id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence_id))
        await db.execute(
            delete(LineRunEpochPositionBinding).where(LineRunEpochPositionBinding.line_run_epoch_id == epoch_id)
        )
        await db.execute(
            delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.line_run_epoch_id == epoch_id)
        )
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_rack_release_snapshot_includes_cross_execution_placement_without_confirmation(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 9)
    command_code = "019d0000-0000-7000-8000-" + identity[:12]
    async with integration_session_factory.begin() as db:
        line = WorkLine(
            line_code=f"RELEASE-{identity[:12]}",
            line_name="Rack release owner",
            line_type=LineType.AUTO,
        )
        db.add(line)
        await db.flush()
        device = Device(
            device_code=f"RELEASE-DEVICE-{identity[:12]}",
            device_name="Placement",
            work_line_id=line.id,
            device_role="PLACEMENT_DEVICE",
        )
        db.add(device)
        await db.flush()
        epoch = LineRunEpoch(
            epoch_code=f"RELEASE-EPOCH-{identity[:12]}",
            workline_id=line.id,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json={},
            status=LineRunEpochStatus.ACTIVE,
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        binding = LineRunEpochDeviceBinding(
            line_run_epoch_id=epoch.id,
            device_id=device.id,
            device_code=device.device_code,
            device_role="PLACEMENT_DEVICE",
            endpoint_base_url="http://ecs-decision:8080",
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            status_max_age_ms=1_000,
            command_timeout_ms=5_000,
        )
        db.add(binding)
        seeds = [
            InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"RELEASE-SEED-{ordinal}-{identity}",
                payload_digest=str(ordinal) * 64,
                normalized_payload={"data": {}},
                received_at=now,
                line_run_epoch_id=epoch.id,
                device_code=device.device_code,
                contract_version="1.0",
                apply_status=InboundEvidenceApplyStatus.IGNORED,
            )
            for ordinal in (1, 2)
        ]
        db.add_all(seeds)
        await db.flush()
        executions = [
            MaterialExecution(
                execution_code=f"RELEASE-EXEC-{ordinal}-{identity}",
                material_trace_id=f"RELEASE-TRACE-{ordinal}-{identity}",
                workline_id=line.id,
                line_run_epoch_id=epoch.id,
                admission_received_at=now,
                admission_evidence_id=seed.id,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=seed.id,
                status_changed_at=now,
            )
            for ordinal, seed in enumerate(seeds, start=1)
        ]
        db.add_all(executions)
        await db.flush()
        command = DeviceCommand(
            command_code=command_code,
            device_code=device.device_code,
            device_binding_id=binding.id,
            line_run_epoch_id=epoch.id,
            execution_ref_type="PLUGIN_DECISION",
            execution_ref_id=f"evidence:{seeds[1].id}:execution:{executions[1].id}:CREATE_DEVICE_COMMAND:0",
            material_execution_id=executions[1].id,
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            task_type="PICK_AND_PUT",
            params={
                "material_trace_id": executions[1].material_trace_id,
                "source": {
                    "location_id": "OUTLET-1",
                    "location_type": "PIPELINE_OUTLET",
                    "material_trace_id": executions[1].material_trace_id,
                },
                "target": {
                    "location_id": "CELL-1",
                    "location_type": "RACK_CELL",
                    "material_trace_id": executions[1].material_trace_id,
                    "rack_id": "RACK-1",
                    "rack_slot_code": "SLOT-1",
                    "bin_id": "BIN-1",
                    "bin_cell_id": "CELL-1",
                },
            },
            deadline_at=now + timedelta(minutes=1),
            payload_digest="c" * 64,
            status=CommandStatus.ACKNOWLEDGED,
        )
        db.add(command)
        await db.flush()
        execution_ids = tuple(execution.id for execution in executions)
        seed_ids = tuple(seed.id for seed in seeds)
        line_id = line.id
        epoch_id = epoch.id
        binding_id = binding.id
        device_id = device.id

        snapshot = await rack_release_snapshot(
            db=db,
            execution=executions[0],
            current_rack_id="RACK-1",
            commands=device_command_repository,
            confirmations=wms_confirmation_repository,
        )
        assert [item.command_code for item in snapshot.placements] == [command_code]
        assert snapshot.placements[0].command_status.value == "ACKNOWLEDGED"
        assert snapshot.placements[0].confirmation_status.value == "ABSENT"
        assert snapshot.placements[0].confirmation_operation_id is None

        await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code == command_code))
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(seed_ids)))
        await db.execute(delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.id == binding_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(Device).where(Device.id == device_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("first_owner", ["replacement", "target"])
async def test_postgresql_rack_fence_serializes_replacement_and_late_target_across_executions(
    integration_session_factory,
    first_owner: str,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 10)
    first_has_lock = asyncio.Event()
    contender_started = asyncio.Event()
    release_first_owner = asyncio.Event()
    async with integration_session_factory.begin() as db:
        line = WorkLine(
            line_code=f"RACK-FENCE-{identity[:12]}",
            line_name="Rack fence concurrency owner",
            line_type=LineType.AUTO,
        )
        db.add(line)
        await db.flush()
        device = Device(
            device_code=f"RACK-FENCE-DEVICE-{identity[:12]}",
            device_name="Placement",
            work_line_id=line.id,
            device_role="PLACEMENT_DEVICE",
        )
        db.add(device)
        await db.flush()
        epoch = LineRunEpoch(
            epoch_code=f"RACK-FENCE-EPOCH-{identity[:12]}",
            workline_id=line.id,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json={},
            status=LineRunEpochStatus.ACTIVE,
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        device_binding = LineRunEpochDeviceBinding(
            line_run_epoch_id=epoch.id,
            device_id=device.id,
            device_code=device.device_code,
            device_role="PLACEMENT_DEVICE",
            endpoint_base_url="http://ecs-decision:8080",
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            status_max_age_ms=1_000,
            command_timeout_ms=5_000,
        )
        db.add(device_binding)
        seeds = [
            InboundEvidence(
                kind=InboundEvidenceKind.WMS_RESULT,
                source_identity=f"RACK-FENCE-SEED-{ordinal}-{identity}",
                payload_digest=str(ordinal) * 64,
                normalized_payload={"data": {}},
                received_at=now,
                line_run_epoch_id=epoch.id,
                operation="inbound.material.target_decide@v1",
                operation_id=f"RACK-FENCE-OP-{ordinal}-{identity}",
                contract_version="1.0",
                apply_status=InboundEvidenceApplyStatus.IGNORED,
            )
            for ordinal in (1, 2)
        ]
        db.add_all(seeds)
        await db.flush()
        executions = [
            MaterialExecution(
                execution_code=f"RACK-FENCE-EXEC-{ordinal}-{identity}",
                material_trace_id=f"RACK-FENCE-TRACE-{ordinal}-{identity}",
                workline_id=line.id,
                line_run_epoch_id=epoch.id,
                admission_received_at=now,
                admission_evidence_id=seed.id,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=seed.id,
                status_changed_at=now,
            )
            for ordinal, seed in enumerate(seeds, start=1)
        ]
        db.add_all(executions)
        await db.flush()
        for seed, execution in zip(seeds, executions, strict=True):
            seed.material_execution_id = execution.id
        await db.flush()
        line_id = line.id
        device_id = device.id
        device_code = device.device_code
        epoch_id = epoch.id
        binding_id = device_binding.id
        seed_ids = tuple(seed.id for seed in seeds)
        execution_ids = tuple(execution.id for execution in executions)
        target_trace_id = executions[0].material_trace_id

    async def create_target_command(rack_id: str, suffix: str, *, hold_lock: bool = False) -> bool:
        async with integration_session_factory.begin() as db:
            if not hold_lock:
                contender_started.set()
            await transport_decision_binding_repository.lock_resource_fence(
                db,
                line_run_epoch_id=epoch_id,
                resource_fence_id=rack_id,
            )
            fence = await transport_decision_binding_repository.get_by_resource_step_for_update(
                db,
                line_run_epoch_id=epoch_id,
                resource_fence_id=rack_id,
                step="OLD_OUT",
            )
            if fence is not None:
                return False
            command_code = f"019d{identity[:4]}-{identity[4:8]}-7{identity[8:11]}-8{identity[11:14]}-{suffix}"
            db.add(
                DeviceCommand(
                    command_code=command_code,
                    device_code=device_code,
                    device_binding_id=binding_id,
                    line_run_epoch_id=epoch_id,
                    execution_ref_type="PLUGIN_DECISION",
                    execution_ref_id=f"evidence:{seed_ids[0]}:execution:{execution_ids[0]}:CREATE_DEVICE_COMMAND:{suffix}",
                    material_execution_id=execution_ids[0],
                    contract_key="rough_sorter.placement_device",
                    contract_version="1.0",
                    task_type="PICK_AND_PUT",
                    params={
                        "material_trace_id": target_trace_id,
                        "source": {
                            "location_id": "OUTLET-1",
                            "location_type": "PIPELINE_OUTLET",
                            "material_trace_id": target_trace_id,
                        },
                        "target": {
                            "location_id": f"CELL-{suffix}",
                            "location_type": "RACK_CELL",
                            "material_trace_id": target_trace_id,
                            "rack_id": rack_id,
                            "rack_slot_code": f"SLOT-{suffix}",
                            "bin_id": f"BIN-{suffix}",
                            "bin_cell_id": f"CELL-{suffix}",
                        },
                    },
                    deadline_at=now + timedelta(minutes=1),
                    payload_digest=suffix[0] * 64,
                    status=CommandStatus.PENDING,
                )
            )
            await db.flush()
            if hold_lock:
                first_has_lock.set()
                await release_first_owner.wait()
            return True

    async def create_old_out_fence(*, hold_lock: bool = False) -> bool:
        async with integration_session_factory.begin() as db:
            if not hold_lock:
                contender_started.set()
            await transport_decision_binding_repository.lock_resource_fence(
                db,
                line_run_epoch_id=epoch_id,
                resource_fence_id="RACK-1",
            )
            commands = await device_command_repository.list_for_epoch_for_update(
                db,
                line_run_epoch_id=epoch_id,
            )
            active_statuses = {
                CommandStatus.PENDING,
                CommandStatus.DISPATCHING,
                CommandStatus.ACKNOWLEDGED,
                CommandStatus.RECONCILING,
            }
            if any(
                command.task_type == "PICK_AND_PUT"
                and CommandStatus(command.status) in active_statuses
                and isinstance(target := command.params.get("target"), dict)
                and target.get("location_type") == "RACK_CELL"
                and target.get("rack_id") == "RACK-1"
                for command in commands
            ):
                return False
            db.add(
                TransportDecisionBinding(
                    correlation_id=f"REPLACE-{identity}",
                    step="OLD_OUT",
                    line_run_epoch_id=epoch_id,
                    resource_fence_id="RACK-1",
                    client_request_id=f"019d0000-0000-7000-8001-{identity[:12]}",
                    source_evidence_id=seed_ids[1],
                )
            )
            await db.flush()
            if hold_lock:
                first_has_lock.set()
                await release_first_owner.wait()
            return True

    if first_owner == "replacement":
        first = asyncio.create_task(create_old_out_fence(hold_lock=True))
        await first_has_lock.wait()
        contender = asyncio.create_task(create_target_command("RACK-1", "000000000101"))
    else:
        first = asyncio.create_task(create_target_command("RACK-1", "000000000102", hold_lock=True))
        await first_has_lock.wait()
        contender = asyncio.create_task(create_old_out_fence())
    await contender_started.wait()
    release_first_owner.set()
    first_result, contender_result = await asyncio.gather(first, contender)
    if first_owner == "replacement":
        assert first_result is True and contender_result is False
        assert await create_target_command("RACK-1", "000000000103") is False
    else:
        assert first_result is True and contender_result is False
        first_command_code = f"019d{identity[:4]}-{identity[4:8]}-7{identity[8:11]}-8{identity[11:14]}-000000000102"
        async with integration_session_factory.begin() as db:
            await db.execute(
                update(DeviceCommand)
                .where(DeviceCommand.command_code == first_command_code)
                .values(status=CommandStatus.SUCCEEDED)
            )

    assert await create_target_command("RACK-2", "000000000104") is True

    async with integration_session_factory.begin() as db:
        commands = list(
            (
                await db.execute(
                    select(DeviceCommand).where(DeviceCommand.line_run_epoch_id == epoch_id).order_by(DeviceCommand.id)
                )
            ).scalars()
        )
        r1_commands = [command for command in commands if command.params["target"]["rack_id"] == "RACK-1"]
        assert len(r1_commands) == (0 if first_owner == "replacement" else 1)
        assert [command.params["target"]["rack_id"] for command in commands][-1] == "RACK-2"
        await db.execute(delete(DeviceCommand).where(DeviceCommand.line_run_epoch_id == epoch_id))
        await db.execute(delete(TransportDecisionBinding).where(TransportDecisionBinding.line_run_epoch_id == epoch_id))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(seed_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(seed_ids)))
        await db.execute(delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.id == binding_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(Device).where(Device.id == device_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_transport_publisher_revalidates_after_accept_first_concurrent_drift(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 12)
    client_request_id = "019d0000-0000-7000-8000-000000000142"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, f"PUBLISHER-LOCK-{identity}", now)
        source = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"PUBLISHER-LOCK-SOURCE-{identity}",
            payload_digest="a" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            operation="inbound.source_rack.replacement_plan_decide@v1",
            operation_id=f"PUBLISHER-LOCK-OP-{identity}",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(source)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"PUBLISHER-LOCK-EXEC-{identity}",
            material_trace_id=f"PUBLISHER-LOCK-TRACE-{identity}",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            admission_received_at=now,
            admission_evidence_id=source.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=source.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        source.material_execution_id = execution.id
        binding = TransportDecisionBinding(
            correlation_id=f"PUBLISHER-LOCK-REPLACE-{identity}",
            step="NEW_IN",
            line_run_epoch_id=epoch.id,
            resource_fence_id="RACK-1",
            client_request_id=client_request_id,
            source_evidence_id=source.id,
        )
        db.add(binding)
        await db.flush()
        line_id = line.id
        epoch_id = epoch.id
        source_id = source.id
        execution_id = execution.id

    owner_locked_execution = asyncio.Event()
    publisher_entered_accept = asyncio.Event()
    real_evidence_service = InboundEvidenceService()

    class SignallingEvidenceService:
        async def accept(self, db: object, **values: object) -> object:
            publisher_entered_accept.set()
            return await real_evidence_service.accept(db, **cast("Any", values))

    async def drift_binding_in_execution_first_order() -> None:
        async with integration_session_factory.begin() as db:
            locked_execution = await material_execution_repository.get_by_id_for_update(db, execution_id)
            assert locked_execution is not None
            owner_locked_execution.set()
            await publisher_entered_accept.wait()
            locked_binding = await transport_decision_binding_repository.get_by_client_request_id_for_update(
                db, client_request_id
            )
            locked_source = await inbound_evidence_repository.get_by_id_for_update(db, source_id)
            assert locked_binding is not None and locked_source is not None
            locked_binding.resource_fence_id = "RACK-DRIFT"

    owner = asyncio.create_task(drift_binding_in_execution_first_order())
    await owner_locked_execution.wait()
    publisher = RoughSorterTransportOutcomePublisher(
        session_factory=integration_session_factory,
        evidence_service=SignallingEvidenceService(),  # type: ignore[arg-type]
    )
    outcome = TransportOutcome(
        transport_task_id=f"TRANSPORT-{identity}",
        client_request_id=client_request_id,
        outcome_version=1,
        caller=TransportCaller(workline_id=str(line_id)),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(
            TransportMemberOutcome(
                object_id="RACK-2",
                final_position=RackPosition("OUTLET"),
                arrival_face="270",
            ),
        ),
    )
    publishing = asyncio.create_task(publisher.publish(outcome))
    await asyncio.wait_for(owner, timeout=5)
    with pytest.raises(ValueError, match="binding correlation drift"):
        await asyncio.wait_for(publishing, timeout=5)

    async with integration_session_factory.begin() as db:
        assert (
            await db.scalar(
                select(InboundEvidence.id).where(
                    InboundEvidence.transport_task_id == outcome.transport_task_id,
                    InboundEvidence.kind == InboundEvidenceKind.TRANSPORT_RESULT,
                )
            )
            is None
        )
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == client_request_id)
        )
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id == source_id).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == source_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_duplicate_transport_publisher_and_fact_processor_share_outcome_first_lock_order(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 13)
    client_request_id = "019d0000-0000-7000-8000-" + identity[:12]
    transport_task_id = f"TRANSPORT-LOCK-{identity}"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, f"PUBLISHER-FACT-{identity}", now)
        source = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"PUBLISHER-FACT-SOURCE-{identity}",
            payload_digest="a" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            operation="inbound.source_rack.replacement_plan_decide@v1",
            operation_id=f"PUBLISHER-FACT-OP-{identity}",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(source)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"PUBLISHER-FACT-EXEC-{identity}",
            material_trace_id=f"PUBLISHER-FACT-TRACE-{identity}",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            admission_received_at=now,
            admission_evidence_id=source.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=source.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        source.material_execution_id = execution.id
        binding = TransportDecisionBinding(
            correlation_id=f"PUBLISHER-FACT-REPLACE-{identity}",
            step="NEW_IN",
            line_run_epoch_id=epoch.id,
            resource_fence_id="RACK-1",
            client_request_id=client_request_id,
            source_evidence_id=source.id,
        )
        db.add(binding)
        outcome = TransportOutcome(
            transport_task_id=transport_task_id,
            client_request_id=client_request_id,
            outcome_version=1,
            caller=TransportCaller(workline_id=str(line.id)),
            status=TransportOutcomeStatus.SUCCEEDED,
            reason_code=None,
            members=(
                TransportMemberOutcome(
                    object_id="RACK-2",
                    final_position=RackPosition("OUTLET"),
                    arrival_face="270",
                ),
            ),
        )
        accepted = await InboundEvidenceService().accept(
            db,
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{transport_task_id}:outcome:1",
            normalized_payload={
                "transport_task_id": transport_task_id,
                "client_request_id": client_request_id,
                "outcome_version": 1,
                "caller": {"workline_id": str(line.id)},
                "status": "SUCCEEDED",
                "reason_code": None,
                "members": [
                    {
                        "object_id": "RACK-2",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "OUTLET"},
                        "position_unknown": False,
                        "failure_code": None,
                        "arrival_face": "270",
                    }
                ],
            },
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=transport_task_id,
            contract_key="rough_sorter.transport_outcome",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        outcome_evidence_id = accepted.evidence.id
        line_id = line.id
        epoch_id = epoch.id
        source_id = source.id
        execution_id = execution.id

    processor_holds_outcome = asyncio.Event()
    release_processor = asyncio.Event()
    publisher_waiting_outcome = asyncio.Event()

    class GatedExecutionRepository:
        def __init__(self) -> None:
            self.calls = 0

        async def get_by_id_for_update(self, db: object, execution_id: int) -> MaterialExecution | None:
            self.calls += 1
            if self.calls == 1:
                processor_holds_outcome.set()
                await release_processor.wait()
            return await material_execution_repository.get_by_id_for_update(db, execution_id)  # type: ignore[arg-type]

    class PublisherEvidenceRepository:
        async def lock_source_identity(self, db: object, source_identity: str) -> None:
            await inbound_evidence_repository.lock_source_identity(db, source_identity)  # type: ignore[arg-type]

        async def get_by_source_identity_for_update(self, db: object, source_identity: str) -> InboundEvidence | None:
            publisher_waiting_outcome.set()
            return await inbound_evidence_repository.get_by_source_identity_for_update(  # type: ignore[arg-type]
                db, source_identity
            )

        async def add(self, db: object, evidence: InboundEvidence) -> InboundEvidence:
            return await inbound_evidence_repository.add(db, evidence)  # type: ignore[arg-type]

        async def add_conflict(self, db: object, conflict: object) -> object:
            return await inbound_evidence_repository.add_conflict(db, conflict)  # type: ignore[arg-type]

    class IdentityFactFactory:
        async def build(self, db: object, fact: TransportResultReadyFact) -> TransportResultReadyFact:
            del db
            return fact

    @handler(fact_type=TransportResultReadyFact, name="transport_lock_order", supported_versions=("1.0",))
    def handle_transport(fact: TransportResultReadyFact) -> tuple[Wait, ...]:
        return (Wait(fact.material_execution_id, fact.fact_id, "TRANSPORT_RECORDED"),)

    class RecordingApplier:
        def __init__(self) -> None:
            self.calls = 0

        async def apply(self, *args: object) -> str:
            del args
            self.calls += 1
            return "applied"

    class Queue:
        def enqueue_execution_facts(self) -> None:
            return None

    applier = RecordingApplier()
    processor = FactProcessor(
        session_factory=integration_session_factory,
        plugin_binding=StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="rough_sorter",
                    plugin_version="1.0.0",
                    handlers=(handle_transport,),
                    fact_factory=IdentityFactFactory(),  # type: ignore[arg-type]
                ),
            )
        ),
        decision_applier=applier,  # type: ignore[arg-type]
        execution_repository=GatedExecutionRepository(),
        clock=lambda: now + timedelta(seconds=1),
        token_factory=lambda: f"claim-{identity}",
    )
    publisher = RoughSorterTransportOutcomePublisher(
        session_factory=integration_session_factory,
        evidence_service=InboundEvidenceService(repository=PublisherEvidenceRepository()),  # type: ignore[arg-type]
        queue_gateway=Queue(),  # type: ignore[arg-type]
    )

    processing = asyncio.create_task(processor.process_batch(limit=1))
    await asyncio.wait_for(processor_holds_outcome.wait(), timeout=5)
    publishing = asyncio.create_task(publisher.publish(outcome))
    await asyncio.wait_for(publisher_waiting_outcome.wait(), timeout=5)
    release_processor.set()
    assert await asyncio.wait_for(processing, timeout=5) == 1
    await asyncio.wait_for(publishing, timeout=5)
    assert await processor.process_batch(limit=1) == 0
    assert applier.calls == 1

    async with integration_session_factory.begin() as db:
        evidences = list(
            (
                await db.execute(
                    select(InboundEvidence).where(
                        InboundEvidence.source_identity == f"transport:{transport_task_id}:outcome:1"
                    )
                )
            ).scalars()
        )
        assert [evidence.id for evidence in evidences] == [outcome_evidence_id]
        assert evidences[0].published_at is not None
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == client_request_id)
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == outcome_evidence_id))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id == source_id).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == source_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))
