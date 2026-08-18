"""RuntimeInbox 内部事件、定时超时、幂等身份与提交边界合同。

设备结果与设备事件由 InboundEvidence 独占；本文件只验证通用 RuntimeInbox producer。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

# 这些模型 import 用于注册隔离 SQLite create_all 所需的跨表 FK metadata。
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

NOW_MS = 1_700_000_000_000

runtime_inbox_service_module = importlib.import_module(
    "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service"
)
RuntimeInboxService = runtime_inbox_service_module.RuntimeInboxService
RuntimeInboxCorrelationUnavailable = runtime_inbox_service_module.RuntimeInboxCorrelationUnavailable
RuntimeInboxConflict = runtime_inbox_service_module.RuntimeInboxConflict


def test_claim_bucket_separates_workline_and_execution_session_namespaces() -> None:
    common = {
        "provider_code": "TEST",
        "event_type": "INTERNAL_EVENT",
        "source_event_id": "evt-1",
    }

    workline_key = runtime_inbox_service_module._runtime_claim_bucket_key(session_id=41, **common)
    execution_key = runtime_inbox_service_module._runtime_claim_bucket_key(execution_session_id=41, **common)
    both_key = runtime_inbox_service_module._runtime_claim_bucket_key(
        session_id=41,
        execution_session_id=41,
        **common,
    )

    assert workline_key == "workline-session:41"
    assert execution_key == "execution-session:41"
    assert workline_key != execution_key
    assert both_key == workline_key


# ============================================================
# accept_internal_event
# ============================================================


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_missing_upstream_event_id(db_session) -> None:
    """内部事件也必须由 producer 提供持久 occurrence identity。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_id"):
        await service.accept_internal_event(
            db_session,
            event_type="SOURCE_PICK_REQUESTED",
            payload_json={"handoff_demand_id": 1},
        )


@pytest.mark.asyncio
async def test_same_payload_without_occurrence_identity_is_rejected_instead_of_acked(db_session) -> None:
    """两次同内容可能是两次发生；缺 identity 时均拒绝，不能错误 ACK。"""

    service = RuntimeInboxService()
    for _occurrence in range(2):
        with pytest.raises(ValueError, match="event_id"):
            await service.accept_internal_event(
                db_session,
                event_type="SAME_CONTENT",
                payload_json={"value": "same"},
            )

    assert await db_session.scalar(select(func.count()).select_from(RuntimeInbox)) == 0


@pytest.mark.asyncio
async def test_persistent_event_id_strips_whitespace_and_accepts_120_char_boundary(db_session) -> None:
    """真实 occurrence identity 只做 strip；120 字符边界不得截断。"""

    expected_event_id = "e" * 120
    result = await RuntimeInboxService().accept_internal_event(
        db_session,
        event_type="IDENTITY_BOUNDARY",
        payload_json={"producer": "internal"},
        event_id=f"  {expected_event_id}  ",
    )

    assert result.record.source_event_id == expected_event_id
    assert result.record.event_id == expected_event_id
    assert len(result.record.event_id) == 120


@pytest.mark.asyncio
async def test_persistent_event_id_rejects_121_chars_before_repository_write(db_session) -> None:
    """event_id 超过数据库 120 字符上限时必须在 repository 写入前拒绝。"""

    service = RuntimeInboxService()
    add_spy = AsyncMock(wraps=service.repository.add_received)
    service.repository.add_received = add_spy  # type: ignore[method-assign]

    with pytest.raises(ValueError, match=r"event_id.*120"):
        await service.accept_internal_event(
            db_session,
            event_type="IDENTITY_TOO_LONG",
            payload_json={"producer": "internal"},
            event_id="e" * 121,
        )

    assert add_spy.await_count == 0


@pytest.mark.asyncio
async def test_accept_internal_event_with_all_optional_args(db_session) -> None:
    """所有可选参数必须正确落库, correlation_id 走 trace_id 反查。"""

    service = RuntimeInboxService()

    session = ExecutionSession(
        workline_id=21,
        state="RUNNING",
    )
    db_session.add(session)
    await db_session.flush()

    trace_id = "trace-internal-001"
    correlation = ExecutionCorrelation(
        correlation_id="corr-internal-001",
        execution_session_id=session.id,
        trace_id=trace_id,
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="SOURCE_PICK_REQUESTED",
        payload_json={"handoff_demand_id": 7},
        trace_id=trace_id,
        event_id="evt-internal-001",
        causation_id="evt-internal-cause-001",
        workline_id=21,
        execution_session_id=session.id,
        correlation_id=None,
    )

    assert result.created is True
    record = result.record
    assert record.kind == "INTERNAL_EVENT"
    assert record.provider_code == "RUNTIME"
    assert record.event_type == "SOURCE_PICK_REQUESTED"
    assert record.source_event_id == "evt-internal-001"
    assert record.workline_id == 21
    assert record.execution_session_id == session.id
    assert record.trace_id == trace_id
    assert record.event_id == "evt-internal-001"
    assert record.causation_id == "evt-internal-cause-001"
    assert record.correlation_id == "corr-internal-001"


