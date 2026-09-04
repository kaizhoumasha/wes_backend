from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus
from src.app.execution.models.inbound_evidence import InboundEvidenceConflict
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler
from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_ISSUED_OPERATION,
    parse_picking_task_issued_event,
)
from src.app.wms_adapter.v1.events import router as wms_event_router
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.services import PickingTaskIssuedService
from src.core.uuid7 import new_uuid7

pytest_plugins = ("tests.integration.conftest",)


def _dispatch_sequence() -> int:
    return int(new_uuid7().replace("-", "")[-12:], 16) + 1


def _event(
    operation_id: str,
    *,
    task_id: str,
    dispatch_sequence: int,
    task_type: str = "MANUAL",
    not_before: int | None = None,
):
    data = {
        "task_id": task_id,
        "task_type": task_type,
        "queue_revision": 1,
        "dispatch_sequence": dispatch_sequence,
    }
    if not_before is not None:
        data["not_before"] = not_before
    return parse_picking_task_issued_event(
        {
            "operation_id": operation_id,
            "operation": PICKING_TASK_ISSUED_OPERATION,
            "timestamp": 1786060800000,
            "data": data,
        }
    )


@pytest.mark.asyncio
async def test_picking_task_issued_is_persisted_idempotently_and_conflicts_fail_closed(
    integration_session_factory,
) -> None:
    task_id = f"PICK-{new_uuid7()}"
    operation_id = new_uuid7()
    conflicting_operation_id = new_uuid7()
    identity = f"{PICKING_TASK_ISSUED_OPERATION}:{operation_id}"
    conflict_identity = f"{PICKING_TASK_ISSUED_OPERATION}:{conflicting_operation_id}"
    identities = {
        identity,
        conflict_identity,
    }
    dispatch_sequence = _dispatch_sequence()
    not_before = 1786060900000
    service = PickingTaskIssuedService(integration_session_factory)

    try:
        event = _event(
            operation_id,
            task_id=task_id,
            dispatch_sequence=dispatch_sequence,
            not_before=not_before,
        )
        first = await service.record(event, received_at=datetime(2026, 9, 3, 10))
        replay = await service.record(event, received_at=datetime(2026, 9, 3, 11))
        idempotency_conflict = await service.record(
            _event(operation_id, task_id=task_id, dispatch_sequence=dispatch_sequence + 1),
            received_at=datetime(2026, 9, 3, 12),
        )
        idempotency_conflict_replay = await service.record(
            _event(operation_id, task_id=task_id, dispatch_sequence=dispatch_sequence + 1),
            received_at=datetime(2026, 9, 3, 12, 30),
        )
        state_conflict = await service.record(
            _event(conflicting_operation_id, task_id=task_id, dispatch_sequence=dispatch_sequence),
            received_at=datetime(2026, 9, 3, 13),
        )
        state_conflict_replay = await service.record(
            _event(conflicting_operation_id, task_id=task_id, dispatch_sequence=dispatch_sequence),
            received_at=datetime(2026, 9, 3, 14),
        )

        assert (first.code, replay.code) == ("RECEIVED", "DUPLICATE")
        assert first.timestamp_ms == replay.timestamp_ms
        assert first.timestamp_ms == 1788429600000
        assert (idempotency_conflict.code, idempotency_conflict.reason_code) == (
            "CONFLICT",
            "IDEMPOTENCY_CONFLICT",
        )
        assert idempotency_conflict_replay == idempotency_conflict
        assert (state_conflict.code, state_conflict.reason_code) == ("CONFLICT", "STATE_CONFLICT")
        assert state_conflict_replay == state_conflict

        async with integration_session_factory() as db:
            task = await db.scalar(select(PickingTask).where(PickingTask.task_id == task_id))
            task_count = await db.scalar(
                select(func.count()).select_from(PickingTask).where(PickingTask.task_id == task_id)
            )
            issued_evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.source_identity == identity)
            )
            conflict_evidence = await db.scalar(
                select(InboundEvidence).where(InboundEvidence.source_identity == conflict_identity)
            )
        assert task is not None
        assert task.status == PickingTaskStatus.QUEUED
        assert task.task_type == PickingTaskType.MANUAL
        assert task.queue_revision == 1
        assert task.dispatch_sequence == dispatch_sequence
        assert task.not_before_ms == not_before
        assert task.issued_at_ms == 1786060800000
        assert task_count == 1
        assert issued_evidence is not None
        assert task.issued_evidence_id == issued_evidence.id
        assert issued_evidence.processed_at == datetime(2026, 9, 3, 10)
        assert conflict_evidence is not None
        assert conflict_evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(
                delete(InboundEvidenceConflict).where(InboundEvidenceConflict.source_identity.in_(identities))
            )
            await db.execute(delete(PickingTask).where(PickingTask.task_id == task_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.in_(identities)))


