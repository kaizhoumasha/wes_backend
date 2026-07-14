"""RuntimeInbox repository/UoW 消费者迁移合同。"""

from __future__ import annotations

import pytest

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.workline.repositories.workline_repository import WorkLineRepository


@pytest.mark.asyncio
async def test_smt_source_pick_evidence_reads_runtime_inbox(db_session) -> None:
    """source_pick_inbox_id 与 producer 一致，必须按 RuntimeInbox.id 读取证据。"""

    inbox = RuntimeInbox(
        provider_code="WORKLINE_INTERNAL",
        event_type="SORTING_SOURCE_PICK_REQUESTED",
        source_event_id="source-pick:11:22:3",
        payload_hash="hash-source-pick",
        kind="INTERNAL_EVENT",
        workline_id=44,
        event_id="evt-source-pick",
        payload_json={"event_type": "SORTING_SOURCE_PICK_REQUESTED", "data": {"source_item_id": 22}},
        payload_schema_version=1,
        status="FAILED",
        claim_bucket_key="source:source-pick:11:22:3",
        received_at=1_783_733_000_000,
        attempt_count=2,
        max_retries=5,
        next_retry_at=1_783_733_400_000,
        failed_at=1_783_733_000_000,
        last_error_code="RESOURCE_WAIT",
        last_error_message="等待上游资源",
    )
    db_session.add(inbox)
    await db_session.flush()

    loaded = await SmtInboundHandoffRepository(runtime_inbox_query=RuntimeInboxRepository()).get_runtime_inbox_by_id(
        db_session, inbox.id
    )

    assert type(loaded).__name__ == "RuntimeInboxEvidence"
    assert loaded.id == inbox.id
    assert loaded.last_error_message == "等待上游资源"

    evidence = await SmtInboundHandoffService()._source_pick_inbox_evidence(db_session, inbox.id)
    assert evidence == {
        "id": inbox.id,
        "status": "FAILED",
        "event_id": "evt-source-pick",
        "attempt_count": 2,
        "max_retries": 5,
        "next_retry_at": 1_783_733_400_000,
        "processed_at": None,
        "failed_at": 1_783_733_000_000,
        "last_error_code": "RESOURCE_WAIT",
        "last_error_message": "等待上游资源",
    }


@pytest.mark.parametrize(
    ("status", "expected_count"),
    [
        ("RECEIVED", 1),
        ("PROCESSING", 1),
        ("FAILED", 1),
        ("PROCESSED", 0),
        ("DEAD_LETTER", 0),
    ],
)
@pytest.mark.asyncio
async def test_unfinished_workload_counts_runtime_inbox_five_state_contract(
    db_session,
    status: str,
    expected_count: int,
) -> None:
    """RECEIVED/PROCESSING/FAILED 属于 backlog，两个终态不阻止 WorkLine 停用。"""

    db_session.add(
        RuntimeInbox(
            provider_code="TEST",
            event_type=f"STATE_{status}",
            source_event_id=f"state:{status}",
            payload_hash=f"hash-{status}",
            kind="INTERNAL_EVENT",
            workline_id=710,
            event_id=f"evt-{status}",
            payload_json={"status": status},
            payload_schema_version=1,
            status=status,
            claim_bucket_key=f"source:state:{status}",
            received_at=1,
            # FAILED 尚未到重试时间也仍是未完成负载。
            next_retry_at=1_999_999_999_999 if status == "FAILED" else None,
        )
    )
    # 显式列 workline_id 是唯一归属依据；payload 中相同数字不得被猜测为关联关系。
    db_session.add(
        RuntimeInbox(
            provider_code="TEST",
            event_type="UNRELATED",
            source_event_id="state:unrelated",
            payload_hash="hash-unrelated",
            kind="INTERNAL_EVENT",
            workline_id=711,
            payload_json={"workline_id": 710},
            payload_schema_version=1,
            status="RECEIVED",
            claim_bucket_key="source:state:unrelated",
            received_at=2,
        )
    )
    await db_session.flush()

    summary = await WorkLineRepository(runtime_inbox_query=RuntimeInboxRepository()).get_unfinished_workload_summary(
        db_session, 710
    )

    assert summary["by_type"]["inboxes"] == expected_count
    assert summary["count"] == expected_count
    if expected_count:
        assert summary["sample"]["type"] == "inbox"
        assert summary["sample"]["status"] == status
    else:
        assert summary["sample"] is None


@pytest.mark.asyncio
async def test_runtime_inbox_repository_can_read_source_pick_evidence_by_id(db_session) -> None:
    """RuntimeInbox repository 的显式 ID 列提供 source-pick evidence 读取。"""

    inbox = RuntimeInbox(
        provider_code="WORKLINE_INTERNAL",
        event_type="SORTING_SOURCE_PICK_REQUESTED",
        source_event_id="source-pick:direct",
        payload_hash="hash-direct",
        kind="INTERNAL_EVENT",
        payload_json={"event_type": "SORTING_SOURCE_PICK_REQUESTED"},
        payload_schema_version=1,
        claim_bucket_key="source:source-pick:direct",
        received_at=1,
    )
    db_session.add(inbox)
    await db_session.flush()

    loaded = await RuntimeInboxRepository().get_by_id(db_session, inbox.id)

    assert loaded is inbox


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
