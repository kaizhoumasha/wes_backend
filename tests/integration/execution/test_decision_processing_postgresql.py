"""Decision processing 的 PostgreSQL 唯一性与事务边界。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, delete, select, text, update
from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateTransportTask,
    CreateWmsConfirmation,
    DevicePosition,
    EvidenceReadyFact,
    RackFace,
    TransportLeg,
    TransportRackPosition,
    TransportResultReadyFact,
    TransportTaskType,
    Wait,
    handler,
)

from deployment._rough_sorter_transport import RoughSorterTransportOutcomePublisher
from deployment._rough_sorter_wms_facts import rack_release_snapshot
from deployment.rough_sorter_composition import _ROUGH_SORTER_TYPES, build_rough_sorter_runtime
from src.app.device.models import CommandStatus, Device, DeviceCommand
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services import DeviceCommandService
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    RackReplacementTransportBinding,
    WmsConfirmation,
    WmsConfirmationStatus,
)
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.execution.repositories import (
    inbound_evidence_repository,
    material_execution_repository,
    rack_replacement_transport_binding_repository,
)
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.execution.services import (
    DecisionApplier,
    FactProcessor,
    InboundEvidenceService,
    MaterialExecutionService,
    WmsConfirmationIdentityConflictError,
    WmsConfirmationRequest,
    WmsConfirmationService,
)
from src.app.transport.contracts import (
    RackFace as CoreRackFace,
)
from src.app.transport.contracts import (
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
    WorkLine,
)
from src.app.workline.models.workline import LineType


@pytest.mark.asyncio
async def test_specialized_unique_constraints_are_installed(integration_session_factory) -> None:
    transport_constraints = {
        constraint
        for constraint in InboundEvidence.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and any(
            fragment in str(constraint.sqltext)
            for fragment in ("(kind = 'TRANSPORT_RESULT')", "kind <> 'TRANSPORT_RESULT'")
        )
    }
    assert len(transport_constraints) == 2
    async with integration_session_factory() as db:
        transport_constraint_names = {
            db.get_bind().dialect.identifier_preparer.format_constraint(constraint)
            for constraint in transport_constraints
        }
        expected = {
            "fk_device_commands_material_execution_id_material_executions",
            "ux_rack_replacement_transport_bindings_business_identity",
            "ux_rack_replacement_transport_bindings_client_request_id",
            "ux_rack_replacement_transport_bindings_epoch_rack_leg",
            "fk_rack_replacement_transport_bindings_epoch",
            "ux_line_run_epoch_device_bindings_epoch_device_role",
            *transport_constraint_names,
        }
        names = set(
            (
                await db.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE constraint_schema = 'wes_biz' AND constraint_name = ANY(:names)"
                    ),
                    {"names": list(expected)},
                )
            ).scalars()
        )

    assert names == expected

    async with integration_session_factory() as db:
        retired_binding_table = await db.scalar(
            text("SELECT to_regclass('wes_biz.inbound_evidence_execution_bindings')")
        )
        transport_indexes = set(
            (
                await db.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'wes_biz' AND indexname = ANY(:names)"),
                    {
                        "names": [
                            "ix_inbound_evidences_transport_task",
                            "ix_wes_biz_inbound_evidences_transport_task_id",
                            "ix_wes_biz_rack_replacement_transport_bindings_epoch_rack",
                        ],
                    },
                )
            ).scalars()
        )
    assert retired_binding_table is None
    assert transport_indexes == {
        "ix_inbound_evidences_transport_task",
        "ix_wes_biz_inbound_evidences_transport_task_id",
        "ix_wes_biz_rack_replacement_transport_bindings_epoch_rack",
    }

    async with integration_session_factory() as db:
        index_names = set(
            (
                await db.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'wes_biz' "
                        "AND indexname = 'ix_wes_biz_device_commands_material_execution_id'"
                    )
                )
            ).scalars()
        )
    assert index_names == {"ix_wes_biz_device_commands_material_execution_id"}

    async with integration_session_factory() as db:
        decision_index_definition = await db.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'wes_biz' "
                "AND indexname = 'ix_inbound_evidences_decision_eligible'"
            )
        )
    assert decision_index_definition is not None
    assert "DEVICE_RESULT" in decision_index_definition
    assert "material_execution_id IS NULL" in decision_index_definition
    assert "NOT" in decision_index_definition

    async with integration_session_factory() as db:
        nullable = await db.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'wes_biz' AND table_name = 'device_commands' "
                "AND column_name = 'material_execution_id'"
            )
        )
    assert nullable == "YES"


@pytest.mark.asyncio
async def test_multi_decision_transaction_rolls_back_prior_effect_on_later_identity_conflict(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    line_code = f"DECISION-ATOMIC-{identity}"
    operation = f"decision.atomic@{identity}"
    operation_id = f"OP-{identity}"
    rack_replacement_id = f"REPLACE-{identity}"
    client_request_id = "019cd8ce-34b7-7000-8000-" + identity[:12]
    now = datetime(2026, 8, 17)
    async with integration_session_factory.begin() as db:
        line = WorkLine(line_code=line_code, line_name="Decision atomic", line_type=LineType.AUTO)
        db.add(line)
        await db.flush()
        device = Device(
            device_code=f"DEVICE-{identity}",
            device_name="Decision transfer",
            work_line_id=line.id,
            device_role="TRANSFER_DEVICE",
        )
        db.add(device)
        await db.flush()
        epoch = LineRunEpoch(
            epoch_code=f"EPOCH-{identity}",
            workline_id=line.id,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        binding = LineRunEpochDeviceBinding(
            line_run_epoch_id=epoch.id,
            device_id=device.id,
            device_code=device.device_code,
            device_role="TRANSFER_DEVICE",
            contract_key="rough_sorter.transfer",
            contract_version="1.0",
            status_max_age_ms=1_000,
            command_timeout_ms=5_000,
        )
        db.add(binding)
        await db.flush()
        evidence = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"SCAN-{identity}",
            payload_digest="c" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            device_code=f"DEVICE-{identity}",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add(evidence)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"EXEC-{identity}",
            material_trace_id=f"TRACE-{identity}",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=evidence.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        evidence.material_execution_id = execution.id
        evidence_id = evidence.id
        execution_id = execution.id
        epoch_id = epoch.id
        binding_id = binding.id
        device_id = device.id
        line_id = line.id

    class _Resolver:
        async def resolve(self, db: object, decision: CreateWmsConfirmation) -> WmsConfirmationRequest:
            del db
            marker = decision.snapshot_refs[0]
            return WmsConfirmationRequest({"marker": marker}, now + timedelta(minutes=1))

    applier = DecisionApplier(
        device_command_service=DeviceCommandService(session_factory=integration_session_factory, clock=lambda: now),
        wms_confirmation_service=WmsConfirmationService(),
        wms_request_resolver=_Resolver(),
        transport_service=TransportService(
            integration_session_factory,
            TransportRepository(),
            SimpleNamespace(),
        ),
        material_execution_service=MaterialExecutionService(),
        clock=lambda: now,
        uuid_factory=lambda: client_request_id,
    )
    fact = EvidenceReadyFact(
        fact_id=f"evidence:{evidence_id}",
        evidence_id=str(evidence_id),
        fact_version="1.0",
        material_execution_id=f"EXEC-{identity}",
    )
    decisions = (
        CreateDeviceCommand(
            fact.material_execution_id,
            fact.fact_id,
            "TRANSFER_DEVICE",
            "MOVE_FORWARD",
            f"TRACE-{identity}",
            DevicePosition("IN", "HANDOFF", f"TRACE-{identity}"),
            DevicePosition("OUT", "HANDOFF", f"TRACE-{identity}"),
        ),
        CreateTransportTask(
            fact.material_execution_id,
            fact.fact_id,
            TransportTaskType.RACK_MOVE,
            rack_replacement_id,
            TransportLeg.NEW_IN,
            f"RACK-CURRENT-{identity}",
            f"RACK-{identity}",
            TransportRackPosition("BUFFER"),
            TransportRackPosition("SORTER"),
            RackFace.A,
        ),
        CreateWmsConfirmation(
            fact.material_execution_id,
            fact.fact_id,
            operation,
            operation_id,
            (fact.evidence_id,),
            ("first",),
        ),
        CreateWmsConfirmation(
            fact.material_execution_id,
            fact.fact_id,
            operation,
            operation_id,
            (fact.evidence_id,),
            ("second",),
        ),
    )

    with pytest.raises(WmsConfirmationIdentityConflictError):
        async with integration_session_factory.begin() as db:
            persisted_evidence = await db.get(InboundEvidence, evidence_id, with_for_update=True)
            persisted_execution = await db.get(MaterialExecution, execution_id, with_for_update=True)
            assert persisted_evidence is not None and persisted_execution is not None
            await applier.apply(db, persisted_evidence, persisted_execution, fact, decisions)

    async with integration_session_factory.begin() as db:
        assert (
            await db.scalar(
                select(WmsConfirmation.id).where(
                    WmsConfirmation.operation == operation,
                    WmsConfirmation.operation_id == operation_id,
                )
            )
            is None
        )
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        persisted_execution = await db.get(MaterialExecution, execution_id)
        assert persisted_evidence is not None and persisted_evidence.published_at is None
        assert persisted_execution is not None and persisted_execution.status == "CREATED"
        assert (
            await db.scalar(select(DeviceCommand.id).where(DeviceCommand.material_execution_id == execution_id)) is None
        )
        assert (
            await db.scalar(
                select(RackReplacementTransportBinding.id).where(
                    RackReplacementTransportBinding.rack_replacement_id == rack_replacement_id
                )
            )
            is None
        )
        assert (
            await db.scalar(select(TransportTask.id).where(TransportTask.client_request_id == client_request_id))
            is None
        )

        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.operation == operation))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id == evidence_id).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == evidence_id))
        await db.execute(delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.id == binding_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(Device).where(Device.id == device_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_concrete_rough_sorter_composition_correlates_first_scan_in_the_claim_transaction(
    integration_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    async with integration_session_factory.begin() as db:
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
            configuration_digest=configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND"),
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
    runtime = build_rough_sorter_runtime(
        session_factory=integration_session_factory,
        transport_runtime=SimpleNamespace(service=object(), repository=object(), client=object()),  # type: ignore[arg-type]
        device_command_service=DeviceCommandService(session_factory=integration_session_factory, clock=lambda: now),
    )

    assert await runtime.execution.fact_processor.process_batch() == 1

    async with integration_session_factory.begin() as db:
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        assert persisted_evidence is not None and persisted_evidence.material_execution_id is not None
        execution = await db.get(MaterialExecution, persisted_evidence.material_execution_id)
        assert execution is not None
        assert execution.material_trace_id == f"TRACE-{identity}"
        assert persisted_evidence.published_at is not None
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
            types=_ROUGH_SORTER_TYPES,
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
            await rack_replacement_transport_binding_repository.lock_rack_fence(
                db,
                line_run_epoch_id=epoch_id,
                current_rack_id=rack_id,
            )
            fence = await rack_replacement_transport_binding_repository.get_old_out_fence_for_update(
                db,
                line_run_epoch_id=epoch_id,
                current_rack_id=rack_id,
            )
            if fence is not None:
                return False
            db.add(
                DeviceCommand(
                    command_code=f"019d0000-0000-7000-8000-{suffix}",
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

    async def create_old_out_fence(*, hold_lock: bool = False) -> None:
        async with integration_session_factory.begin() as db:
            if not hold_lock:
                contender_started.set()
            await rack_replacement_transport_binding_repository.lock_rack_fence(
                db,
                line_run_epoch_id=epoch_id,
                current_rack_id="RACK-1",
            )
            db.add(
                RackReplacementTransportBinding(
                    rack_replacement_id=f"REPLACE-{identity}",
                    leg="OLD_OUT",
                    line_run_epoch_id=epoch_id,
                    current_rack_id="RACK-1",
                    client_request_id=f"019d0000-0000-7000-8001-{identity[:12]}",
                    source_evidence_id=seed_ids[1],
                )
            )
            await db.flush()
            if hold_lock:
                first_has_lock.set()
                await release_first_owner.wait()

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
        assert first_result is None and contender_result is False
    else:
        assert first_result is True and contender_result is None

    assert await create_target_command("RACK-1", "000000000103") is False
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
        await db.execute(
            delete(RackReplacementTransportBinding).where(RackReplacementTransportBinding.line_run_epoch_id == epoch_id)
        )
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
async def test_postgresql_decision_applier_rejects_existing_transport_binding_from_other_source(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 11)
    client_request_id = "019d0000-0000-7000-8000-000000000141"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, f"BINDING-CORRELATION-{identity}", now)
        sources = [
            InboundEvidence(
                kind=InboundEvidenceKind.WMS_RESULT,
                source_identity=f"BINDING-CORRELATION-SOURCE-{ordinal}-{identity}",
                payload_digest=str(ordinal) * 64,
                normalized_payload={"data": {}},
                received_at=now,
                line_run_epoch_id=epoch.id,
                operation="inbound.source_rack.replacement_plan_decide@v1",
                operation_id=f"BINDING-CORRELATION-OP-{ordinal}-{identity}",
                contract_version="1.0",
                apply_status=InboundEvidenceApplyStatus.IGNORED,
            )
            for ordinal in (1, 2)
        ]
        db.add_all(sources)
        await db.flush()
        executions = [
            MaterialExecution(
                execution_code=f"BINDING-CORRELATION-EXEC-{ordinal}-{identity}",
                material_trace_id=f"BINDING-CORRELATION-TRACE-{ordinal}-{identity}",
                workline_id=line.id,
                line_run_epoch_id=epoch.id,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=source.id,
                status_changed_at=now,
            )
            for ordinal, source in enumerate(sources, start=1)
        ]
        db.add_all(executions)
        await db.flush()
        for source, execution in zip(sources, executions, strict=True):
            source.material_execution_id = execution.id
        binding = RackReplacementTransportBinding(
            rack_replacement_id=f"REPLACE-{identity}",
            leg="NEW_IN",
            line_run_epoch_id=epoch.id,
            current_rack_id="RACK-1",
            client_request_id=client_request_id,
            source_evidence_id=sources[0].id,
        )
        db.add(binding)
        await db.flush()
        line_id = line.id
        epoch_id = epoch.id
        source_ids = tuple(source.id for source in sources)
        execution_ids = tuple(execution.id for execution in executions)

    class Transport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def move_rack_in_session(self, db: object, **kwargs: object) -> object:
            del db
            self.calls.append(kwargs)
            return object()

    transport = Transport()
    applier = DecisionApplier(
        device_command_service=object(),
        wms_confirmation_service=object(),
        wms_request_resolver=object(),
        transport_service=transport,
        material_execution_service=object(),
        clock=lambda: now,
    )
    fact = EvidenceReadyFact(
        fact_id=f"evidence:{source_ids[1]}",
        evidence_id=str(source_ids[1]),
        fact_version="1.0",
        material_execution_id=f"BINDING-CORRELATION-EXEC-2-{identity}",
    )
    decision = CreateTransportTask(
        fact.material_execution_id,
        fact.fact_id,
        TransportTaskType.RACK_MOVE,
        f"REPLACE-{identity}",
        TransportLeg.NEW_IN,
        "RACK-1",
        "RACK-2",
        TransportRackPosition("BUFFER"),
        TransportRackPosition("OUTLET"),
        RackFace.B,
    )

    with pytest.raises(ValueError, match="binding correlation"):
        async with integration_session_factory.begin() as db:
            source = await db.get(InboundEvidence, source_ids[1], with_for_update=True)
            execution = await db.get(MaterialExecution, execution_ids[1], with_for_update=True)
            assert source is not None and execution is not None
            await applier.apply(db, source, execution, fact, (decision,))

    assert transport.calls == []
    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(RackReplacementTransportBinding).where(
                RackReplacementTransportBinding.client_request_id == client_request_id
            )
        )
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(source_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(source_ids)))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_transport_publisher_revalidates_after_execution_first_concurrent_drift(
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
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=source.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        source.material_execution_id = execution.id
        binding = RackReplacementTransportBinding(
            rack_replacement_id=f"PUBLISHER-LOCK-REPLACE-{identity}",
            leg="NEW_IN",
            line_run_epoch_id=epoch.id,
            current_rack_id="RACK-1",
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
    publisher_attempting_execution = asyncio.Event()

    class SignallingExecutionRepository:
        async def get_by_id_for_update(self, db: object, execution_id: int) -> MaterialExecution | None:
            publisher_attempting_execution.set()
            return await material_execution_repository.get_by_id_for_update(db, execution_id)  # type: ignore[arg-type]

    async def drift_binding_in_execution_first_order() -> None:
        async with integration_session_factory.begin() as db:
            locked_execution = await material_execution_repository.get_by_id_for_update(db, execution_id)
            assert locked_execution is not None
            owner_locked_execution.set()
            await publisher_attempting_execution.wait()
            locked_binding = await rack_replacement_transport_binding_repository.get_by_client_request_id_for_update(
                db, client_request_id
            )
            locked_source = await inbound_evidence_repository.get_by_id_for_update(db, source_id)
            assert locked_binding is not None and locked_source is not None
            locked_binding.current_rack_id = "RACK-DRIFT"

    owner = asyncio.create_task(drift_binding_in_execution_first_order())
    await owner_locked_execution.wait()
    publisher = RoughSorterTransportOutcomePublisher(
        session_factory=integration_session_factory,
        execution_repository=SignallingExecutionRepository(),
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
                arrival_face=CoreRackFace.B,
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
            delete(RackReplacementTransportBinding).where(
                RackReplacementTransportBinding.client_request_id == client_request_id
            )
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
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=source.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        source.material_execution_id = execution.id
        binding = RackReplacementTransportBinding(
            rack_replacement_id=f"PUBLISHER-FACT-REPLACE-{identity}",
            leg="NEW_IN",
            line_run_epoch_id=epoch.id,
            current_rack_id="RACK-1",
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
                    arrival_face=CoreRackFace.B,
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
                        "arrival_face": "B",
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
            delete(RackReplacementTransportBinding).where(
                RackReplacementTransportBinding.client_request_id == client_request_id
            )
        )
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == outcome_evidence_id))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id == source_id).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == source_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


def _claim_evidence(
    identity: str,
    *,
    received_at: datetime,
    line_run_epoch_id: int,
    apply_status: InboundEvidenceApplyStatus = InboundEvidenceApplyStatus.APPLIED,
    next_attempt_at: datetime | None = None,
) -> InboundEvidence:
    return InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=identity,
        payload_digest="e" * 64,
        normalized_payload={"data": {}},
        received_at=received_at,
        line_run_epoch_id=line_run_epoch_id,
        device_code="CLAIM-DEVICE",
        contract_version="1.0",
        apply_status=apply_status,
        decision_next_attempt_at=next_attempt_at,
    )


async def _claim_epoch(db, identity: str, now: datetime) -> tuple[WorkLine, LineRunEpoch]:
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
        started_at=now,
    )
    db.add(epoch)
    await db.flush()
    return line, epoch


async def _cleanup_claim_epoch(
    integration_session_factory,
    *,
    source_identity_prefix: str,
    epoch: LineRunEpoch,
    line: WorkLine,
) -> None:
    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{source_identity_prefix}%"))
        )
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch.id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line.id))


@pytest.mark.asyncio
async def test_postgresql_decision_claim_skips_foundation_result_and_keeps_correlated_result(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 17, 12)
    async with integration_session_factory.begin() as db:
        line = WorkLine(
            line_code=f"CLAIM-RESULT-{identity}",
            line_name="Decision result claim",
            line_type=LineType.AUTO,
        )
        db.add(line)
        await db.flush()
        epoch = LineRunEpoch(
            epoch_code=f"EPOCH-RESULT-{identity}",
            workline_id=line.id,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="ROUGH_SORT_INBOUND",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        seed = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"CLAIM-SEED-{identity}",
            payload_digest="f" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            device_code="CLAIM-DEVICE",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(seed)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"CLAIM-EXEC-{identity}",
            material_trace_id=f"CLAIM-TRACE-{identity}",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=seed.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        foundation = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_RESULT,
            source_identity=f"CLAIM-FOUNDATION-{identity}",
            payload_digest="1" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=None,
            device_code="CLAIM-DEVICE",
            command_code=f"CLAIM-CMD-FOUNDATION-{identity}",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        correlated = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_RESULT,
            source_identity=f"CLAIM-CORRELATED-{identity}",
            payload_digest="2" * 64,
            normalized_payload={"data": {}},
            received_at=now + timedelta(microseconds=1),
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            device_code="CLAIM-DEVICE",
            command_code=f"CLAIM-CMD-CORRELATED-{identity}",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add_all([foundation, correlated])
        await db.flush()
        seed_id = seed.id
        execution_id = execution.id
        epoch_id = epoch.id
        line_id = line.id

    async with integration_session_factory.begin() as db:
        claimed = await inbound_evidence_repository.claim_decision_batch(
            db,
            now=now,
            claim_token="claim-result",
            claim_expires_at=now + timedelta(seconds=30),
            limit=10,
        )
        assert [item.source_identity for item in claimed] == [f"CLAIM-CORRELATED-{identity}"]

    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(InboundEvidence).where(
                InboundEvidence.source_identity.in_([f"CLAIM-FOUNDATION-{identity}", f"CLAIM-CORRELATED-{identity}"])
            )
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id == seed_id))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
        await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


@pytest.mark.asyncio
async def test_postgresql_decision_claim_skip_locked_does_not_reissue_to_second_session(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 17, 12)
    prefix = f"CLAIM-SKIP-{identity}"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
        db.add_all(
            [
                _claim_evidence(f"{prefix}-1", received_at=now, line_run_epoch_id=epoch.id),
                _claim_evidence(f"{prefix}-2", received_at=now + timedelta(microseconds=1), line_run_epoch_id=epoch.id),
            ]
        )

    first_session = integration_session_factory()
    second_session = integration_session_factory()
    try:
        async with first_session.begin():
            first = await inbound_evidence_repository.claim_decision_batch(
                first_session,
                now=now,
                claim_token="claim-first",
                claim_expires_at=now + timedelta(seconds=30),
                limit=1,
            )
            async with second_session.begin():
                second = await inbound_evidence_repository.claim_decision_batch(
                    second_session,
                    now=now,
                    claim_token="claim-second",
                    claim_expires_at=now + timedelta(seconds=30),
                    limit=1,
                )
                assert [item.source_identity for item in first] == [f"{prefix}-1"]
                assert [item.source_identity for item in second] == [f"{prefix}-2"]
    finally:
        await first_session.close()
        await second_session.close()
        await _cleanup_claim_epoch(
            integration_session_factory,
            source_identity_prefix=prefix,
            epoch=epoch,
            line=line,
        )


@pytest.mark.asyncio
async def test_postgresql_transport_outcomes_are_claimed_in_unknown_causal_order_across_sessions(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 17, 12)
    prefix = f"CLAIM-TRANSPORT-ORDER-{identity}"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
        seed = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"{prefix}-SEED",
            payload_digest="1" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            device_code="CLAIM-DEVICE",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(seed)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"{prefix}-EXEC",
            material_trace_id=f"{prefix}-TRACE",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=seed.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        task_id = f"{prefix}-TASK"
        unknown = InboundEvidence(
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{task_id}:outcome:1",
            payload_digest="2" * 64,
            normalized_payload={"transport_task_id": task_id, "outcome_version": 1, "status": "UNKNOWN"},
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=task_id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        succeeded = InboundEvidence(
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{task_id}:outcome:2",
            payload_digest="3" * 64,
            normalized_payload={"transport_task_id": task_id, "outcome_version": 2, "status": "SUCCEEDED"},
            received_at=now + timedelta(microseconds=1),
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=task_id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add_all([unknown, succeeded])
        await db.flush()
        evidence_ids = (unknown.id, succeeded.id)
        execution_id = execution.id

    first_session = integration_session_factory()
    second_session = integration_session_factory()
    try:
        async with first_session.begin():
            first = await inbound_evidence_repository.claim_decision_batch(
                first_session,
                now=now,
                claim_token="claim-unknown",
                claim_expires_at=now + timedelta(seconds=30),
                limit=1,
            )
            async with second_session.begin():
                second = await inbound_evidence_repository.claim_decision_batch(
                    second_session,
                    now=now,
                    claim_token="claim-determinate",
                    claim_expires_at=now + timedelta(seconds=30),
                    limit=1,
                )
                assert [item.id for item in first] == [evidence_ids[0]]
                assert second == []
    finally:
        await first_session.close()
        await second_session.close()
        async with integration_session_factory.begin() as db:
            await db.execute(
                update(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)).values(material_execution_id=None)
            )
            await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{prefix}%")))
            await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch.id))
            await db.execute(delete(WorkLine).where(WorkLine.id == line.id))


@pytest.mark.asyncio
async def test_postgresql_persist_only_unknown_does_not_block_later_determinate_outcome(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 17, 12)
    prefix = f"CLAIM-TRANSPORT-PERSIST-ONLY-{identity}"
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
        seed = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"{prefix}-SEED",
            payload_digest="1" * 64,
            normalized_payload={"data": {}},
            received_at=now,
            line_run_epoch_id=epoch.id,
            device_code="CLAIM-DEVICE",
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        db.add(seed)
        await db.flush()
        execution = MaterialExecution(
            execution_code=f"{prefix}-EXEC",
            material_trace_id=f"{prefix}-TRACE",
            workline_id=line.id,
            line_run_epoch_id=epoch.id,
            last_transition_reason="INITIAL_EVIDENCE",
            last_transition_evidence_id=seed.id,
            status_changed_at=now,
        )
        db.add(execution)
        await db.flush()
        task_id = f"{prefix}-TASK"
        processed_unknown = InboundEvidence(
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{task_id}:outcome:1",
            payload_digest="2" * 64,
            normalized_payload={"transport_task_id": task_id, "outcome_version": 1, "status": "UNKNOWN"},
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=task_id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            published_at=now,
            decision_digest="5" * 64,
        )
        db.add(processed_unknown)
        await db.flush()
        execution.status = MaterialExecutionStatus.RECONCILING
        execution.last_transition_evidence_id = processed_unknown.id
        persist_only_unknown = InboundEvidence(
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{task_id}:outcome:2",
            payload_digest="3" * 64,
            normalized_payload={"transport_task_id": task_id, "outcome_version": 2, "status": "UNKNOWN"},
            received_at=now + timedelta(microseconds=1),
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=task_id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.IGNORED,
        )
        determinate = InboundEvidence(
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity=f"transport:{task_id}:outcome:3",
            payload_digest="4" * 64,
            normalized_payload={"transport_task_id": task_id, "outcome_version": 3, "status": "SUCCEEDED"},
            received_at=now + timedelta(microseconds=2),
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            transport_task_id=task_id,
            contract_version="1.0",
            apply_status=InboundEvidenceApplyStatus.APPLIED,
        )
        db.add_all([persist_only_unknown, determinate])
        await db.flush()
        evidence_ids = (processed_unknown.id, persist_only_unknown.id, determinate.id)
        execution_id = execution.id

    first_session = integration_session_factory()
    second_session = integration_session_factory()
    try:
        async with first_session.begin():
            first = await inbound_evidence_repository.claim_decision_batch(
                first_session,
                now=now,
                claim_token="claim-determinate-first",
                claim_expires_at=now + timedelta(seconds=30),
                limit=1,
            )
            async with second_session.begin():
                second = await inbound_evidence_repository.claim_decision_batch(
                    second_session,
                    now=now,
                    claim_token="claim-determinate-second",
                    claim_expires_at=now + timedelta(seconds=30),
                    limit=1,
                )
                assert [item.id for item in first] == [evidence_ids[2]]
                assert second == []
    finally:
        await first_session.close()
        await second_session.close()
        async with integration_session_factory.begin() as db:
            await db.execute(
                update(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)).values(material_execution_id=None)
            )
            await db.execute(delete(MaterialExecution).where(MaterialExecution.id == execution_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{prefix}%")))
            await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch.id))
            await db.execute(delete(WorkLine).where(WorkLine.id == line.id))


@pytest.mark.asyncio
async def test_postgresql_decision_claim_respects_live_lease_and_recovers_expired_lease(
    integration_session_factory,
) -> None:
    identity = f"CLAIM-LEASE-{uuid4().hex}"
    now = datetime(2026, 8, 17, 12)
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
        db.add(_claim_evidence(identity, received_at=now, line_run_epoch_id=epoch.id))

    try:
        async with integration_session_factory.begin() as db:
            first = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now,
                claim_token="claim-first",
                claim_expires_at=now + timedelta(seconds=30),
                limit=1,
            )
            assert [item.source_identity for item in first] == [identity]

        async with integration_session_factory.begin() as db:
            live_lease = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now + timedelta(seconds=10),
                claim_token="claim-too-early",
                claim_expires_at=now + timedelta(seconds=40),
                limit=1,
            )
            assert live_lease == []

        async with integration_session_factory.begin() as db:
            recovered = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now + timedelta(seconds=31),
                claim_token="claim-recovered",
                claim_expires_at=now + timedelta(seconds=61),
                limit=1,
            )
            assert [item.source_identity for item in recovered] == [identity]
            assert recovered[0].decision_attempt_count == 0
    finally:
        await _cleanup_claim_epoch(
            integration_session_factory,
            source_identity_prefix=identity,
            epoch=epoch,
            line=line,
        )


@pytest.mark.asyncio
async def test_postgresql_decision_claim_filters_status_and_backoff_and_caps_fifo_batch_at_100(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    prefix = f"CLAIM-FIFO-{identity}"
    now = datetime(2026, 8, 17, 12)
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
    eligible = [
        _claim_evidence(
            f"{prefix}-eligible-{ordinal:03d}",
            received_at=now + timedelta(microseconds=ordinal),
            line_run_epoch_id=epoch.id,
        )
        for ordinal in range(100)
    ]
    pending = _claim_evidence(
        f"{prefix}-pending",
        received_at=now - timedelta(seconds=2),
        line_run_epoch_id=epoch.id,
        apply_status=InboundEvidenceApplyStatus.PENDING,
    )
    future = _claim_evidence(
        f"{prefix}-future",
        received_at=now - timedelta(seconds=1),
        line_run_epoch_id=epoch.id,
        next_attempt_at=now + timedelta(minutes=1),
    )
    deferred = _claim_evidence(
        f"{prefix}-deferred",
        received_at=now - timedelta(seconds=3),
        line_run_epoch_id=epoch.id,
        next_attempt_at=now,
    )
    async with integration_session_factory.begin() as db:
        db.add_all([*eligible, pending, future, deferred])

    try:
        async with integration_session_factory.begin() as db:
            claimed = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now,
                claim_token="claim-batch",
                claim_expires_at=now + timedelta(seconds=30),
                limit=100,
            )
            assert [item.source_identity for item in claimed] == [
                f"{prefix}-eligible-{ordinal:03d}" for ordinal in range(100)
            ]

        async with integration_session_factory.begin() as db:
            rotated = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now,
                claim_token="claim-deferred",
                claim_expires_at=now + timedelta(seconds=30),
                limit=100,
            )
            assert [item.source_identity for item in rotated] == [f"{prefix}-deferred"]
    finally:
        await _cleanup_claim_epoch(
            integration_session_factory,
            source_identity_prefix=prefix,
            epoch=epoch,
            line=line,
        )


@pytest.mark.asyncio
async def test_postgresql_decision_claim_never_claims_a_closed_epoch(integration_session_factory) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 17, 12)
    async with integration_session_factory.begin() as db:
        line, epoch = await _claim_epoch(db, identity, now)
        epoch.status = "CLOSED"
        epoch.closed_at = now
        evidence = _claim_evidence(f"CLAIM-CLOSED-{identity}", received_at=now, line_run_epoch_id=epoch.id)
        db.add(evidence)

    try:
        async with integration_session_factory.begin() as db:
            claimed = await inbound_evidence_repository.claim_decision_batch(
                db,
                now=now,
                claim_token="claim-closed",
                claim_expires_at=now + timedelta(seconds=30),
                limit=100,
            )
            assert claimed == []
    finally:
        await _cleanup_claim_epoch(
            integration_session_factory,
            source_identity_prefix=f"CLAIM-CLOSED-{identity}",
            epoch=epoch,
            line=line,
        )
