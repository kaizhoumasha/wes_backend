"""PostgreSQL 对核心 execution owner 约束的最终裁决。"""

from __future__ import annotations

import asyncio
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
    PositionProjection,
    WmsConfirmation,
)
from src.app.execution.repositories.material_execution_repository import MaterialExecutionRepository
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceIdentityConflictError,
    InboundEvidenceService,
)
from src.app.execution.services.position_projection_service import (
    PositionProjectionAuthorityError,
    PositionProjectionService,
)
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictError,
    WmsConfirmationResponseConflictError,
    WmsConfirmationService,
)
from src.app.transport.contracts import TransportExecutionAuthority
from src.app.wms_integration.outbound_picking.models import PickingTask as _PickingTask
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException

PREFIX = "EXECUTION-CONSTRAINT-"


async def _seed_workline(db) -> tuple[WorkLine, str]:
    identity = uuid4().hex
    workline = WorkLine(
        line_code=f"{PREFIX}LINE-{identity[:20]}",
        line_name="Execution constraints",
        line_type=LineType.AUTO,
    )
    db.add(workline)
    await db.flush()
    workline.is_active = True
    workline.position_bindings = {"HANDOFF": {"location_id": "H1", "location_type": "HANDOFF_POSITION"}}
    await db.flush()
    return workline, identity


async def _seed_creation_evidence(db, workline: WorkLine, identity: str) -> InboundEvidence:
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=f"{PREFIX}SCAN-{identity}",
        payload_digest="1" * 64,
        normalized_payload={"source_event_id": f"{PREFIX}SCAN-{identity}"},
        received_at=datetime(2026, 8, 16),
        workline_id=workline.id,
        device_code=f"{PREFIX}MEASUREMENT-{identity}",
    )
    db.add(evidence)
    await db.flush()
    return evidence


