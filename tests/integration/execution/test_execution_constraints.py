"""PostgreSQL 对核心 execution owner 约束的最终裁决。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
    WmsConfirmation,
)
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceIdentityConflictError,
    InboundEvidenceService,
)
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictError,
    WmsConfirmationResponseConflictError,
    WmsConfirmationService,
)
from src.app.workline.models.line_run_epoch import LineRunEpoch
from src.app.workline.models.workline import LineType, WorkLine

PREFIX = "EXECUTION-CONSTRAINT-"


async def _seed_epoch(db) -> tuple[WorkLine, LineRunEpoch, str]:
    identity = uuid4().hex
    line = WorkLine(
        line_code=f"{PREFIX}LINE-{identity[:20]}",
        line_name="Execution constraints",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"{PREFIX}EPOCH-{identity}",
        workline_id=line.id,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 16),
    )
    db.add(epoch)
    await db.flush()
    return line, epoch, identity


async def _seed_creation_evidence(db, epoch: LineRunEpoch, identity: str) -> InboundEvidence:
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=f"{PREFIX}SCAN-{identity}",
        payload_digest="1" * 64,
        normalized_payload={"source_event_id": f"{PREFIX}SCAN-{identity}"},
        received_at=datetime(2026, 8, 16),
        line_run_epoch_id=epoch.id,
        device_code=f"{PREFIX}MEASUREMENT-{identity}",
    )
    db.add(evidence)
    await db.flush()
    return evidence


def _execution(
    line: WorkLine,
    epoch: LineRunEpoch,
    identity: str,
    *,
    evidence_id: int,
) -> MaterialExecution:
    return MaterialExecution(
        execution_code=f"{PREFIX}MATERIAL-{identity}",
        material_trace_id=f"{PREFIX}TRACE-{identity}",
        workline_id=line.id,
        line_run_epoch_id=epoch.id,
        status=MaterialExecutionStatus.CREATED,
        last_transition_reason="SCAN_ACCEPTED",
        last_transition_evidence_id=evidence_id,
        status_changed_at=datetime(2026, 8, 16),
    )


@pytest_asyncio.fixture(autouse=True)
async def cleanup_execution_constraint_rows(integration_session_factory):
    yield
    async with integration_session_factory.begin() as db:
        execution_ids = select(MaterialExecution.id).where(MaterialExecution.execution_code.like(f"{PREFIX}%"))
        evidence_ids = select(InboundEvidence.id).where(InboundEvidence.source_identity.like(f"{PREFIX}%"))
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like(f"{PREFIX}%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like(f"{PREFIX}%"))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id.in_(execution_ids)))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id.in_(epoch_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))


@pytest.mark.asyncio
async def test_postgresql_allows_only_one_active_execution_per_material_trace(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        line, epoch, identity = await _seed_epoch(db)
        first_evidence = await _seed_creation_evidence(db, epoch, identity)
        second_evidence = await _seed_creation_evidence(db, epoch, f"SECOND-{identity}")
        first = _execution(line, epoch, identity, evidence_id=first_evidence.id)
        second = _execution(
            line,
            epoch,
            f"SECOND-{identity}",
            evidence_id=second_evidence.id,
        )
        second.material_trace_id = first.material_trace_id
        db.add(first)
        await db.flush()
        db.add(second)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_freezes_inbound_source_and_wms_operation_identity(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        line, epoch, identity = await _seed_epoch(db)
        creation_evidence = await _seed_creation_evidence(db, epoch, identity)
        execution = _execution(line, epoch, identity, evidence_id=creation_evidence.id)
        db.add(execution)
        await db.flush()
        source_identity = f"{PREFIX}WMS-RESULT-{identity}"
        evidence = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=source_identity,
            payload_digest="c" * 64,
            normalized_payload={"result": "WAIT"},
            received_at=datetime(2026, 8, 16),
            material_execution_id=execution.id,
            operation="inbound.material.admission_decide@v1",
            operation_id=f"{PREFIX}OP-{identity}",
        )
        db.add(evidence)
        await db.flush()
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.WMS_RESULT,
                source_identity=source_identity,
                payload_digest="d" * 64,
                normalized_payload={"result": "ACCEPT"},
                received_at=datetime(2026, 8, 16),
                material_execution_id=execution.id,
                operation="inbound.material.admission_decide@v1",
                operation_id=f"{PREFIX}OP-OTHER-{identity}",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_wms_confirmation_identity_is_operation_plus_operation_id(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        line, epoch, identity = await _seed_epoch(db)
        creation_evidence = await _seed_creation_evidence(db, epoch, identity)
        execution = _execution(line, epoch, identity, evidence_id=creation_evidence.id)
        db.add(execution)
        await db.flush()
        operation_id = f"{PREFIX}OP-{identity}"
        first = WmsConfirmation(
            operation="inbound.material.admission_decide@v1",
            operation_id=operation_id,
            material_execution_id=execution.id,
            request_digest="e" * 64,
            request_payload={"data": {}},
            deadline_at=datetime(2026, 8, 16, 0, 5),
        )
        second = WmsConfirmation(
            operation="inbound.material.admission_decide@v1",
            operation_id=operation_id,
            material_execution_id=execution.id,
            request_digest="f" * 64,
            request_payload={"data": {"changed": True}},
            deadline_at=datetime(2026, 8, 16, 0, 5),
        )
        db.add(first)
        await db.flush()
        db.add(second)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_direct_cutover_schema_has_no_previous_evidence_or_resource_confirmation_owner(
    integration_session_factory,
) -> None:
    async with integration_session_factory() as db:
        old_tables = set(
            (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'wes_biz' "
                        "AND table_name IN ('device_evidences', 'device_evidence_conflicts')"
                    )
                )
            ).scalars()
        )
        old_column_exists = await db.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'wes_biz' "
                "AND table_name = 'resource_bin_material_mounts' "
                "AND column_name = 'wms_confirmation_status'"
                ")"
            )
        )

    assert old_tables == set()
    assert old_column_exists is False


@pytest.mark.asyncio
async def test_inbound_conflict_commits_before_transaction_owner_maps_error(
    integration_session_factory,
) -> None:
    service = InboundEvidenceService()
    identity = uuid4().hex
    source_identity = f"{PREFIX}CONFLICT-{identity}"
    async with integration_session_factory.begin() as db:
        await service.accept(
            db,
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=source_identity,
            normalized_payload={"source_event_id": source_identity, "data": {"result": "PASS"}},
            received_at=datetime(2026, 8, 16),
            device_code=f"{PREFIX}DEVICE-{identity}",
        )

    async with integration_session_factory.begin() as db:
        conflict_result = await service.accept(
            db,
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=source_identity,
            normalized_payload={"source_event_id": source_identity, "data": {"result": "FAIL"}},
            received_at=datetime(2026, 8, 16, 0, 1),
            device_code=f"{PREFIX}DEVICE-{identity}",
        )

    with pytest.raises(InboundEvidenceIdentityConflictError):
        raise conflict_result.to_exception()
    async with integration_session_factory() as db:
        persisted = await db.scalar(
            select(InboundEvidenceConflict).where(InboundEvidenceConflict.source_identity == source_identity)
        )
    assert persisted is not None
    assert persisted.reason_code == "SOURCE_IDENTITY_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_wms_identity_conflict_commits_reconciling_before_error_mapping(
    integration_session_factory,
) -> None:
    service = WmsConfirmationService()
    async with integration_session_factory.begin() as db:
        line, epoch, identity = await _seed_epoch(db)
        creation_evidence = await _seed_creation_evidence(db, epoch, identity)
        execution = _execution(line, epoch, identity, evidence_id=creation_evidence.id)
        db.add(execution)
        await db.flush()
        accepted = await service.create_or_get(
            db,
            operation="inbound.material.admission_decide@v1",
            operation_id=f"{PREFIX}OP-{identity}",
            material_execution_id=execution.id,
            request_payload={"data": {"material_trace_id": execution.material_trace_id}},
            deadline_at=datetime(2026, 8, 16, 0, 5),
            created_at=datetime(2026, 8, 16),
        )
        confirmation_id = accepted.confirmation.id

    async with integration_session_factory.begin() as db:
        conflict_result = await service.create_or_get(
            db,
            operation="inbound.material.admission_decide@v1",
            operation_id=f"{PREFIX}OP-{identity}",
            material_execution_id=execution.id,
            request_payload={"data": {"material_trace_id": "CHANGED"}},
            deadline_at=datetime(2026, 8, 16, 0, 5),
            created_at=datetime(2026, 8, 16),
        )

    with pytest.raises(WmsConfirmationIdentityConflictError):
        raise conflict_result.to_exception()
    async with integration_session_factory() as db:
        status = await db.scalar(select(WmsConfirmation.status).where(WmsConfirmation.id == confirmation_id))
    assert status == "RECONCILING"


@pytest.mark.asyncio
async def test_wms_response_conflict_commits_reconciling_before_error_mapping(
    integration_session_factory,
) -> None:
    service = WmsConfirmationService()
    async with integration_session_factory.begin() as db:
        line, epoch, identity = await _seed_epoch(db)
        creation_evidence = await _seed_creation_evidence(db, epoch, identity)
        execution = _execution(line, epoch, identity, evidence_id=creation_evidence.id)
        db.add(execution)
        await db.flush()
        operation = f"{PREFIX}WMS-OP"
        accepted = await service.create_or_get(
            db,
            operation=operation,
            operation_id=f"{PREFIX}OP-{identity}",
            material_execution_id=execution.id,
            request_payload={"data": {"material_trace_id": execution.material_trace_id}},
            deadline_at=datetime(2026, 8, 16, 0, 5),
            created_at=datetime(2026, 8, 16),
        )
        first_response = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"{operation}:{PREFIX}RESULT-1-{identity}",
            payload_digest="7" * 64,
            normalized_payload={"result": "WAIT"},
            received_at=datetime(2026, 8, 16, 0, 1),
            material_execution_id=execution.id,
            operation=operation,
            operation_id=f"{PREFIX}RESULT-1-{identity}",
        )
        second_response = InboundEvidence(
            kind=InboundEvidenceKind.WMS_RESULT,
            source_identity=f"{operation}:{PREFIX}RESULT-2-{identity}",
            payload_digest="8" * 64,
            normalized_payload={"result": "ACCEPT"},
            received_at=datetime(2026, 8, 16, 0, 2),
            material_execution_id=execution.id,
            operation=operation,
            operation_id=f"{PREFIX}RESULT-2-{identity}",
        )
        db.add(first_response)
        db.add(second_response)
        await db.flush()
        await service.complete(
            db,
            accepted.confirmation,
            response_evidence_id=first_response.id,
            response_result="WAIT",
            completed_at=datetime(2026, 8, 16, 0, 1),
        )
        confirmation_id = accepted.confirmation.id
        second_response_id = second_response.id

    async with integration_session_factory.begin() as db:
        confirmation = await db.get(WmsConfirmation, confirmation_id, with_for_update=True)
        assert confirmation is not None
        conflict_result = await service.complete(
            db,
            confirmation,
            response_evidence_id=second_response_id,
            response_result="ACCEPT",
            completed_at=datetime(2026, 8, 16, 0, 2),
        )

    with pytest.raises(WmsConfirmationResponseConflictError):
        raise conflict_result.to_exception()
    async with integration_session_factory() as db:
        status = await db.scalar(select(WmsConfirmation.status).where(WmsConfirmation.id == confirmation_id))
    assert status == "RECONCILING"