@pytest.mark.asyncio
async def test_accept_internal_event_uses_explicit_correlation_id(db_session) -> None:
    """调用方显式传入已持久化 correlation_id 时，必须直接采用。"""

    service = RuntimeInboxService()
    correlation = ExecutionCorrelation(
        correlation_id="corr-explicit-001",
        trace_id="trace-persisted-correlation",
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="trace-not-in-table",
        event_id="evt-explicit-correlation-001",
        correlation_id="corr-explicit-001",
    )

    assert result.created is True
    assert result.record.correlation_id == "corr-explicit-001"


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_unknown_explicit_correlation_id(db_session) -> None:
    """显式 correlation_id 不存在时必须在 service 边界拒绝，而非等待 FK flush 失败。"""

    service = RuntimeInboxService()

    with pytest.raises(RuntimeInboxCorrelationUnavailable) as exc_info:
        await service.accept_internal_event(
            db_session,
            event_type="INTERNAL_HEARTBEAT",
            payload_json={},
            event_id="evt-unknown-correlation-001",
            correlation_id="corr-not-persisted",
        )

    assert exc_info.value.correlation_id == "corr-not-persisted"


@pytest.mark.asyncio
async def test_accept_internal_event_orphan_trace_does_not_synthesize_correlation_id(db_session) -> None:
    """内部事件的孤立 trace_id 只用于追踪，不得冒充 ExecutionCorrelation 外键。"""

    service = RuntimeInboxService()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="evt-orphan-heartbeat-101",
        event_id="evt-orphan-heartbeat-101",
    )

    assert result.record.trace_id == "evt-orphan-heartbeat-101"
    assert result.record.correlation_id is None


@pytest.mark.asyncio
async def test_accept_internal_event_duplicate_trace_does_not_choose_arbitrary_correlation_id(db_session) -> None:
    """trace_id 非唯一时不得任意选择一条 ExecutionCorrelation。"""

    service = RuntimeInboxService()
    db_session.add_all(
        [
            ExecutionCorrelation(correlation_id="corr-duplicate-trace-a", trace_id="trace-duplicate"),
            ExecutionCorrelation(correlation_id="corr-duplicate-trace-b", trace_id="trace-duplicate"),
        ]
    )
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="trace-duplicate",
        event_id="evt-duplicate-trace-001",
    )

    assert result.record.correlation_id is None


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_missing_event_type(db_session) -> None:
    """event_type 缺失必须抛 ValueError。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_type"):
        await service.accept_internal_event(
            db_session,
            event_type="",
            payload_json={},
        )


@pytest.mark.asyncio
async def test_accept_internal_event_propagates_db_integrity_error(db_session) -> None:
    """repository.add_received 抛 IntegrityError 时, accept_internal_event 必须透传。"""

    service = RuntimeInboxService()

    broken_repo = SimpleNamespace(
        get_by_source_event_identity=AsyncMock(return_value=None),
        add_received=AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("constraint"))),
    )
    service.repository = broken_repo  # type: ignore[assignment]

    with pytest.raises(IntegrityError):
        await service.accept_internal_event(
            db_session,
            event_type="EVT",
            payload_json={},
            event_id="evt-integrity-error-001",
        )

    assert broken_repo.add_received.await_count == 1


@pytest.mark.asyncio
async def test_internal_producer_writes_non_empty_priority_bucket_and_received_at(db_session) -> None:
    """内部 producer 必须按身份优先级写稳定桶和毫秒时间。"""

    service = RuntimeInboxService()
    session = ExecutionSession(workline_id=31, state="RUNNING")
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id="corr-internal-lower-priority",
        execution_session_id=session.id,
        trace_id="trace-internal-lower-priority",
    )
    db_session.add(correlation)
    await db_session.flush()

    internal = await service.accept_internal_event(
        db_session,
        event_type="SOURCE_PICK_REQUESTED",
        payload_json={"event_type": "SOURCE_PICK_REQUESTED", "data": {}},
        event_id="evt-internal-bucket-001",
        execution_session_id=session.id,
        correlation_id="corr-internal-lower-priority",
        workline_id=31,
    )
    fallback = await service.accept_internal_event(
        db_session,
        event_type="RUNTIME_HEARTBEAT",
        payload_json={"event_type": "RUNTIME_HEARTBEAT", "data": {}},
        event_id="evt-fallback-001",
    )

    assert internal.record.claim_bucket_key == f"execution-session:{session.id}"
    assert fallback.record.claim_bucket_key.startswith("source:RUNTIME:RUNTIME_HEARTBEAT:evt-fallback-001")
    assert all(result.record.received_at and result.record.received_at > 0 for result in (internal, fallback))


@pytest.mark.asyncio
async def test_internal_producer_acks_duplicate_source_identity_with_same_payload(db_session) -> None:
    """内部 producer 的稳定 source identity 必须同 hash ACK。"""

    service = RuntimeInboxService()
    kwargs = {
        "event_type": "DUPLICATE_TEST",
        "event_id": "evt-internal-duplicate-001",
        "payload_json": {"event_type": "DUPLICATE_TEST", "data": {"value": 1}},
    }
    first = await service.accept_internal_event(db_session, **kwargs)
    duplicate = await service.accept_internal_event(db_session, **kwargs)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.record.id == first.record.id
    assert first.record.payload_hash


@pytest.mark.asyncio
async def test_internal_producer_rejects_duplicate_source_identity_with_different_payload(db_session) -> None:
    """同一内部 source identity 的不同 canonical payload 必须走显式冲突合同。"""

    service = RuntimeInboxService()
    common = {
        "event_type": "DUPLICATE_TEST",
        "event_id": "evt-internal-conflict-001",
    }
    await service.accept_internal_event(db_session, payload_json={"value": 1}, **common)

    with pytest.raises(RuntimeInboxConflict):
        await service.accept_internal_event(db_session, payload_json={"value": 2}, **common)
