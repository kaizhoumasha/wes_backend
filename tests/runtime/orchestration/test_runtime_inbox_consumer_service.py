"""RuntimeInbox production service contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

# 这些模型 import 用于注册隔离 SQLite create_all 所需的跨表 FK metadata。
from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.workline.models import WorkLine

NOW_MS = 1_700_000_000_000


async def _seed_execution_correlation(db_session, *, correlation_id: str = "corr-device-event-001"):
    """建立 ExecutionSession + ExecutionCorrelation，满足 IdempotencyKey FK 前置。"""

    session = ExecutionSession(workline_id=1, manifest_version="v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id=f"trace-{correlation_id}",
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


class _AuditServiceStub:
    """捕获 RuntimeInbox 人工重放审计调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_audit_log(self, _db: Any, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(id=len(self.calls))


class _RuntimeInboxUniqueRaceRepository:
    """模拟查询后并发插入导致唯一索引冲突的 repository。"""

    def __init__(self, existing: Any) -> None:
        self.existing = existing
        self.add_calls = 0

    async def get_by_source_event_identity(self, *_args: Any, **_kwargs: Any) -> Any | None:
        return self.existing if self.add_calls > 0 else None

    async def add_received(self, *_args: Any, **_kwargs: Any) -> Any:
        self.add_calls += 1
        raise IntegrityError("INSERT INTO runtime_inbox", {}, Exception("unique source event"))


class _RuntimeInboxStaleReadRepository:
    """用真实 add_received 触发唯一索引冲突，同时模拟第一次读到旧快照。"""

    def __init__(self) -> None:
        from src.app.runtime.orchestration.consumers.runtime_inbox_repository import RuntimeInboxRepository

        self.real_repository = RuntimeInboxRepository()
        self.read_count = 0

    async def get_by_source_event_identity(self, db: Any, **kwargs: Any) -> Any | None:
        self.read_count += 1
        if self.read_count == 1:
            return None
        return await self.real_repository.get_by_source_event_identity(db, **kwargs)

    async def add_received(self, db: Any, data: dict[str, Any]) -> RuntimeInbox:
        return await self.real_repository.add_received(db, data)


@pytest.mark.asyncio
async def test_runtime_inbox_accept_returns_existing_ack_for_same_hash(db_session) -> None:
    """同 source event 且 payload_hash 一致时返回既有 ACK, 不新建记录。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()

    first = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-001",
        payload_hash="hash-001",
    )
    second = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-001",
        payload_hash="hash-001",
    )

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id
    assert second.record.status == "RECEIVED"


@pytest.mark.asyncio
async def test_runtime_inbox_accept_returns_existing_after_unique_conflict(db_session) -> None:
    """并发插入撞唯一索引时，必须重新读取既有记录并返回幂等 ACK。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    existing = SimpleNamespace(id=7, payload_hash="hash-001", status="RECEIVED")
    repository = _RuntimeInboxUniqueRaceRepository(existing)
    service = RuntimeInboxService(repository=repository)

    result = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-race-001",
        payload_hash="hash-001",
    )

    assert result.created is False
    assert result.record is existing
    assert repository.add_calls == 1


@pytest.mark.asyncio
async def test_runtime_inbox_accept_keeps_session_usable_after_real_unique_conflict(db_session) -> None:
    """真实 flush 撞唯一索引后，savepoint rollback 必须允许重读和继续写入。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    existing = RuntimeInbox(
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-real-race-001",
        payload_hash="hash-001",
        status="RECEIVED",
    )
    db_session.add(existing)
    await db_session.flush()
    assert existing.id is not None

    repository = _RuntimeInboxStaleReadRepository()
    service = RuntimeInboxService(repository=repository)

    result = await service.accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-real-race-001",
        payload_hash="hash-001",
    )

    assert result.created is False
    assert result.record.id == existing.id

    probe = RuntimeInbox(
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-real-race-probe",
        payload_hash="hash-probe",
        status="RECEIVED",
    )
    db_session.add(probe)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(RuntimeInbox).where(RuntimeInbox.source_event_id.in_(["evt-real-race-001", "evt-real-race-probe"]))
        )
    ).scalars()
    assert {record.source_event_id for record in rows} == {"evt-real-race-001", "evt-real-race-probe"}


@pytest.mark.asyncio
async def test_runtime_inbox_accept_rejects_same_event_different_hash(db_session) -> None:
    """同 source event 不同 payload_hash 必须 409, 不静默覆盖 evidence。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxConflict,
        RuntimeInboxService,
    )

    service = RuntimeInboxService()
    _ = await service.accept_received(
        db_session,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id="evt-002",
        payload_hash="hash-original",
    )

    with pytest.raises(RuntimeInboxConflict) as exc_info:
        _ = await service.accept_received(
            db_session,
            provider_code="ECS",
            event_type="DEVICE_EVENT",
            source_event_id="evt-002",
            payload_hash="hash-tampered",
        )

    audit_event = exc_info.value.to_audit_event()
    assert exc_info.value.status_code == 409
    assert audit_event["event_type"] == "RUNTIME_INBOX_PAYLOAD_CONFLICT"
    assert audit_event["existing_payload_hash"] == "hash-original"
    assert audit_event["incoming_payload_hash"] == "hash-tampered"


