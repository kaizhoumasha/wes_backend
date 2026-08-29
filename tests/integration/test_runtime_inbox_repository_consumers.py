"""RuntimeInbox repository 与 WorkLine 通用消费者合同。"""

from __future__ import annotations

import pytest

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox


@pytest.mark.asyncio
async def test_runtime_inbox_repository_counts_only_requested_statuses(db_session) -> None:
    repository = RuntimeInboxRepository()
    for index, status in enumerate(("RECEIVED", "FAILED", "PROCESSED"), start=1):
        db_session.add(
            RuntimeInbox(
                provider_code="TEST",
                event_type=f"COUNT_{status}",
                source_event_id=f"count-status:{index}",
                payload_hash=f"hash-count-{index}",
                kind="INTERNAL_EVENT",
                payload_json={"index": index},
                payload_schema_version=1,
                status=status,
                claim_bucket_key=f"source:count-status:{index}",
                received_at=index,
            )
        )
    await db_session.flush()

    count = await repository.count_by_statuses(db_session, {"RECEIVED", "FAILED"})

    assert count == 2


@pytest.mark.asyncio
async def test_runtime_inbox_repository_lists_session_reference_in_fifo_order(db_session) -> None:
    repository = RuntimeInboxRepository()
    for source_event_id, session_ref, received_at in (
        ("session-ref:later", 901, 20),
        ("session-ref:other", 902, 1),
        ("session-ref:earlier", 901, 10),
    ):
        db_session.add(
            RuntimeInbox(
                provider_code="TEST",
                event_type="SESSION_REF",
                source_event_id=source_event_id,
                payload_hash=f"hash-{source_event_id}",
                kind="INTERNAL_EVENT",
                workline_session_id=session_ref,
                payload_json={"source_event_id": source_event_id},
                payload_schema_version=1,
                status="RECEIVED",
                claim_bucket_key=f"source:{source_event_id}",
                received_at=received_at,
            )
        )
    await db_session.flush()

    projections = await repository.list_by_workline_session_ref(db_session, 901)

    assert [item.source_event_id for item in projections] == ["session-ref:earlier", "session-ref:later"]
    assert all(item.workline_session_ref == 901 for item in projections)
