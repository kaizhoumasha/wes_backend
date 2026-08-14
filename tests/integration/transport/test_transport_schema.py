from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import BigInteger, text
from sqlalchemy.exc import IntegrityError

from src.app.transport.models import TransportEvidence, TransportResourceBinding, TransportTask
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.asyncio


async def test_transport_result_revision_columns_use_signed_64_bit_storage() -> None:
    assert isinstance(TransportTask.__table__.c.last_applied_wms_outcome_revision.type, BigInteger)
    assert isinstance(TransportEvidence.__table__.c.outcome_revision.type, BigInteger)


def _task(task_id: str, request_id: str, digest: str, now: object) -> TransportTask:
    operation_id = new_uuid7(timestamp_ms=1_723_456_789_012)
    return TransportTask(
        transport_task_id=task_id,
        client_request_id=request_id,
        payload_digest=digest,
        kind="RACK_MOVE",
        caller_json={"workline_id": "line"},
        request_json={"rack_id": "rack"},
        submit_operation_id=operation_id,
        submit_timestamp_ms=1_723_456_789_012,
        submit_payload_json={"transport_task_id": task_id, "kind": "RACK_MOVE"},
        submit_payload_digest=digest,
        created_at=now,
        updated_at=now,
    )


async def test_transport_schema_enforces_client_request_and_operation_identity(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task = _task(f"transport-{suffix}", f"request-{suffix}", "a" * 64, now)
    integration_db_session.add(task)
    await integration_db_session.flush()

    integration_db_session.add(_task(f"other-{suffix}", task.client_request_id, "b" * 64, now))
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_transport_schema_enforces_one_active_binding_per_resource(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    tasks = [
        _task(f"transport-{index}-{suffix}", f"request-{index}-{suffix}", str(index) * 64, now) for index in (1, 2)
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
    assert "(next_submit_at IS NOT NULL)" in definitions["ix_transport_tasks_submit_claim"]
    assert "next_submit_at, id)" in definitions["ix_transport_tasks_submit_claim"]
    assert "(submit_claim_until, id)" in definitions["ix_transport_tasks_ambiguous_claim"]
    assert "(updated_at, id)" in definitions["ix_transport_tasks_outcome_claim"]


async def test_transport_schema_contains_only_final_wire_identity_columns(
    integration_db_session: AsyncSession,
) -> None:
    result = await integration_db_session.execute(
        text(
            "SELECT table_name, column_name, column_default FROM information_schema.columns "
            "WHERE table_schema = 'wes_runtime' AND table_name IN "
            "('transport_tasks', 'transport_evidence', 'transport_members', 'transport_position_projections')"
        )
    )
    columns = {(row[0], row[1]): row[2] for row in result}
    assert {
        ("transport_tasks", "submit_operation_id"),
        ("transport_tasks", "submit_timestamp_ms"),
        ("transport_tasks", "submit_payload_json"),
        ("transport_tasks", "submit_payload_digest"),
        ("transport_evidence", "operation_id"),
        ("transport_evidence", "event_timestamp_ms"),
        ("transport_evidence", "ack_timestamp_ms"),
        ("transport_evidence", "ack_data_json"),
        ("transport_members", "last_operation_id"),
        ("transport_position_projections", "source_operation_id"),
    } <= columns.keys()
    assert {
        ("transport_evidence", "event_id"),
        ("transport_members", "last_event_id"),
        ("transport_position_projections", "source_event_id"),
    }.isdisjoint(columns)
    assert columns[("transport_tasks", "last_applied_wms_outcome_revision")] is None


async def test_transport_evidence_identity_is_operation_and_operation_id(integration_db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task = _task(f"transport-{suffix}", f"request-{suffix}", "c" * 64, now)
    integration_db_session.add(task)
    await integration_db_session.flush()
    operation_id = new_uuid7()
    for operation in ("transport.task.resulted@v1", "transport.task.member_position_changed@v1"):
        integration_db_session.add(
            TransportEvidence(
                operation_id=operation_id,
                transport_task_id=task.transport_task_id,
                operation=operation,
                outcome_revision=1 if operation == "transport.task.resulted@v1" else None,
                event_timestamp_ms=1_723_456_789_011,
                payload_digest="d" * 64,
                payload_json={"status": "SUCCEEDED"},
                ack_timestamp_ms=1_723_456_789_012,
                ack_data_json={"transport_task_id": task.transport_task_id},
                received_at=now,
            )
        )
        await integration_db_session.flush()
    integration_db_session.add(
        TransportEvidence(
            operation_id=operation_id,
            transport_task_id=task.transport_task_id,
            operation="transport.task.resulted@v1",
            outcome_revision=2,
            event_timestamp_ms=1_723_456_789_012,
            payload_digest="e" * 64,
            payload_json={"status": "FAILED"},
            ack_timestamp_ms=1_723_456_789_013,
            ack_data_json={"transport_task_id": task.transport_task_id},
            received_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_transport_result_revision_is_unique_per_task(integration_db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex
    now = timezone.now_for_db()
    task = _task(f"transport-revision-{suffix}", f"request-revision-{suffix}", "f" * 64, now)
    integration_db_session.add(task)
    await integration_db_session.flush()

    def evidence(operation_id: str) -> TransportEvidence:
        return TransportEvidence(
            operation_id=operation_id,
            transport_task_id=task.transport_task_id,
            operation="transport.task.resulted@v1",
            outcome_revision=1,
            event_timestamp_ms=1_723_456_789_011,
            payload_digest="a" * 64,
            payload_json={"outcome_revision": 1},
            ack_timestamp_ms=1_723_456_789_012,
            ack_data_json={"transport_task_id": task.transport_task_id},
            received_at=now,
        )

    integration_db_session.add(evidence(new_uuid7()))
    await integration_db_session.flush()
    integration_db_session.add(evidence(new_uuid7()))
    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()