@pytest.mark.asyncio
async def test_runtime_inbox_accept_keeps_distinct_canonical_event_types_separate(db_session) -> None:
    """同 source_event_id 但不同 canonical callback/event type 不得落入同一幂等空间。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()

    result_record = await service.accept_received(
        db_session,
        provider_code="ECS",
        event_type="DEVICE_RESULT",
        source_event_id="evt-shared-001",
        payload_hash="hash-result-001",
    )
    event_record = await service.accept_received(
        db_session,
        provider_code="ECS",
        event_type="SCAN_COMPLETED",
        source_event_id="evt-shared-001",
        payload_hash="hash-event-001",
    )

    assert result_record.created is True
    assert event_record.created is True
    assert result_record.record.id != event_record.record.id


@pytest.mark.asyncio
async def test_runtime_inbox_accept_conflict_after_unique_conflict(db_session) -> None:
    """并发插入后发现同 source event 不同 hash 时，仍必须返回 409 conflict。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxConflict,
        RuntimeInboxService,
    )

    existing = SimpleNamespace(id=8, payload_hash="hash-original", status="RECEIVED")
    repository = _RuntimeInboxUniqueRaceRepository(existing)
    service = RuntimeInboxService(repository=repository)

    with pytest.raises(RuntimeInboxConflict) as exc_info:
        await service.accept_received(
            db_session,
            provider_code="WMS",
            event_type="WMS_TASK_CHANGE",
            source_event_id="evt-race-002",
            payload_hash="hash-tampered",
        )

    assert exc_info.value.existing_payload_hash == "hash-original"
    assert exc_info.value.incoming_payload_hash == "hash-tampered"


@pytest.mark.asyncio
async def test_runtime_inbox_device_event_accept_claims_idempotency_key(db_session) -> None:
    """device_event 入站生产入口必须同步 claim IdempotencyKey。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    correlation = await _seed_execution_correlation(db_session)
    service = RuntimeInboxService()

    first = await service.accept_received(
        db_session,
        provider_code="ECS",
        event_type="COMMAND_RESULT",
        source_event_id="evt-device-001",
        payload_hash="hash-device-001",
        correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )
    second = await service.accept_received(
        db_session,
        provider_code="ECS",
        event_type="COMMAND_RESULT",
        source_event_id="evt-device-001",
        payload_hash="hash-device-001",
        correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert first.created is True
    assert second.created is False
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.provider_code == "ECS",
                IdempotencyKey.operation_kind == "device_event",
                IdempotencyKey.idempotency_key == "evt-device-001",
            )
        )
    ).scalar_one()
    assert stored.request_hash == "hash-device-001"
    assert stored.execution_correlation_id == correlation.correlation_id


@pytest.mark.asyncio
async def test_runtime_inbox_device_event_accept_rejects_existing_idempotency_hash_conflict(db_session) -> None:
    """device_event 已有 IdempotencyKey 不同 hash 时必须 409 并暴露 device 审计域。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
    from src.app.runtime.orchestration.services.idempotency_guard import IdempotencyConflict

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-device-event-conflict")
    db_session.add(
        IdempotencyKey(
            provider_code="ECS",
            operation_kind="device_event",
            idempotency_key="evt-device-conflict",
            execution_correlation_id=correlation.correlation_id,
            request_hash="hash-original",
            business_owner_key="device_event:evt-device-conflict",
            created_at=NOW_MS,
        )
    )
    await db_session.flush()

    with pytest.raises(IdempotencyConflict) as exc_info:
        await RuntimeInboxService().accept_received(
            db_session,
            provider_code="ECS",
            event_type="EVENT_PUSH",
            source_event_id="evt-device-conflict",
            payload_hash="hash-tampered",
            correlation_id=correlation.correlation_id,
            now_ms=NOW_MS,
        )

    audit_event = exc_info.value.to_audit_event()
    assert audit_event["normalized_operation_kind"] == "device_event"
    assert audit_event["domain"] == "device"
    assert audit_event["incoming_request_hash"] == "hash-tampered"


