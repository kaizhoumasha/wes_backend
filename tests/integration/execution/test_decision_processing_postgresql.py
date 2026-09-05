"""Decision processing 的 PostgreSQL 唯一性与事务边界。"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateTransportTask,
    DevicePosition,
    EvidenceReadyFact,
    TransportRackPosition,
    TransportRcsTemplateId,
    TransportResultReadyFact,
    TransportTaskType,
    Wait,
    handler,
)

from src.app.device.models import CommandStatus, Device, DeviceCommand
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services import DeviceCommandService
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    TransportDecisionBinding,
)
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.execution.repositories import (
    inbound_evidence_repository,
    material_execution_repository,
    transport_decision_binding_repository,
)
from src.app.execution.services import (
    DecisionApplier,
    FactProcessor,
    InboundEvidenceService,
    MaterialExecutionService,
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
            "ux_transport_decision_bindings_decision_identity",
            "ux_transport_decision_bindings_client_request_id",
            "fk_transport_decision_bindings_epoch",
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
        retired_role_constraint = await db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_schema = 'wes_biz' "
                "AND constraint_name = 'ux_line_run_epoch_device_bindings_epoch_device_role')"
            )
        )
        assert retired_role_constraint is False
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
                            "ix_wes_biz_transport_decision_bindings_epoch_resource",
                        ],
                    },
                )
            ).scalars()
        )
    assert retired_binding_table is None
    assert transport_indexes == {
        "ix_inbound_evidences_transport_task",
        "ix_wes_biz_inbound_evidences_transport_task_id",
        "ix_wes_biz_transport_decision_bindings_epoch_resource",
    }

    async with integration_session_factory() as db:
        binding_checks = (
            await db.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'wes_biz.transport_decision_bindings'::regclass AND contype = 'c'"
                )
            )
        ).scalars()
        assert all("OLD_OUT" not in definition and "NEW_IN" not in definition for definition in binding_checks)

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
async def test_transport_decision_identity_is_epoch_scoped_without_resource_cardinality(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = datetime(2026, 8, 18, 8)
    async with integration_session_factory.begin() as db:
        first_line, first_epoch = await _claim_epoch(db, f"FIRST-{identity}", now)
        second_line, second_epoch = await _claim_epoch(db, f"SECOND-{identity}", now)
        first_source = _claim_evidence(
            f"BINDING-FIRST-{identity}",
            received_at=now,
            line_run_epoch_id=first_epoch.id,
        )
        second_source = _claim_evidence(
            f"BINDING-SECOND-{identity}",
            received_at=now,
            line_run_epoch_id=second_epoch.id,
        )
        db.add_all([first_source, second_source])
        await db.flush()
        db.add_all(
            [
                TransportDecisionBinding(
                    correlation_id=f"CORRELATION-A-{identity}",
                    step="MOVE_IN",
                    line_run_epoch_id=first_epoch.id,
                    resource_fence_id="SHARED-STATION",
                    client_request_id=f"REQUEST-A-{identity}",
                    source_evidence_id=first_source.id,
                ),
                TransportDecisionBinding(
                    correlation_id=f"CORRELATION-B-{identity}",
                    step="MOVE_IN",
                    line_run_epoch_id=first_epoch.id,
                    resource_fence_id="SHARED-STATION",
                    client_request_id=f"REQUEST-B-{identity}",
                    source_evidence_id=first_source.id,
                ),
                TransportDecisionBinding(
                    correlation_id=f"CORRELATION-A-{identity}",
                    step="MOVE_IN",
                    line_run_epoch_id=second_epoch.id,
                    resource_fence_id="SHARED-STATION",
                    client_request_id=f"REQUEST-C-{identity}",
                    source_evidence_id=second_source.id,
                ),
            ]
        )
        await db.flush()
        binding_ids = tuple(
            (
                await db.execute(
                    select(TransportDecisionBinding.id).where(
                        TransportDecisionBinding.client_request_id.in_(
                            [f"REQUEST-A-{identity}", f"REQUEST-B-{identity}", f"REQUEST-C-{identity}"]
                        )
                    )
                )
            ).scalars()
        )

    assert len(binding_ids) == 3
    async with integration_session_factory.begin() as db:
        await db.execute(delete(TransportDecisionBinding).where(TransportDecisionBinding.id.in_(binding_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_([first_source.id, second_source.id])))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id.in_([first_epoch.id, second_epoch.id])))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_([first_line.id, second_line.id])))


@pytest.mark.asyncio
async def test_multi_decision_transaction_rolls_back_prior_effect_on_later_identity_conflict(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    line_code = f"DECISION-ATOMIC-{identity}"
    correlation_id = f"FLOW-{identity}"
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
            plugin_key="test_plugin",
            plugin_version="1.0.0",
            flow_mode="TEST_FLOW",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json={},
            started_at=now,
        )
        db.add(epoch)
        await db.flush()
        binding = LineRunEpochDeviceBinding(
            line_run_epoch_id=epoch.id,
            device_id=device.id,
            device_code=device.device_code,
            device_role="TRANSFER_DEVICE",
            endpoint_base_url="http://ecs-decision:8080",
            contract_key="test.transfer",
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
            admission_received_at=now,
            admission_evidence_id=evidence.id,
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

    applier = DecisionApplier(
        device_command_service=DeviceCommandService(session_factory=integration_session_factory, clock=lambda: now),
        wms_confirmation_service=object(),
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
            f"DEVICE-{identity}",
            "MOVE_FORWARD",
            f"TRACE-{identity}",
            DevicePosition("IN", "HANDOFF", f"TRACE-{identity}"),
            DevicePosition("OUT", "HANDOFF", f"TRACE-{identity}"),
        ),
        CreateTransportTask(
            fact.material_execution_id,
            fact.fact_id,
            TransportTaskType.RACK_MOVE,
            correlation_id,
            "MOVE_IN",
            f"RACK-CURRENT-{identity}",
            f"RACK-{identity}",
            TransportRackPosition("BUFFER"),
            TransportRackPosition("SORTER"),
            "90",
            TransportRcsTemplateId.CTU01,
        ),
        CreateTransportTask(
            fact.material_execution_id,
            fact.fact_id,
            TransportTaskType.RACK_MOVE,
            correlation_id,
            "MOVE_IN",
            f"OTHER-RESOURCE-{identity}",
            f"RACK-{identity}",
            TransportRackPosition("BUFFER"),
            TransportRackPosition("SORTER"),
            "90",
            TransportRcsTemplateId.CTU01,
        ),
    )

    with pytest.raises(ValueError, match="transport decision binding conflict"):
        async with integration_session_factory.begin() as db:
            persisted_evidence = await db.get(InboundEvidence, evidence_id, with_for_update=True)
            persisted_execution = await db.get(MaterialExecution, execution_id, with_for_update=True)
            assert persisted_evidence is not None and persisted_execution is not None
            await applier.apply(db, persisted_evidence, persisted_execution, fact, decisions)

    async with integration_session_factory.begin() as db:
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        persisted_execution = await db.get(MaterialExecution, execution_id)
        assert persisted_evidence is not None and persisted_evidence.published_at is None
        assert persisted_execution is not None and persisted_execution.status == "CREATED"
        assert (
            await db.scalar(select(DeviceCommand.id).where(DeviceCommand.material_execution_id == execution_id)) is None
        )
        assert (
            await db.scalar(
                select(TransportDecisionBinding.id).where(TransportDecisionBinding.correlation_id == correlation_id)
            )
            is None
        )
        assert (
            await db.scalar(select(TransportTask.id).where(TransportTask.client_request_id == client_request_id))
            is None
        )
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
                operation="test.workflow.source@v1",
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
                admission_received_at=now,
                admission_evidence_id=source.id,
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
        binding = TransportDecisionBinding(
            correlation_id=f"FLOW-{identity}",
            step="MOVE_IN",
            line_run_epoch_id=epoch.id,
            resource_fence_id="RACK-1",
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
        f"FLOW-{identity}",
        "MOVE_IN",
        "RACK-1",
        "RACK-2",
        TransportRackPosition("BUFFER"),
        TransportRackPosition("OUTLET"),
        "270",
        TransportRcsTemplateId.CTU01,
    )

    with pytest.raises(ValueError, match="transport decision binding conflict"):
        async with integration_session_factory.begin() as db:
            source = await db.get(InboundEvidence, source_ids[1], with_for_update=True)
            execution = await db.get(MaterialExecution, execution_ids[1], with_for_update=True)
            assert source is not None and execution is not None
            await applier.apply(db, source, execution, fact, (decision,))

    assert transport.calls == []
    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.client_request_id == client_request_id)
        )
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(source_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(source_ids)))
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
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
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
        plugin_key="test_plugin",
        plugin_version="1.0.0",
        flow_mode="TEST_FLOW",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
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
            plugin_key="test_plugin",
            plugin_version="1.0.0",
            flow_mode="TEST_FLOW",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json={},
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
            admission_received_at=now,
            admission_evidence_id=seed.id,
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
            admission_received_at=now,
            admission_evidence_id=seed.id,
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
            admission_received_at=now,
            admission_evidence_id=seed.id,
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