def _execution(
    workline: WorkLine,
    identity: str,
    *,
    evidence_id: int,
) -> MaterialExecution:
    return MaterialExecution(
        execution_code=f"{PREFIX}MATERIAL-{identity}",
        material_trace_id=f"{PREFIX}TRACE-{identity}",
        workline_id=workline.id,
        admission_received_at=datetime(2026, 8, 16),
        admission_evidence_id=evidence_id,
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
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like(f"{PREFIX}%"))
        await db.execute(delete(WmsConfirmation).where(WmsConfirmation.material_execution_id.in_(execution_ids)))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(delete(PositionProjection).where(PositionProjection.workline_id.in_(line_ids)))
        await db.execute(
            update(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)).values(material_execution_id=None)
        )
        await db.execute(delete(MaterialExecution).where(MaterialExecution.id.in_(execution_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))


@pytest.mark.asyncio
async def test_postgresql_allows_independent_active_executions_for_the_same_material_trace(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        first_evidence = await _seed_creation_evidence(db, workline, identity)
        second_evidence = await _seed_creation_evidence(db, workline, f"SECOND-{identity}")
        first = _execution(workline, identity, evidence_id=first_evidence.id)
        second = _execution(
            workline,
            f"SECOND-{identity}",
            evidence_id=second_evidence.id,
        )
        second.material_trace_id = first.material_trace_id
        db.add_all([first, second])
        await db.flush()


@pytest.mark.asyncio
async def test_postgresql_allows_only_one_execution_per_initial_evidence(
    integration_session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        async with integration_session_factory.begin() as db:
            workline, identity = await _seed_workline(db)
            evidence = await _seed_creation_evidence(db, workline, identity)
            first = _execution(workline, identity, evidence_id=evidence.id)
            second = _execution(workline, f"SECOND-{identity}", evidence_id=evidence.id)
            db.add_all([first, second])
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_fifo_head_keeps_retrying_earlier_material_ahead_of_later_material(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        first_evidence = await _seed_creation_evidence(db, workline, identity)
        second_evidence = await _seed_creation_evidence(db, workline, f"SECOND-{identity}")
        first = _execution(workline, identity, evidence_id=first_evidence.id)
        first.status = MaterialExecutionStatus.RECONCILING
        second = _execution(workline, f"SECOND-{identity}", evidence_id=second_evidence.id)
        second.admission_received_at = datetime(2026, 8, 16, 0, 1)
        db.add_all([first, second])
        await db.flush()

        head = await MaterialExecutionRepository().get_admission_head_for_update(
            db,
            workline_id=workline.id,
        )

        assert head is not None
        assert head.id == first.id
        assert head.status == MaterialExecutionStatus.RECONCILING


def _projection(workline_id: int, object_id: str) -> PositionProjection:
    return PositionProjection(
        object_type="BIN",
        object_id=object_id,
        workline_id=workline_id,
        position_json={"kind": "HANDOFF_POSITION", "location_code": "H1"},
        source_operation_id="019d0000-0000-7000-8000-000000000001",
        source_transport_task_id=f"{PREFIX}TRANSPORT-{object_id}",
        updated_at=datetime(2026, 8, 28),
    )


async def _apply_projection(
    service,
    db,
    line_id: int,
    object_id: str,
    *,
    unknown=False,
    transport_task_id: str | None = None,
):
    frozen_transport_task_id = transport_task_id or f"{PREFIX}TRANSPORT-{object_id}"
    return await service.apply_transport_result(
        db,
        authority=TransportExecutionAuthority(workline_id=line_id),
        object_type="BIN",
        object_id=object_id,
        position=None if unknown else {"kind": "HANDOFF_POSITION", "location_code": "H1"},
        position_unknown=unknown,
        arrival_face=None,
        operation_id="019d0000-0000-7000-8000-000000000001",
        transport_task_id=frozen_transport_task_id,
        updated_at=datetime(2026, 8, 28),
    )


@pytest.mark.asyncio
async def test_postgresql_allows_only_one_current_projection_per_bin(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        db.add(_projection(workline.id, identity))
        await db.flush()
        db.add(_projection(workline.id, identity))
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_bin_projection_uses_workline_and_transport_identity(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        projection = await _apply_projection(PositionProjectionService(), db, workline.id, identity)
        assert projection.workline_id == workline.id
        assert projection.object_id == identity
        assert projection.source_transport_task_id == f"{PREFIX}TRANSPORT-{identity}"
        assert projection.source_operation_id == "019d0000-0000-7000-8000-000000000001"


@pytest.mark.asyncio
async def test_postgresql_concurrent_cross_task_results_keep_one_unconfirmed_projection(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        first, identity = await _seed_workline(db)
        second, _ = await _seed_workline(db)
        line_ids = (first.id, second.id)

    async def apply(line_id):
        try:
            async with integration_session_factory.begin() as db:
                return await _apply_projection(
                    PositionProjectionService(),
                    db,
                    line_id,
                    identity,
                    transport_task_id=f"{PREFIX}TRANSPORT-{identity}-{line_id}",
                )
        except PositionProjectionAuthorityError as error:
            return error

    results = await asyncio.gather(*(apply(line_id) for line_id in line_ids))
    assert all(isinstance(result, PositionProjection) for result in results)
    async with integration_session_factory() as db:
        projection = await PositionProjectionRepository().get(db, "BIN", identity)
    assert projection is not None
    assert projection.workline_id in line_ids
    assert projection.position_unknown is True
    assert projection.source_transport_task_id == f"{PREFIX}TRANSPORT-{identity}-{projection.workline_id}"


@pytest.mark.asyncio
async def test_postgresql_unknown_projection_blocks_waiting_workline_deactivation(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        line_id = workline.id
    locked, release = asyncio.Event(), asyncio.Event()

    class BlockingProjectionRepository(PositionProjectionRepository):
        async def get_workline_for_update(self, db, workline_id):
            line = await super().get_workline_for_update(db, workline_id)
            locked.set()
            await release.wait()
            return line

    async def apply():
        async with integration_session_factory.begin() as db:
            await _apply_projection(
                PositionProjectionService(repository=BlockingProjectionRepository()),
                db,
                line_id,
                identity,
                unknown=True,
            )

    async def deactivate():
        async with integration_session_factory.begin() as db:
            current = await db.get(WorkLine, line_id)
            await WorkLineConfigurationService(definitions=()).deactivate(
                db, workline_id=line_id, version=current.version
            )

    applying = asyncio.create_task(apply())
    await asyncio.wait_for(locked.wait(), timeout=5)
    deactivating = asyncio.create_task(deactivate())
    done, _ = await asyncio.wait({deactivating}, timeout=0.1)
    assert done == set()
    release.set()
    await applying
    with pytest.raises(BusinessException, match="未完成运行负载"):
        await deactivating
    async with integration_session_factory() as db:
        assert (await db.get(WorkLine, line_id)).is_active
        projection = await PositionProjectionRepository().get(db, "BIN", identity)
        assert projection.position_unknown
        assert projection.source_transport_task_id == f"{PREFIX}TRANSPORT-{identity}"


@pytest.mark.asyncio
async def test_postgresql_workline_deactivation_retains_waiting_original_result(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        line_id, version = workline.id, workline.version
    locked, release = asyncio.Event(), asyncio.Event()

    class BlockingWorkLineRepository(WorkLineRepository):
        async def get_for_update(self, db, workline_id):
            line = await super().get_for_update(db, workline_id)
            locked.set()
            await release.wait()
            return line

    async def deactivate():
        async with integration_session_factory() as db:
            await WorkLineConfigurationService(
                definitions=(), workline_repository=BlockingWorkLineRepository()
            ).deactivate(db, workline_id=line_id, version=version)

    async def apply():
        async with integration_session_factory.begin() as db:
            await _apply_projection(PositionProjectionService(), db, line_id, identity)

    deactivating = asyncio.create_task(deactivate())
    await asyncio.wait_for(locked.wait(), timeout=5)
    applying = asyncio.create_task(apply())
    done, _ = await asyncio.wait({applying}, timeout=0.1)
    assert done == set()
    release.set()
    await deactivating
    await applying
    async with integration_session_factory() as db:
        assert not (await db.get(WorkLine, line_id)).is_active
        projection = await PositionProjectionRepository().get(db, "BIN", identity)
        assert projection is not None
        assert projection.source_transport_task_id == f"{PREFIX}TRANSPORT-{identity}"


@pytest.mark.asyncio
async def test_postgresql_freezes_inbound_source_and_wms_operation_identity(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        workline, identity = await _seed_workline(db)
        creation_evidence = await _seed_creation_evidence(db, workline, identity)
        execution = _execution(workline, identity, evidence_id=creation_evidence.id)
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
        workline, identity = await _seed_workline(db)
        creation_evidence = await _seed_creation_evidence(db, workline, identity)
        execution = _execution(workline, identity, evidence_id=creation_evidence.id)
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
                        "AND table_name IN ("
                        "'device_evidences', 'device_evidence_conflicts', 'inbound_evidence_execution_bindings'"
                        ")"
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
async def test_device_observation_is_closed_and_requires_device_identity(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_OBSERVATION,
                source_identity=f"{PREFIX}DEVICE-OBSERVATION-VALID",
                payload_digest="5" * 64,
                normalized_payload={"observation": "RESULT_UNKNOWN"},
                received_at=datetime(2026, 8, 16),
                device_code=f"{PREFIX}DEVICE-1",
                command_code=f"{PREFIX}COMMAND-1",
            )
        )

    async with integration_session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.DEVICE_OBSERVATION,
                source_identity=f"{PREFIX}DEVICE-OBSERVATION-MISSING-DEVICE",
                payload_digest="6" * 64,
                normalized_payload={"observation": "NOT_ACCEPTED"},
                received_at=datetime(2026, 8, 16),
                command_code=f"{PREFIX}COMMAND-2",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_transport_evidence_identity_is_required_and_isolated(integration_session_factory) -> None:  # type: ignore[no-untyped-def]
    async with integration_session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.TRANSPORT_RESULT,
                source_identity=f"{PREFIX}TRANSPORT-VALID",
                payload_digest="2" * 64,
                normalized_payload={"transport_task_id": "TRANSPORT-1", "outcome_version": 1},
                received_at=datetime(2026, 8, 16),
                transport_task_id="TRANSPORT-1",
            )
        )

    async with integration_session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.TRANSPORT_RESULT,
                source_identity=f"{PREFIX}TRANSPORT-MISSING",
                payload_digest="3" * 64,
                normalized_payload={"transport_task_id": "TRANSPORT-2", "outcome_version": 1},
                received_at=datetime(2026, 8, 16),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async with integration_session_factory.begin() as db:
        db.add(
            InboundEvidence(
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{PREFIX}TRANSPORT-MIXED",
                payload_digest="4" * 64,
                normalized_payload={"data": {}},
                received_at=datetime(2026, 8, 16),
                transport_task_id="TRANSPORT-3",
                operation="inbound.execution.recovery_decided@v1",
                operation_id="RECOVERY-1",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


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
        workline, identity = await _seed_workline(db)
        creation_evidence = await _seed_creation_evidence(db, workline, identity)
        execution = _execution(workline, identity, evidence_id=creation_evidence.id)
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
        workline, identity = await _seed_workline(db)
        creation_evidence = await _seed_creation_evidence(db, workline, identity)
        execution = _execution(workline, identity, evidence_id=creation_evidence.id)
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