@pytest.mark.asyncio
async def test_runtime_inbox_manual_replay_creates_new_record_and_audit(db_session) -> None:
    """DEAD_LETTER 人工重放必须新建 inbox 记录并写审计。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    session = ExecutionSession(workline_id=11, manifest_version="manifest-v1", state="HOLD")
    db_session.add(session)
    await db_session.flush()

    dead = RuntimeInbox(
        execution_session_id=session.id,
        provider_code="WMS",
        event_type="WMS_EXCHANGE_COMPLETED",
        source_event_id="evt-dead-001",
        payload_hash="hash-dead-001",
        status="DEAD_LETTER",
        attempt_count=5,
        max_retries=5,
    )
    db_session.add(dead)
    await db_session.flush()
    assert dead.id is not None

    audit_service = _AuditServiceStub()
    service = RuntimeInboxService(audit_service=audit_service)
    result = await service.replay_from_dead_letter(
        db_session,
        source_inbox_id=dead.id,
        actor="ops-aaron",
        reason="修复 provider 字段映射后重放",
        replay_source_event_id="evt-dead-001:replay-001",
    )

    assert dead.status == "DEAD_LETTER"
    assert result.replay_record.id != dead.id
    assert result.replay_record.status == "RECEIVED"
    assert result.replay_record.payload_hash == "hash-dead-001"
    assert result.replay_record.execution_session_id == session.id
    assert result.audit_event["source_inbox_id"] == str(dead.id)
    assert result.audit_event["replay_inbox_id"] == str(result.replay_record.id)
    assert audit_service.calls
    assert audit_service.calls[0]["title"] == "RuntimeInbox 人工重放"
    assert audit_service.calls[0]["args"]["actor"] == "ops-aaron"


@pytest.mark.asyncio
async def test_runtime_inbox_accept_allows_missing_source_event_id_without_dedup(db_session) -> None:
    """source_event_id 缺失时允许 ACK，但不做 source-event 级去重。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()

    first = await service.accept_received(
        db_session,
        provider_code="AGV",
        event_type="external",
        source_event_id=None,
        payload_hash="hash-missing-001",
    )
    second = await service.accept_received(
        db_session,
        provider_code="AGV",
        event_type="external",
        source_event_id=None,
        payload_hash="hash-missing-001",
    )

    assert first.created is True
    assert second.created is True
    assert first.record.id != second.record.id


@pytest.mark.asyncio
async def test_runtime_inbox_accept_received_writes_stable_bucket_and_received_at(db_session) -> None:
    """普通入站必须写毫秒接收时间，并按 session 优先生成稳定桶键。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    session = ExecutionSession(workline_id=17, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()

    result = await RuntimeInboxService().accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-bucket-001",
        payload_hash="hash-bucket-001",
        execution_session_id=session.id,
        correlation_id="corr-lower-priority",
        now_ms=NOW_MS,
    )

    assert result.record.claim_bucket_key == f"session:{session.id}"
    assert result.record.received_at == NOW_MS


@pytest.mark.parametrize(
    ("event_type", "expected_operation_kind", "expected_domain"),
    [
        ("result", "device_event", "device"),
        ("DEVICE_RESULT", "device_event", "device"),
        ("event", "device_event", "device"),
        ("external", "callback", "callback"),
        ("fulfillment", "fulfillment", "wms_integration"),
        ("device_event", "device_event", "device"),
        ("reconciliation", "reconciliation", "reconciliation"),
    ],
)
def test_runtime_inbox_conflict_audit_maps_operation_kind(
    event_type: str,
    expected_operation_kind: str,
    expected_domain: str,
) -> None:
    """RuntimeInbox 冲突审计必须覆盖 callback/result/event 等 canonical operation_kind。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxConflict

    audit_event = RuntimeInboxConflict(
        provider_code="ECS",
        event_type=event_type,
        source_event_id="evt-map-001",
        existing_payload_hash="hash-old",
        incoming_payload_hash="hash-new",
    ).to_audit_event()

    assert audit_event["operation_kind"] == expected_operation_kind
    assert audit_event["domain"] == expected_domain


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    ["result", "event", "command_result", "event_push", "device_event", "DEVICE_RESULT", "device_result"],
)
async def test_runtime_inbox_accept_device_event_aliases_claim_idempotency_key(db_session, event_type: str) -> None:
    """result/event canonical 与 legacy alias 都必须归一到 device_event 并 claim IdempotencyKey。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    correlation = await _seed_execution_correlation(db_session, correlation_id=f"corr-{event_type}")
    source_event_id = f"evt-{event_type}"

    _ = await RuntimeInboxService().accept_received(
        db_session,
        provider_code="ECS",
        event_type=event_type,
        source_event_id=source_event_id,
        payload_hash=f"hash-{event_type}",
        correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.provider_code == "ECS",
                IdempotencyKey.operation_kind == "device_event",
                IdempotencyKey.idempotency_key == source_event_id,
            )
        )
    ).scalar_one()
    assert stored.request_hash == f"hash-{event_type}"
