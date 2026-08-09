from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.app.transport.models import TransportEvidence, TransportResourceBinding, TransportTask
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio


async def test_transport_schema_enforces_client_request_and_event_identity(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task = TransportTask(
        transport_task_id=f"transport-{suffix}",
        client_request_id=f"request-{suffix}",
        payload_digest="a" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "line"},
        request_json={"rack_id": "rack"},
        created_at=now,
        updated_at=now,
    )
    integration_db_session.add(task)
    await integration_db_session.flush()

    integration_db_session.add(
        TransportTask(
            transport_task_id=f"other-{suffix}",
            client_request_id=task.client_request_id,
            payload_digest="b" * 64,
            kind="RACK_MOVE",
            caller_json={"workline_id": "line"},
            request_json={"rack_id": "other"},
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_transport_schema_enforces_one_active_binding_per_resource(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    tasks = [
        TransportTask(
            transport_task_id=f"transport-{index}-{suffix}",
            client_request_id=f"request-{index}-{suffix}",
            payload_digest=str(index) * 64,
            kind="RACK_MOVE",
            caller_json={"workline_id": "line"},
            request_json={"rack_id": "rack"},
            created_at=now,
            updated_at=now,
        )
        for index in (1, 2)
    ]
    integration_db_session.add_all(tasks)
    await integration_db_session.flush()
    integration_db_session.add(
        TransportResourceBinding(
            transport_task_id=tasks[0].transport_task_id,
            resource_type="RACK",
            resource_id=f"rack-{suffix}",
            created_at=now,
        )
    )
    await integration_db_session.flush()
    integration_db_session.add(
        TransportResourceBinding(
            transport_task_id=tasks[1].transport_task_id,
            resource_type="RACK",
            resource_id=f"rack-{suffix}",
            created_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_transport_schema_contains_required_claim_indexes(integration_db_session: AsyncSession) -> None:
    result = await integration_db_session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'wes_runtime' AND tablename LIKE 'transport_%'"
        )
    )
    definitions = {row[0]: row[1] for row in result}
    assert {
        "ix_transport_tasks_submit_claim",
        "ix_transport_tasks_ambiguous_claim",
        "ix_transport_evidence_pending_claim",
        "ix_transport_tasks_outcome_claim",
        "ux_transport_resource_bindings_active",
    } <= definitions.keys()
    assert "(submit_claim_until, id)" in definitions["ix_transport_tasks_ambiguous_claim"]
    assert "(updated_at, id)" in definitions["ix_transport_tasks_outcome_claim"]


async def test_transport_evidence_event_id_is_unique(integration_db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task = TransportTask(
        transport_task_id=f"transport-{suffix}",
        client_request_id=f"request-{suffix}",
        payload_digest="c" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "line"},
        request_json={"rack_id": "rack"},
        created_at=now,
        updated_at=now,
    )
    integration_db_session.add(task)
    await integration_db_session.flush()
    for _ in range(2):
        integration_db_session.add(
            TransportEvidence(
                event_id=f"event-{suffix}",
                transport_task_id=task.transport_task_id,
                operation="transport.task.result@v1",
                payload_digest="d" * 64,
                payload_json={"status": "SUCCEEDED"},
                received_at=now,
            )
        )
        if _ == 0:
            await integration_db_session.flush()
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()
