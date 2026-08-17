"""Decision processing 的 PostgreSQL 唯一性与事务边界。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateTransportTask,
    CreateWmsConfirmation,
    DevicePosition,
    EvidenceReadyFact,
    RackFace,
    TransportLeg,
    TransportRackPosition,
    TransportTaskType,
)

from src.app.device.models import Device, DeviceCommand
from src.app.device.services import DeviceCommandService
from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    RackReplacementTransportBinding,
    WmsConfirmation,
)
from src.app.execution.repositories import inbound_evidence_repository
from src.app.execution.services import (
    DecisionApplier,
    MaterialExecutionService,
    WmsConfirmationIdentityConflictError,
    WmsConfirmationRequest,
    WmsConfirmationService,
)
from src.app.transport.models import TransportTask
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.workline.models import LineRunEpoch, LineRunEpochDeviceBinding, WorkLine
from src.app.workline.models.workline import LineType


@pytest.mark.asyncio
async def test_specialized_unique_constraints_are_installed(integration_session_factory) -> None:
    expected = {
        "fk_device_commands_material_execution_id_material_executions",
        "ux_inbound_evidence_execution_bindings_evidence_execution",
        "ux_inbound_evidence_execution_bindings_evidence_ordinal",
        "ux_rack_replacement_transport_bindings_business_identity",
        "ux_rack_replacement_transport_bindings_client_request_id",
        "ux_line_run_epoch_device_bindings_epoch_device_role",
    }
    async with integration_session_factory() as db:
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
        def resolve(self, decision: CreateWmsConfirmation) -> WmsConfirmationRequest:
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


def _claim_evidence(
    identity: str,
    *,
    received_at: datetime,
    apply_status: InboundEvidenceApplyStatus = InboundEvidenceApplyStatus.APPLIED,
    next_attempt_at: datetime | None = None,
) -> InboundEvidence:
    return InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=identity,
        payload_digest="e" * 64,
        normalized_payload={"data": {}},
        received_at=received_at,
        device_code="CLAIM-DEVICE",
        contract_version="1.0",
        apply_status=apply_status,
        decision_next_attempt_at=next_attempt_at,
    )


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
        db.add_all(
            [
                _claim_evidence(f"{prefix}-1", received_at=now),
                _claim_evidence(f"{prefix}-2", received_at=now + timedelta(microseconds=1)),
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

    async with integration_session_factory.begin() as db:
        await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{prefix}%")))


@pytest.mark.asyncio
async def test_postgresql_decision_claim_respects_live_lease_and_recovers_expired_lease(
    integration_session_factory,
) -> None:
    identity = f"CLAIM-LEASE-{uuid4().hex}"
    now = datetime(2026, 8, 17, 12)
    async with integration_session_factory.begin() as db:
        db.add(_claim_evidence(identity, received_at=now))

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
        assert recovered[0].decision_attempt_count == 2

    async with integration_session_factory.begin() as db:
        await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity == identity))


@pytest.mark.asyncio
async def test_postgresql_decision_claim_filters_status_and_backoff_and_caps_fifo_batch_at_100(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    prefix = f"CLAIM-FIFO-{identity}"
    now = datetime(2026, 8, 17, 12)
    eligible = [
        _claim_evidence(f"{prefix}-eligible-{ordinal:03d}", received_at=now + timedelta(microseconds=ordinal))
        for ordinal in range(101)
    ]
    pending = _claim_evidence(
        f"{prefix}-pending",
        received_at=now - timedelta(seconds=2),
        apply_status=InboundEvidenceApplyStatus.PENDING,
    )
    future = _claim_evidence(
        f"{prefix}-future",
        received_at=now - timedelta(seconds=1),
        next_attempt_at=now + timedelta(minutes=1),
    )
    async with integration_session_factory.begin() as db:
        db.add_all([*eligible, pending, future])

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
        await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.like(f"{prefix}%")))
