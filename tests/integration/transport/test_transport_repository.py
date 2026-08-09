from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from src.app.transport.models import TransportTask
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
