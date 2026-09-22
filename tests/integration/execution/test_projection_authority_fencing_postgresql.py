"""PostgreSQL evidence for taskless drain projection authority fencing."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from src.app.execution.models import InboundEvidence, InboundEvidenceKind, PositionProjection
from src.app.execution.models.transport_decision_binding import TransportDecisionBinding
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.execution.services.position_projection_service import PositionProjectionService
from src.app.transport.contracts import TransportExecutionAuthority
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories import WorkLineRepository

PREFIX = "PAF-"


@pytest_asyncio.fixture(autouse=True)
async def projection_authority_rows(integration_session_factory):
    created: dict[str, list[int]] = {"line_ids": [], "evidence_ids": []}
    yield created
    async with integration_session_factory.begin() as db:
        await db.execute(
            delete(TransportDecisionBinding).where(TransportDecisionBinding.workline_id.in_(created["line_ids"]))
        )
        await db.execute(delete(PositionProjection).where(PositionProjection.workline_id.in_(created["line_ids"])))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(created["evidence_ids"])))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(created["line_ids"])))


async def _seed_taskless_drain_binding(db, created: dict[str, list[int]]) -> tuple[int, str, str, str]:
    identity = uuid4().hex
    rack_id = f"{PREFIX}RACK-{identity}"
    client_request_id = f"{PREFIX}REQUEST-{identity}"
    operation_id = str(uuid4())
    line = WorkLine(
        line_code=f"{PREFIX}LINE-{identity[:20]}",
        line_name="Projection authority fence",
        line_type=LineType.AUTO,
        is_active=True,
    )
    db.add(line)
    await db.flush()
    created["line_ids"].append(line.id)
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{PREFIX}EVIDENCE-{identity}",
        payload_digest="a" * 64,
        normalized_payload={"operation_id": operation_id},
        received_at=datetime(2026, 9, 22),
        workline_id=line.id,
        operation="outbound.return_buffer.drain_rack.decide@v1",
        operation_id=operation_id,
    )
    db.add(evidence)
    await db.flush()
    created["evidence_ids"].append(evidence.id)
    binding = TransportDecisionBinding(
        correlation_id=f"drain:{operation_id}:rack:{rack_id}",
        step="DRAIN_RACK_OUT",
        workline_id=line.id,
        picking_task_id=None,
        resource_fence_id=rack_id,
        client_request_id=client_request_id,
        source_evidence_id=evidence.id,
    )
    db.add(binding)
    await db.flush()
    assert binding.picking_task_id is None
    return line.id, rack_id, client_request_id, operation_id


async def _apply_taskless_drain_result(
    db,
    *,
    line_id: int,
    rack_id: str,
    client_request_id: str,
    operation_id: str,
) -> PositionProjection:
    projection = await PositionProjectionService().apply_transport_result(
        db,
        authority=TransportExecutionAuthority(workline_id=line_id),
        object_type="RACK",
        object_id=rack_id,
        position={"kind": "RACK_POSITION", "location_code": "RETURN_BUFFER"},
        position_unknown=False,
        arrival_face="A",
        client_request_id=client_request_id,
        operation_id=operation_id,
        transport_task_id=f"{PREFIX}TRANSPORT-{rack_id}",
        updated_at=datetime(2026, 9, 22, 0, 1),
    )
    assert projection is not None
    return projection


@pytest.mark.asyncio
async def test_taskless_drain_projection_mutation_waits_for_workline_authority_root(
    integration_session_factory,
    projection_authority_rows,
) -> None:
    async with integration_session_factory.begin() as db:
        line_id, rack_id, client_request_id, operation_id = await _seed_taskless_drain_binding(
            db, projection_authority_rows
        )

    authority_transition = integration_session_factory()
    await authority_transition.begin()
    mutation = None
    try:
        locked_line = await WorkLineRepository().get_for_authority_update(authority_transition, line_id)
        assert locked_line is not None

        async def apply() -> PositionProjection:
            async with integration_session_factory.begin() as db:
                return await _apply_taskless_drain_result(
                    db,
                    line_id=line_id,
                    rack_id=rack_id,
                    client_request_id=client_request_id,
                    operation_id=operation_id,
                )

        mutation = asyncio.create_task(apply())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(mutation), timeout=0.1)

        await authority_transition.rollback()
        projection = await asyncio.wait_for(mutation, timeout=2)
        assert projection.object_id == rack_id
        assert projection.workline_id == line_id
    finally:
        await authority_transition.rollback()
        await authority_transition.close()
        if mutation is not None and not mutation.done():
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_taskless_drain_missing_projection_insert_converges_to_one_row(
    integration_session_factory,
    projection_authority_rows,
) -> None:
    async with integration_session_factory.begin() as db:
        line_id, rack_id, client_request_id, operation_id = await _seed_taskless_drain_binding(
            db, projection_authority_rows
        )

    async def apply() -> int:
        async with integration_session_factory.begin() as db:
            projection = await _apply_taskless_drain_result(
                db,
                line_id=line_id,
                rack_id=rack_id,
                client_request_id=client_request_id,
                operation_id=operation_id,
            )
            return projection.id

    projection_ids = await asyncio.wait_for(asyncio.gather(apply(), apply()), timeout=5)

    async with integration_session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PositionProjection)
            .where(
                PositionProjection.object_type == "RACK",
                PositionProjection.object_id == rack_id,
            )
        )
        projection = await PositionProjectionRepository().get(db, "RACK", rack_id)

    assert count == 1
    assert projection_ids[0] == projection_ids[1]
    assert projection is not None
    assert projection.source_operation_id == operation_id
    assert projection.source_effect_phase == "FINAL_RESULT"
