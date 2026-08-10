from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from src.app.transport.models import TransportEvidence, TransportTask
from src.app.transport.repository import TransportRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.asyncio


async def test_two_workers_do_not_claim_same_pending_task(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task_id = f"transport-{suffix}"
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(
            TransportTask(
                transport_task_id=task_id,
                client_request_id=f"request-{suffix}",
                payload_digest="a" * 64,
                kind="RACK_MOVE",
                caller_json={"workline_id": "line"},
                request_json={"rack_id": "rack"},
                created_at=now,
                updated_at=now,
            )
        )
    repository = TransportRepository()

    try:
        async with integration_session_factory.begin() as first_db:
            first = await repository.claim_pending_tasks(
                first_db,
                limit=1,
                token="worker-1",
                now=now,
                claim_until=now + timedelta(seconds=30),
            )
            async with integration_session_factory.begin() as second_db:
                second = await repository.claim_pending_tasks(
                    second_db,
                    limit=1,
                    token="worker-2",
                    now=now,
                    claim_until=now + timedelta(seconds=30),
                )

        assert [task.transport_task_id for task in first] == [task_id]
        assert second == []
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
        setup_db.add(
            TransportTask(
                transport_task_id=task_id,
                client_request_id=f"request-expired-{suffix}",
                payload_digest="b" * 64,
                kind="RACK_MOVE",
                caller_json={"workline_id": "line"},
                request_json={"rack_id": "rack"},
                created_at=now,
                updated_at=now,
            )
        )

    try:
        async with integration_session_factory.begin() as first_db:
            first = await repository.claim_pending_tasks(
                first_db,
                limit=1,
                token="expired-worker",
                now=now,
                claim_until=now + timedelta(seconds=30),
            )
        async with integration_session_factory.begin() as second_db:
            second = await repository.claim_pending_tasks(
                second_db,
                limit=1,
                token="replacement-worker",
                now=now + timedelta(seconds=31),
                claim_until=now + timedelta(seconds=61),
            )

        assert [task.transport_task_id for task in first] == [task_id]
        assert [task.transport_task_id for task in second] == [task_id]
        assert second[0].submit_claim_token == "replacement-worker"
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
    async with integration_session_factory.begin() as setup_db:
        setup_db.add(
            TransportEvidence(
                event_id=f"event-{suffix}",
                transport_task_id=f"missing-{suffix}",
                operation="transport.task.resulted@v1",
                payload_digest="c" * 64,
                payload_json={"event_id": f"event-{suffix}"},
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

        assert [item.event_id for item in first] == [f"event-{suffix}"]
        assert concurrent == []
        assert [item.event_id for item in recovered] == [f"event-{suffix}"]
        assert recovered[0].claim_token == "evidence-worker-3"
    finally:
        async with integration_session_factory.begin() as cleanup_db:
            evidence = await repository.get_evidence_by_event_id(
                cleanup_db,
                "transport.task.resulted@v1",
                f"event-{suffix}",
                for_update=True,
            )
            if evidence is not None:
                await cleanup_db.delete(evidence)
