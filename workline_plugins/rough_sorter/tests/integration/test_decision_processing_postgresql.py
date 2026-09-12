"""Decision processing 的 PostgreSQL 唯一性与事务边界。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from deployment.plugin_composition import build_deployment_runtime
from sqlalchemy import delete, select, text, update
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
    DecisionApplier,
    FactProcessor,
    InboundEvidenceService,
    MaterialExecutionService,
)
from src.app.resource.models import RackKind, RackPlacement, RackPlacementStatus, ResourceSourceSystem
from src.app.resource.repositories import rack_placement_repository
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.contracts import (
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)
from src.app.wms_adapter.inbound_material.typed import decode_outcome

# WmsConfirmation 的可空外键仍需在独立插件测试进程中注册目标表。
from src.app.wms_integration.outbound_picking.models import PickingTask  # noqa: F401
from src.app.workline.activation import WorkLineDeviceBinding, WorkLinePositionBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.rack_position_role import WorklineRackPositionRole
from wes_plugin_sdk import (
    TransportResultReadyFact,
    Wait,
    WmsResultReadyFact,
    handler,
)

from rough_sorter.application.factory import RoughSorterPluginFactFactory
from rough_sorter.application.transport import RoughSorterTransportOutcomePublisher
from rough_sorter.application.wms_facts import rack_release_snapshot
from rough_sorter.handlers import TargetDecidedHandler

pytest_plugins = ("tests.integration.conftest",)


def _publish(line, devices, positions=()):
    from dataclasses import asdict

    line.config = {"device_bindings": {item.device_role: item.device_code for item in devices}}
    line.device_contracts = {
        item.device_code: {
            key: value
            for key, value in asdict(item).items()
            if key not in {"device_code", "device_role", "workline_id"}
        }
        for item in devices
    }
    line.position_bindings = {
        item.position_role: {"location_id": item.location_id, "location_type": item.location_type} for item in positions
    }


async def _claim_workline(db: Any, identity: str, now: datetime) -> WorkLine:
    suffix = identity.rsplit("-", maxsplit=1)[-1][:12]
    line = WorkLine(
        line_code=f"CL-{suffix}",
        line_name="Decision claim",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    line.plugin_key = "rough_sorter"
    line.plugin_version = "1.0.0"
    line.flow_mode = "ROUGH_SORT_INBOUND"
    line.is_active = True
    await db.flush()
    return line


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
        ("MEASUREMENT_POSITION", "MEASUREMENT_POSITION"),
        ("PIPELINE_INLET", "PIPELINE_INLET"),
        ("PIPELINE_OUTLET", "PIPELINE_OUTLET"),
        ("NG_POSITION", "NG_POSITION"),
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
            )
            for role, _contract in roles
        ]
        db.add_all(devices)
        await db.flush()
        line.plugin_key = "rough_sorter"
        line.plugin_version = "1.0.0"
        line.flow_mode = "ROUGH_SORT_INBOUND"
        line.is_active = True
        await db.flush()
        device_bindings = [
            WorkLineDeviceBinding(
                workline_id=line.id,
                device_id=device.id,
                device_code=device.device_code,
                device_role=role,
                endpoint_base_url="http://ecs-decision:8080",
                contract_key=contract,
                contract_version="1.0",
                status_max_age_ms=10_000,
                command_timeout_ms=30_000,
            )
            for device, (role, contract) in zip(devices, roles, strict=True)
        ]
        position_bindings = [
            WorkLinePositionBinding(
                position_role=role,
                location_id=location_id,
                location_type=role,
            )
            for role, location_id in position_values
        ]
        _publish(line, device_bindings, position_bindings)
        await db.flush()
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
                        "location_id": "MEASUREMENT_POSITION",
                        "location_type": "MEASUREMENT_POSITION",
                        "material_trace_id": f"TRACE-{identity}",
                    },
                },
            },
            received_at=now,
            workline_id=line.id,
            device_code=devices[0].device_code,
            contract_key="rough_sorter.measurement_device",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add(evidence)
        await db.flush()
        evidence_id = evidence.id
        line_id = line.id
        device_ids = tuple(device.id for device in devices)

    from src.core.task_queue_gateway import task_queue_gateway

    monkeypatch.setattr(task_queue_gateway, "enqueue_wms_confirmations", lambda: None)
    runtime = build_deployment_runtime(
        enabled_plugin_keys=("rough_sorter",),
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
        line.plugin_key = "rough_sorter"
        line.plugin_version = "1.0.0"
        line.flow_mode = "ROUGH_SORT_INBOUND"
        line.is_active = True
        await db.flush()
        binding = WorkLineDeviceBinding(
            workline_id=line.id,
            device_id=device.id,
            device_code=device.device_code,
            device_role="PLACEMENT_DEVICE",
            endpoint_base_url="http://ecs-decision:8080",
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            status_max_age_ms=10_000,
            command_timeout_ms=30_000,
        )
        _publish(line, (binding,))
        seeds = [
            InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=f"RELEASE-SEED-{ordinal}-{identity}",
                payload_digest=str(ordinal) * 64,
                normalized_payload={"data": {}},
                received_at=now,
                workline_id=line.id,
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
            endpoint_base_url=binding.endpoint_base_url,
            status_max_age_ms=binding.status_max_age_ms,
            command_timeout_ms=binding.command_timeout_ms,
            workline_id=line.id,
            execution_ref_type="PLUGIN_DECISION",
            execution_ref_id=f"evidence:{seeds[1].id}:execution:{executions[1].id}:CREATE_DEVICE_COMMAND:0",
            material_execution_id=executions[1].id,
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            task_type="PICK_AND_PUT",
            params={
                "material_trace_id": executions[1].material_trace_id,
                "source": {
                    "location_id": "PIPELINE_OUTLET",
                    "location_type": "PIPELINE_OUTLET",
                    "material_trace_id": executions[1].material_trace_id,
                },
                "target": {
                    "location_id": "CELL-1",
                    "location_type": "RACK_CELL",
                    "material_trace_id": executions[1].material_trace_id,
                    "rack_id": "RACK-1",
                    "rack_slot_code": "SLOT-1",
                    "bin_code": "BIN-1",
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
        await db.execute(delete(Device).where(Device.id == device_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_target_revalidates_after_concurrent_rack_replacement(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 10)
    operation = "inbound.material.target_decide@v1"
    operation_id = f"019d{identity[:4]}-{identity[4:8]}-7{identity[8:11]}-8{identity[11:14]}-{identity[14:26]}"
    async with integration_session_factory.begin() as db:
        line = WorkLine(
            line_code=f"RACK-FENCE-{identity[:12]}",
            line_name="Rack fence concurrency owner",
            line_type=LineType.AUTO,
        )
        db.add(line)
        await db.flush()
        role_contracts = (
            ("MEASUREMENT_DEVICE", "rough_sorter.measurement_device"),
            ("TRANSFER_DEVICE", "rough_sorter.transfer_device"),
            ("PLACEMENT_DEVICE", "rough_sorter.placement_device"),
        )
        devices = [
            Device(
                device_code=f"RACK-FENCE-{role}-{identity[:8]}",
                device_name=role,
                work_line_id=line.id,
                device_role=role,
            )
            for role, _contract in role_contracts
        ]
        db.add_all(devices)
        await db.flush()
        line.plugin_key = "rough_sorter"
        line.plugin_version = "1.0.0"
        line.flow_mode = "ROUGH_SORT_INBOUND"
        line.is_active = True
        await db.flush()
        bindings = [
            WorkLineDeviceBinding(
                workline_id=line.id,
                device_id=device.id,
                device_code=device.device_code,
                device_role=role,
                endpoint_base_url="http://ecs-decision:8080",
                contract_key=contract,
                contract_version="1.0",
                status_max_age_ms=10_000,
                command_timeout_ms=30_000,
            )
            for device, (role, contract) in zip(devices, role_contracts, strict=True)
        ]
        positions = tuple(
            WorkLinePositionBinding(position_role=role, location_id=role, location_type=role)
            for role in ("MEASUREMENT_POSITION", "PIPELINE_INLET", "PIPELINE_OUTLET", "NG_POSITION")
        )
        _publish(line, bindings, positions)
        outlet_position = WorkLinePosition(
            workline_id=line.id,
            workline_code=line.line_code,
            position_code=f"OUTLET-{identity[:12]}",
            position_name="Pipeline outlet",
            position_type="RACK_POSITION",
            position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
            allowed_rack_kind=RackKind.SINGLE_LAYER,
            capacity=1,
            logic_location_code="PIPELINE_OUTLET",
        )
        db.add(outlet_position)
        admission = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"RACK-FENCE-ADMISSION-{identity}",
            payload_digest="a" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            workline_id=line.id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(admission)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"RACK-FENCE-EXEC-{identity}",
            material_trace_id=f"RACK-FENCE-TRACE-{identity}",
            workline_id=line.id,
            admission_received_at=now,
            admission_evidence_id=admission.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=admission.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        response = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"{operation}:{operation_id}",
            payload_digest="b" * 64,
            normalized_payload={
                "operation_id": operation_id,
                "code": "DECIDED",
                "timestamp": 1_787_040_000_200,
                "data": {
                    "result": "ASSIGNED",
                    "target_assignment_id": f"ASSIGN-{identity}",
                    "target_position": {
                        "type": "ONE_LAYER_BIN_CELL",
                        "rack_id": "RACK-1",
                        "rack_slot_code": "SLOT-1",
                        "bin_code": "BIN-1",
                        "bin_cell_id": "CELL-1",
                    },
                    "placement_sequence": 1,
                    "expected_height_mm": "3.2",
                },
            },
            received_at=now,
            workline_id=line.id,
            material_execution_id=execution.id,
            contract_key=operation,
            contract_version="1.0",
            operation=operation,
            operation_id=operation_id,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add(response)
        await db.flush()
        confirmation = WmsConfirmation(
            operation=operation,
            operation_id=operation_id,
            material_execution_id=execution.id,
            request_digest="c" * 64,
            request_payload={
                "operation": operation,
                "operation_id": operation_id,
                "timestamp": 1_787_040_000_100,
                "data": {
                    "material_execution_id": execution.execution_code,
                    "material_trace_id": execution.material_trace_id,
                    "pkg_id": "PKG-1",
                    "inbound_admission_id": "ADM-1",
                    "source_position": {"type": "HANDOFF_POSITION", "location_code": "PIPELINE_OUTLET"},
                    "current_rack_id": "RACK-1",
                },
            },
            deadline_at=now + timedelta(minutes=1),
            status="COMPLETED",
            response_evidence_id=response.id,
            response_result="ASSIGNED",
            completed_at=now,
        )
        placement = RackPlacement(
            rack_code="RACK-1",
            rack_kind=RackKind.SINGLE_LAYER,
            location_code="PIPELINE_OUTLET",
            workline_id=line.id,
            workline_code=line.line_code,
            position_code=outlet_position.position_code,
            position_role=WorklineRackPositionRole.SMT_SORTER_STATION.value,
            logic_location_code="PIPELINE_OUTLET",
            placement_status=RackPlacementStatus.ARRIVED,
            source_system=ResourceSourceSystem.WES_RUNTIME,
            source_event_id=f"RACK-1-ARRIVED-{identity}",
            started_at=now,
        )
        db.add_all([confirmation, placement])
        await db.flush()
        line_id = line.id
        device_ids = tuple(device.id for device in devices)
        evidence_ids = (admission.id, response.id)
        execution_id = execution.id
        response_id = response.id
        outlet_position_id = outlet_position.id

    class AlwaysReady:
        async def is_ready(self, db: object, binding: object) -> bool:
            del db, binding
            return True

    factory = RoughSorterPluginFactFactory(device_readiness_reader=AlwaysReady())
    applier = DecisionApplier(
        device_command_service=DeviceCommandService(session_factory=integration_session_factory, clock=lambda: now),
        wms_confirmation_service=cast(Any, object()),
        transport_service=cast(Any, object()),
        material_execution_service=MaterialExecutionService(),
        clock=lambda: now,
    )
    base = WmsResultReadyFact(
        f"evidence:{response_id}",
        str(response_id),
        "1.0",
        f"RACK-FENCE-EXEC-{identity}",
        operation_id,
        decode_outcome(operation, response.normalized_payload, material_trace_id=f"RACK-FENCE-TRACE-{identity}"),
    )
    writer_holds_placement = asyncio.Event()
    release_writer = asyncio.Event()
    target_started = asyncio.Event()
    pids: dict[str, int] = {}

    async def replace_rack() -> None:
        async with integration_session_factory.begin() as db:
            pids["writer"] = cast("int", await db.scalar(text("SELECT pg_backend_pid()")))
            active = await rack_placement_repository.list_active_by_workline_position(
                db,
                workline_code=f"RACK-FENCE-{identity[:12]}",
                position_code=f"OUTLET-{identity[:12]}",
                for_update=True,
            )
            assert len(active) == 1 and active[0].rack_code == "RACK-1"
            active[0].placement_status = RackPlacementStatus.DEPARTED
            active[0].ended_at = now + timedelta(seconds=1)
            db.add(
                RackPlacement(
                    rack_code="RACK-2",
                    rack_kind=RackKind.SINGLE_LAYER,
                    location_code="PIPELINE_OUTLET",
                    workline_id=line_id,
                    workline_code=f"RACK-FENCE-{identity[:12]}",
                    position_code=f"OUTLET-{identity[:12]}",
                    position_role=WorklineRackPositionRole.SMT_SORTER_STATION.value,
                    logic_location_code="PIPELINE_OUTLET",
                    placement_status=RackPlacementStatus.ARRIVED,
                    source_system=ResourceSourceSystem.WES_RUNTIME,
                    source_event_id=f"RACK-2-ARRIVED-{identity}",
                    started_at=now + timedelta(seconds=1),
                )
            )
            await db.flush()
            writer_holds_placement.set()
            await release_writer.wait()

    async def apply_target() -> str | None:
        async with integration_session_factory.begin() as db:
            pids["target"] = cast("int", await db.scalar(text("SELECT pg_backend_pid()")))
            target_started.set()
            fact = await factory.build(db, base)
            decisions = TargetDecidedHandler()(cast(Any, fact))
            execution = await db.get(MaterialExecution, execution_id)
            evidence = await db.get(InboundEvidence, response_id)
            assert execution is not None and evidence is not None
            await applier.apply(db, evidence, execution, fact, decisions)
            return None

    writer = asyncio.create_task(replace_rack())
    target: asyncio.Task[str | None] | None = None
    try:
        await asyncio.wait_for(writer_holds_placement.wait(), 5)
        target = asyncio.create_task(apply_target())
        await asyncio.wait_for(target_started.wait(), 5)
        async with asyncio.timeout(5), integration_session_factory() as observer:
            while pids["writer"] not in await observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["target"]}
            ):
                await asyncio.sleep(0.01)
        release_writer.set()
        await asyncio.wait_for(writer, 5)
        with pytest.raises(ValueError, match="target request current rack"):
            await asyncio.wait_for(target, 5)
    finally:
        release_writer.set()
        await asyncio.gather(writer, *([target] if target is not None else []), return_exceptions=True)

    async with integration_session_factory.begin() as db:
        commands = list((await db.execute(select(DeviceCommand).where(DeviceCommand.workline_id == line_id))).scalars())
        active = await rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=f"RACK-FENCE-{identity[:12]}",
            position_code=f"OUTLET-{identity[:12]}",
        )
        assert commands == []
        assert [item.rack_code for item in active] == ["RACK-2"]
        await db.execute(delete(DeviceCommand).where(DeviceCommand.workline_id == line_id))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id == execution_id))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id == response_id).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(RackPlacement).where(RackPlacement.workline_id == line_id))
        await db.execute(delete(WorkLinePosition).where(WorkLinePosition.id == outlet_position_id))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_transport_publisher_revalidates_after_accept_first_concurrent_drift(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 12)
    client_request_id = "019d0000-0000-7000-8000-000000000142"
    async with integration_session_factory.begin() as db:
        line = await _claim_workline(db, f"PUBLISHER-LOCK-{identity}", now)
        source = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"PUBLISHER-LOCK-SOURCE-{identity}",
            payload_digest="a" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            workline_id=line.id,
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
            workline_id=line.id,
            resource_fence_id="RACK-1",
            client_request_id=client_request_id,
            source_evidence_id=source.id,
        )
        db.add(binding)
        await db.flush()
        line_id = line.id
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
                final_position=RackPosition("PIPELINE_OUTLET"),
                arrival_face="270",
            ),
        ),
    )

    async def publish():
        async with integration_session_factory.begin() as db:
            return await publisher.publish(db, outcome)

    publishing = asyncio.create_task(publish())
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
        line = await _claim_workline(db, f"PUBLISHER-FACT-{identity}", now)
        source = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"PUBLISHER-FACT-SOURCE-{identity}",
            payload_digest="a" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            workline_id=line.id,
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
            workline_id=line.id,
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
                    final_position=RackPosition("PIPELINE_OUTLET"),
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
                        "final_position": {"kind": "RACK_POSITION", "location_code": "PIPELINE_OUTLET"},
                        "position_unknown": False,
                        "failure_code": None,
                        "arrival_face": "270",
                    }
                ],
            },
            received_at=now,
            workline_id=line.id,
            material_execution_id=execution.id,
            transport_task_id=transport_task_id,
            contract_key="rough_sorter.transport_outcome",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        outcome_evidence_id = accepted.evidence.id
        line_id = line.id
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
        evidence_service=InboundEvidenceService(repository=PublisherEvidenceRepository()),  # type: ignore[arg-type]
    )

    processing = asyncio.create_task(processor.process_batch(limit=1))
    await asyncio.wait_for(processor_holds_outcome.wait(), timeout=5)

    async def publish():
        async with integration_session_factory.begin() as db:
            return await publisher.publish(db, outcome)

    publishing = asyncio.create_task(publish())
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
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_old_out_bindings_remain_historical_after_admission_api_removal(integration_session_factory):
    from src.app.transport.models import TransportTask

    identity = uuid4().hex
    now = datetime(2026, 9, 6, 12)
    async with integration_session_factory.begin() as db:
        line = await _claim_workline(db, identity, now)
        line_id = line.id
        source = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=identity,
            payload_digest="a" * 64,
            normalized_payload={},
            received_at=now,
            workline_id=line_id,
            operation="inbound.material.target_decide@v1",
            operation_id=identity,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(source)
        await db.flush()
        for ordinal, status in enumerate(("SUCCEEDED", "SUCCEEDED", "PENDING")):
            client_id = f"{identity}-{ordinal}"
            task_id = f"OLD-{client_id}"
            db.add(
                TransportTask(
                    transport_task_id=task_id,
                    client_request_id=client_id,
                    request_digest="a" * 64,
                    kind="RACK_MOVE",
                    caller_json={"workline_id": str(line_id)},
                    request_json={},
                    submit_operation_id=str(uuid4()),
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="a" * 64,
                    status=status,
                    authority_workline_id=line_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                TransportDecisionBinding(
                    correlation_id=client_id,
                    step="OLD_OUT",
                    workline_id=line_id,
                    resource_fence_id=identity,
                    client_request_id=client_id,
                    source_evidence_id=source.id,
                )
            )
    try:
        async with integration_session_factory.begin() as db:
            bindings = list(
                (
                    await db.execute(
                        select(TransportDecisionBinding)
                        .where(TransportDecisionBinding.workline_id == line_id)
                        .order_by(TransportDecisionBinding.id)
                    )
                )
                .scalars()
                .all()
            )
            assert [binding.client_request_id for binding in bindings] == [
                f"{identity}-0",
                f"{identity}-1",
                f"{identity}-2",
            ]
            assert [binding.step for binding in bindings] == ["OLD_OUT", "OLD_OUT", "OLD_OUT"]
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(TransportDecisionBinding).where(TransportDecisionBinding.workline_id == line_id))
            await db.execute(delete(TransportTask).where(TransportTask.authority_workline_id == line_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.workline_id == line_id))
            await db.execute(delete(WorkLine).where(WorkLine.id == line_id))
