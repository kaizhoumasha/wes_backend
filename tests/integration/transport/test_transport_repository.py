from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from src.app.transport.models import TransportEvidence, TransportTask
from src.app.transport.repository import TransportRepository
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio


def _task(task_id: str, request_id: str, digest: str, now: object) -> TransportTask:
    operation_id = new_uuid7(timestamp_ms=1_723_456_789_012)
    return TransportTask(
        transport_task_id=task_id,
        client_request_id=request_id,
        request_digest=digest,
        kind="RACK_MOVE",
        caller_json={"workline_id": "line"},
        request_json={"rack_id": "rack"},
        submit_operation_id=operation_id,
        submit_timestamp_ms=1_723_456_789_012,
        submit_request_body='{"data":{}}',
        submit_request_body_digest=digest,
        created_at=now,
        updated_at=now,
    )


async def test_two_workers_do_not_claim_same_pending_task(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task_id = f"transport-{suffix}"
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(_task(task_id, f"request-{suffix}", "a" * 64, now))
    repository = TransportRepository()

    try:
        async with integration_session_factory.begin() as first_db:
            first = await repository.claim_next_pending_task(
                first_db,
                token="worker-1",
                now=now,
                claim_until=now + timedelta(seconds=30),
            )
            async with integration_session_factory.begin() as second_db:
                second = await repository.claim_next_pending_task(
                    second_db,
                    token="worker-2",
                    now=now,
                    claim_until=now + timedelta(seconds=30),
                )

        assert first is not None and first.transport_task_id == task_id
        assert second is None
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            task = await repository.get_task(cleanup_db, task_id, for_update=True)
            if task is not None:
                await cleanup_db.delete(task)


async def test_expired_pending_task_claim_is_recovered_by_a_new_worker(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task_id = f"transport-expired-{suffix}"
    repository = TransportRepository()
    async with integration_session_factory.begin() as setup_db:
        task = _task(task_id, f"request-expired-{suffix}", "b" * 64, now)
        task.submit_claim_token = "expired-worker"
        task.submit_claim_until = now - timedelta(seconds=1)
        setup_db.add(task)

    try:
        async with integration_session_factory.begin() as second_db:
            second = await repository.claim_next_pending_task(
                second_db,
                token="replacement-worker",
                now=now,
                claim_until=now + timedelta(seconds=30),
            )

        assert second is not None and second.transport_task_id == task_id
        assert second.submit_claim_token == "replacement-worker"
        assert second.send_started_at == now
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            task = await repository.get_task(cleanup_db, task_id, for_update=True)
            if task is not None:
                await cleanup_db.delete(task)


async def test_two_evidence_workers_are_fenced_and_expired_claim_is_recovered(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    repository = TransportRepository()
    operation_id = new_uuid7()
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(
            TransportEvidence(
                operation_id=operation_id,
                transport_task_id=f"missing-{suffix}",
                operation="transport.task.resulted@v1",
                outcome_revision=1,
                event_timestamp_ms=1_723_456_789_011,
                message_digest="c" * 64,
                payload_json={"transport_task_id": f"missing-{suffix}"},
                ack_timestamp_ms=1_723_456_789_012,
                ack_data_json={"transport_task_id": f"missing-{suffix}"},
                received_at=now,
            )
        )

    try:
        async with integration_session_factory.begin() as first_db:
            first = await repository.claim_pending_evidence(
                first_db,
                limit=1,
                token="evidence-worker-1",
                now=now,
                claim_until=now + timedelta(seconds=30),
            )
            async with integration_session_factory.begin() as concurrent_db:
                concurrent = await repository.claim_pending_evidence(
                    concurrent_db,
                    limit=1,
                    token="evidence-worker-2",
                    now=now,
                    claim_until=now + timedelta(seconds=30),
                )
        async with integration_session_factory.begin() as recovered_db:
            recovered = await repository.claim_pending_evidence(
                recovered_db,
                limit=1,
                token="evidence-worker-3",
                now=now + timedelta(seconds=31),
                claim_until=now + timedelta(seconds=61),
            )

        assert [item.operation_id for item in first] == [operation_id]
        assert concurrent == []
        assert [item.operation_id for item in recovered] == [operation_id]
        assert recovered[0].claim_token == "evidence-worker-3"
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            evidence = await repository.get_evidence_by_operation_id(
                cleanup_db,
                "transport.task.resulted@v1",
                operation_id,
                for_update=True,
            )
            if evidence is not None:
                await cleanup_db.delete(evidence)


async def test_latest_evidence_uses_received_at_then_id_and_returns_postgres_naive_datetime(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    task_id = f"transport-latest-{suffix}"
    received_at = timezone.now_for_db()
    repository = TransportRepository()
    first_operation_id = new_uuid7()
    second_operation_id = new_uuid7()
    async with integration_session_factory.begin() as setup_db:
        for operation_id in (first_operation_id, second_operation_id):
            setup_db.add(
                TransportEvidence(
                    operation_id=operation_id,
                    transport_task_id=task_id,
                    operation="transport.task.member_position_changed@v1",
                    outcome_revision=None,
                    event_timestamp_ms=1_723_456_789_011,
                    message_digest=uuid.uuid4().hex * 2,
                    payload_json={"transport_task_id": task_id},
                    ack_timestamp_ms=1_723_456_789_012,
                    ack_data_json={"transport_task_id": task_id},
                    received_at=received_at,
                )
            )
            await setup_db.flush()

    try:
        async with integration_session_factory() as db:
            latest = await repository.get_latest_evidence(db, task_id)

        assert latest is not None
        assert latest.operation_id == second_operation_id
        assert latest.received_at.tzinfo is None
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            for operation_id in (first_operation_id, second_operation_id):
                evidence = await repository.get_evidence_by_operation_id(
                    cleanup_db,
                    "transport.task.member_position_changed@v1",
                    operation_id,
                    for_update=True,
                )
                if evidence is not None:
                    await cleanup_db.delete(evidence)