@pytest.mark.asyncio
async def test_concurrent_picking_task_issued_replay_creates_one_task(
    integration_session_factory,
) -> None:
    task_id = f"PICK-{new_uuid7()}"
    operation_id = new_uuid7()
    identity = f"{PICKING_TASK_ISSUED_OPERATION}:{operation_id}"
    dispatch_sequence = _dispatch_sequence()
    services = [PickingTaskIssuedService(integration_session_factory) for _ in range(2)]

    try:
        results = await asyncio.gather(
            *(
                service.record(
                    _event(operation_id, task_id=task_id, dispatch_sequence=dispatch_sequence),
                    received_at=datetime(2026, 9, 3, 10),
                )
                for service in services
            )
        )

        assert {result.code for result in results} == {"RECEIVED", "DUPLICATE"}
        assert len({result.timestamp_ms for result in results}) == 1
        async with integration_session_factory() as db:
            task_count = await db.scalar(
                select(func.count()).select_from(PickingTask).where(PickingTask.task_id == task_id)
            )
        assert task_count == 1
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(PickingTask).where(PickingTask.task_id == task_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity == identity))


@pytest.mark.asyncio
async def test_concurrent_tasks_cannot_claim_the_same_dispatch_sequence(
    integration_session_factory,
) -> None:
    task_ids = (f"PICK-{new_uuid7()}", f"PICK-{new_uuid7()}")
    operation_ids = (new_uuid7(), new_uuid7())
    task_types = ("MANUAL", "AUTO")
    identities = tuple(f"{PICKING_TASK_ISSUED_OPERATION}:{value}" for value in operation_ids)
    dispatch_sequence = _dispatch_sequence()
    services = [PickingTaskIssuedService(integration_session_factory) for _ in range(2)]

    try:
        results = await asyncio.gather(
            *(
                service.record(
                    _event(
                        operation_id,
                        task_id=task_id,
                        task_type=task_type,
                        dispatch_sequence=dispatch_sequence,
                    ),
                    received_at=datetime(2026, 9, 3, 10),
                )
                for service, operation_id, task_id, task_type in zip(
                    services,
                    operation_ids,
                    task_ids,
                    task_types,
                    strict=True,
                )
            )
        )

        assert sorted((result.code, result.reason_code) for result in results) == [
            ("CONFLICT", "STATE_CONFLICT"),
            ("RECEIVED", None),
        ]
        async with integration_session_factory() as db:
            task_count = await db.scalar(
                select(func.count()).select_from(PickingTask).where(PickingTask.task_id.in_(task_ids))
            )
        assert task_count == 1
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(PickingTask).where(PickingTask.task_id.in_(task_ids)))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.in_(identities)))


@pytest.mark.asyncio
async def test_manual_and_auto_tasks_are_persisted_in_the_same_queue_entity(
    integration_session_factory,
) -> None:
    task_ids = (f"PICK-{new_uuid7()}", f"PICK-{new_uuid7()}")
    operation_ids = (new_uuid7(), new_uuid7())
    identities = tuple(f"{PICKING_TASK_ISSUED_OPERATION}:{value}" for value in operation_ids)
    dispatch_sequences = (_dispatch_sequence(), _dispatch_sequence())
    if dispatch_sequences[0] == dispatch_sequences[1]:
        dispatch_sequences = (dispatch_sequences[0], dispatch_sequences[1] + 1)
    service = PickingTaskIssuedService(integration_session_factory)

    try:
        results = [
            await service.record(
                _event(
                    operation_id,
                    task_id=task_id,
                    task_type=task_type,
                    dispatch_sequence=dispatch_sequence,
                ),
                received_at=datetime(2026, 9, 3, 10),
            )
            for operation_id, task_id, task_type, dispatch_sequence in zip(
                operation_ids,
                task_ids,
                ("MANUAL", "AUTO"),
                dispatch_sequences,
                strict=True,
            )
        ]

        assert [result.code for result in results] == ["RECEIVED", "RECEIVED"]
        async with integration_session_factory() as db:
            tasks = list(
                (
                    await db.scalars(
                        select(PickingTask).where(PickingTask.task_id.in_(task_ids)).order_by(PickingTask.task_type)
                    )
                ).all()
            )
        assert {task.task_type for task in tasks} == {PickingTaskType.MANUAL, PickingTaskType.AUTO}
        assert {task.task_id for task in tasks} == set(task_ids)
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(PickingTask).where(PickingTask.task_id.in_(task_ids)))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity.in_(identities)))


@pytest.mark.asyncio
async def test_wms_event_http_route_persists_picking_task_before_received_ack(
    integration_session_factory,
) -> None:
    task_id = f"PICK-{new_uuid7()}"
    operation_id = new_uuid7()
    identity = f"{PICKING_TASK_ISSUED_OPERATION}:{operation_id}"
    dispatch_sequence = _dispatch_sequence()
    handler = PickingTaskIssuedHandler(PickingTaskIssuedService(integration_session_factory))
    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
    app.state.wms_picking_task_issued_handler = handler
    app.state.transport_runtime = None
    app.state.wms_recovery_event_handler = None
    app.state.wms_event_stream_service = SimpleNamespace(publish_to=AsyncMock(return_value=True))
    app.include_router(wms_event_router, prefix="/api/v1/wms")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/wms/events",
                json=_event(
                    operation_id,
                    task_id=task_id,
                    dispatch_sequence=dispatch_sequence,
                ).model_dump(mode="json", exclude_none=True),
            )

        assert response.status_code == 202
        assert response.json()["code"] == "RECEIVED"
        async with integration_session_factory() as db:
            task = await db.scalar(select(PickingTask).where(PickingTask.task_id == task_id))
        assert task is not None
        assert task.issued_evidence_id is not None
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(PickingTask).where(PickingTask.task_id == task_id))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.source_identity == identity))
