"""RuntimeInbox production service contract tests."""

from __future__ import annotations

import json
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


def _canonical_payload_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    )


async def _accept_received(service: Any, db: Any, **kwargs: Any) -> Any:
    """使用全量 canonical contract 调用 RuntimeInboxService.accept_received。"""
    event_type = str(kwargs["event_type"])
    kind = "COMMAND_RESULT" if "RESULT" in event_type.upper() else "EXTERNAL_HTTP"
    return await service.accept_received(
        db,
        kind=kind,
        payload_json={"event_type": event_type},
        payload_schema_version=1,
        **kwargs,
    )


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
        from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository

        self.real_repository = RuntimeInboxRepository()
        self.read_count = 0

    async def get_by_source_event_identity(self, db: Any, **kwargs: Any) -> Any | None:
        self.read_count += 1
        if self.read_count == 1:
            return None
        return await self.real_repository.get_by_source_event_identity(db, **kwargs)

    async def add_received(self, db: Any, data: dict[str, Any]) -> RuntimeInbox:
        return await self.real_repository.add_received(db, data)


class _RuntimeInboxCorrelationValidationRaceRepository:
    """模拟首次查询后、correlation 校验前由并发方插入既有 identity。"""

    def __init__(self, existing: Any) -> None:
        self.existing = existing
        self.read_count = 0

    async def get_by_source_event_identity(self, *_args: Any, **_kwargs: Any) -> Any | None:
        self.read_count += 1
        return None if self.read_count == 1 else self.existing

    async def add_received(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("correlation 校验失败后不应继续 INSERT")


class _IdempotencyGuardSpy:
    """记录 claim 调用，验证归属冲突不会产生幂等副作用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def claim_or_match(self, _db: Any, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return object()


@pytest.mark.asyncio
async def test_accept_received_rejects_workline_session_namespace_mismatch(db_session) -> None:
    """显式 WorklineSession FK 与 canonical ref 不一致时不得落库。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    with pytest.raises(ValueError, match="workline_session_id mismatch"):
        await RuntimeInboxService().accept_received(
            db_session,
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            source_event_id="mismatch-session-ref",
            payload_hash="hash",
            kind="INTERNAL_EVENT",
            payload_json={"event_type": "INTERNAL_EVENT", "data": {"session_id": 41}},
            payload_schema_version=1,
            workline_session_id=42,
        )

    rows = (await db_session.execute(select(RuntimeInbox))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_accept_received_rejects_unknown_explicit_correlation_before_repository_write(db_session) -> None:
    """统一入口必须在 repository 写入前拒绝不存在的 ExecutionCorrelation。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxCorrelationUnavailable,
        RuntimeInboxService,
    )

    with pytest.raises(RuntimeInboxCorrelationUnavailable, match="correlation is unavailable"):
        await _accept_received(
            RuntimeInboxService(),
            db_session,
            provider_code="ECS",
            event_type="COMMAND_RESULT",
            source_event_id="evt-unknown-correlation",
            payload_hash="hash-unknown-correlation",
            correlation_id="corr-not-persisted",
        )

    rows = (await db_session.execute(select(RuntimeInbox))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_callback_result_writer_rejects_unknown_command_correlation(db_session) -> None:
    """result writer 不得把 DeviceCommand 上的孤立 correlation 传入 RuntimeInbox FK。"""

    from src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer import CallbackRuntimeInboxWriter
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxCorrelationUnavailable

    with pytest.raises(RuntimeInboxCorrelationUnavailable, match="correlation is unavailable"):
        await CallbackRuntimeInboxWriter().write_result_callback(
            db_session,
            payload={
                "command_code": "CMD-UNKNOWN-CORR",
                "device_code": "ARM_01",
                "result": "SUCCESS",
            },
            request_id="req-unknown-corr",
            canonical_result_type="DEVICE_RESULT",
            correlation_id="corr-command-not-persisted",
        )

    rows = (await db_session.execute(select(RuntimeInbox))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_runtime_inbox_accept_returns_existing_ack_for_same_hash(db_session) -> None:
    """同 source event 且 payload_hash 一致时返回既有 ACK, 不新建记录。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()

    first = await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-001",
        payload_hash="hash-001",
    )
    second = await _accept_received(
        service,
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
async def test_accept_received_existing_same_hash_acks_before_unknown_correlation_validation(db_session) -> None:
    """已落库的同 K/H 重试应直接 ACK，不受迟到或已清理 correlation 影响。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()
    first = await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-existing-before-correlation",
        payload_hash="hash-existing-before-correlation",
    )

    retry = await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-existing-before-correlation",
        payload_hash="hash-existing-before-correlation",
        correlation_id="corr-no-longer-present",
    )

    assert retry.created is False
    assert retry.record.id == first.record.id


@pytest.mark.asyncio
async def test_accept_received_existing_different_hash_conflicts_before_unknown_correlation_validation(
    db_session,
) -> None:
    """既有 identity 的 hash 冲突必须保持 409 优先级，不能被关联完整性错误遮蔽。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxConflict,
        RuntimeInboxService,
    )

    service = RuntimeInboxService()
    await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-conflict-before-correlation",
        payload_hash="hash-original",
    )

    with pytest.raises(RuntimeInboxConflict):
        await _accept_received(
            service,
            db_session,
            provider_code="WMS",
            event_type="WMS_TASK_CHANGE",
            source_event_id="evt-conflict-before-correlation",
            payload_hash="hash-changed",
            correlation_id="corr-no-longer-present",
        )


@pytest.mark.parametrize("incoming_hash", ["hash-race", "hash-changed"], ids=["same-hash", "different-hash"])
@pytest.mark.asyncio
async def test_accept_received_correlation_validation_race_rechecks_existing_identity(
    db_session,
    incoming_hash: str,
) -> None:
    """关联校验失败时必须回读并发插入的 identity，再按 K/H 收敛为 ACK 或 409。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxConflict,
        RuntimeInboxService,
    )

    existing = SimpleNamespace(id=701, payload_hash="hash-race", workline_session_id=None)
    service = RuntimeInboxService(repository=_RuntimeInboxCorrelationValidationRaceRepository(existing))

    call = _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-correlation-validation-race",
        payload_hash=incoming_hash,
        correlation_id="corr-no-longer-present",
    )
    if incoming_hash == existing.payload_hash:
        result = await call
        assert result.created is False
        assert result.record is existing
    else:
        with pytest.raises(RuntimeInboxConflict):
            await call


@pytest.mark.asyncio
async def test_accept_received_retry_without_owner_keeps_processor_assigned_owner(db_session) -> None:
    """未声明 owner 的相同 K/H 重试应 ACK processor 已回填的 owner，且不重复 claim。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    correlation = await _seed_execution_correlation(
        db_session,
        correlation_id="corr-owner-filled-by-processor",
    )
    guard = _IdempotencyGuardSpy()
    service = RuntimeInboxService(idempotency_guard=guard)  # type: ignore[arg-type]
    first = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id="evt-owner-filled-by-processor",
        payload_hash="hash-owner-filled-by-processor",
        correlation_id=correlation.correlation_id,
    )
    await db_session.flush()

    # 模拟 processor 在 ACK 后解析业务上下文并持久化会话归属。
    first.record.workline_session_id = 41
    await db_session.flush()

    retry = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id="evt-owner-filled-by-processor",
        payload_hash="hash-owner-filled-by-processor",
        correlation_id=correlation.correlation_id,
    )

    await db_session.refresh(first.record)
    assert retry.created is False
    assert retry.record.id == first.record.id
    assert retry.record.workline_session_id == 41
    assert first.record.workline_session_id == 41
    assert len(guard.calls) == 1


@pytest.mark.asyncio
async def test_accept_received_explicit_owner_acks_existing_unspecified_owner_without_backfill(db_session) -> None:
    """既有记录未定 owner 时，incoming 明确 owner 可 ACK，但 ACK 路径不得回填归属。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    service = RuntimeInboxService()
    first = await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-existing-owner-unspecified",
        payload_hash="hash-existing-owner-unspecified",
    )
    await db_session.flush()

    retry = await _accept_received(
        service,
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-existing-owner-unspecified",
        payload_hash="hash-existing-owner-unspecified",
        workline_session_id=41,
    )

    await db_session.refresh(first.record)
    assert retry.created is False
    assert retry.record.id == first.record.id
    assert retry.record.workline_session_id is None
    assert first.record.workline_session_id is None


@pytest.mark.asyncio
async def test_accept_received_unique_race_without_owner_acks_processor_assigned_owner_without_claim(
    db_session,
) -> None:
    """唯一键竞态回读 owner=41 时，未声明 owner 的重试应直接 ACK 且不 claim。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    existing = SimpleNamespace(
        id=10,
        payload_hash="hash-owner-race-unspecified",
        status="RECEIVED",
        workline_session_id=41,
    )
    repository = _RuntimeInboxUniqueRaceRepository(existing)
    correlation = await _seed_execution_correlation(
        db_session,
        correlation_id="corr-owner-race-unspecified",
    )
    guard = _IdempotencyGuardSpy()
    service = RuntimeInboxService(repository=repository, idempotency_guard=guard)  # type: ignore[arg-type]

    retry = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id="evt-owner-race-unspecified",
        payload_hash="hash-owner-race-unspecified",
        correlation_id=correlation.correlation_id,
    )

    assert retry.created is False
    assert retry.record is existing
    assert existing.workline_session_id == 41
    assert repository.add_calls == 1
    assert guard.calls == []


@pytest.mark.asyncio
async def test_accept_received_rejects_existing_identity_owned_by_another_workline_session(db_session) -> None:
    """K/H 相同也不能跨 WorklineSession 归属复用 ACK。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxService,
        RuntimeInboxSessionOwnershipConflict,
    )

    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-session-owner")
    guard = _IdempotencyGuardSpy()
    service = RuntimeInboxService(idempotency_guard=guard)  # type: ignore[arg-type]
    first = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="COMMAND_RESULT",
        source_event_id="evt-session-owner",
        payload_hash="hash-session-owner",
        correlation_id=correlation.correlation_id,
        workline_session_id=41,
    )

    with pytest.raises(RuntimeInboxSessionOwnershipConflict) as exc_info:
        await _accept_received(
            service,
            db_session,
            provider_code="ECS",
            event_type="COMMAND_RESULT",
            source_event_id="evt-session-owner",
            payload_hash="hash-session-owner",
            correlation_id=correlation.correlation_id,
            workline_session_id=42,
        )

    await db_session.refresh(first.record)
    assert exc_info.value.status_code == 409
    assert first.record.workline_session_id == 41
    assert len(guard.calls) == 1


@pytest.mark.asyncio
async def test_accept_received_unique_race_rejects_another_session_before_idempotency_claim(db_session) -> None:
    """唯一键竞态回读到其他会话归属时，不得留下 claim 副作用。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxService,
        RuntimeInboxSessionOwnershipConflict,
    )

    existing = SimpleNamespace(
        id=9,
        payload_hash="hash-session-race",
        status="RECEIVED",
        workline_session_id=41,
    )
    repository = _RuntimeInboxUniqueRaceRepository(existing)
    correlation = await _seed_execution_correlation(db_session, correlation_id="corr-session-race")
    guard = _IdempotencyGuardSpy()
    service = RuntimeInboxService(repository=repository, idempotency_guard=guard)  # type: ignore[arg-type]

    with pytest.raises(RuntimeInboxSessionOwnershipConflict):
        await _accept_received(
            service,
            db_session,
            provider_code="ECS",
            event_type="COMMAND_RESULT",
            source_event_id="evt-session-race",
            payload_hash="hash-session-race",
            correlation_id=correlation.correlation_id,
            workline_session_id=42,
        )

    assert repository.add_calls == 1
    assert guard.calls == []


@pytest.mark.asyncio
async def test_runtime_inbox_accepts_canonical_payload_at_exact_utf8_byte_limit(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTF-8 canonical JSON bytes 等于上限时允许持久化。"""
    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService
    from src.core.conf import settings

    payload = {"message": "中文边界"}
    monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", _canonical_payload_size(payload))

    accepted = await RuntimeInboxService().accept_received(
        db_session,
        provider_code="WMS",
        event_type="WMS_RACK_TASK_RESULT",
        source_event_id="evt-payload-boundary",
        payload_hash="hash-boundary",
        kind="EXTERNAL_HTTP",
        payload_json=payload,
        payload_schema_version=1,
        trace_id="trace-payload-boundary",
    )

    assert accepted.created is True
    assert accepted.record.payload_json == payload


@pytest.mark.asyncio
async def test_runtime_inbox_rejects_oversized_canonical_payload_before_repository_add(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """canonical payload 超限必须在 RuntimeInbox add/ACK 前失败并保持零落库。"""
    from sqlalchemy import func

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxPayloadTooLarge,
        RuntimeInboxService,
    )
    from src.core.conf import settings

    payload = {"message": "中文超限"}
    payload_size = _canonical_payload_size(payload)
    monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", payload_size - 1)

    with pytest.raises(RuntimeInboxPayloadTooLarge) as exc_info:
        await RuntimeInboxService().accept_received(
            db_session,
            provider_code="WMS",
            event_type="WMS_RACK_TASK_RESULT",
            source_event_id="evt-payload-too-large",
            payload_hash="hash-too-large",
            kind="EXTERNAL_HTTP",
            payload_json=payload,
            payload_schema_version=1,
            trace_id="trace-payload-too-large",
        )

    assert exc_info.value.actual_bytes == payload_size
    assert exc_info.value.max_bytes == payload_size - 1
    count = await db_session.scalar(select(func.count()).select_from(RuntimeInbox))
    assert count == 0


@pytest.mark.asyncio
async def test_internal_producer_uses_same_canonical_payload_size_guard(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内部 producer 与 external 必须共用 repository add 前的 bytes guard。"""
    from sqlalchemy import func

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
        RuntimeInboxPayloadTooLarge,
        RuntimeInboxService,
    )
    from src.core.conf import settings

    monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", 1)

    with pytest.raises(RuntimeInboxPayloadTooLarge):
        await RuntimeInboxService().accept_device_event(
            db_session,
            device_code="ARM_01",
            event_type="SCAN_COMPLETED",
            payload_json={"event_type": "SCAN_COMPLETED", "data": {"barcode": "TOO-LARGE"}},
        )

    count = await db_session.scalar(select(func.count()).select_from(RuntimeInbox))
    assert count == 0


@pytest.mark.asyncio
async def test_runtime_inbox_accept_returns_existing_after_unique_conflict(db_session) -> None:
    """并发插入撞唯一索引时，必须重新读取既有记录并返回幂等 ACK。"""

    from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxService

    existing = SimpleNamespace(id=7, payload_hash="hash-001", status="RECEIVED")
    repository = _RuntimeInboxUniqueRaceRepository(existing)
    service = RuntimeInboxService(repository=repository)

    result = await _accept_received(
        service,
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

    result = await _accept_received(
        service,
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
    _ = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id="evt-002",
        payload_hash="hash-original",
    )

    with pytest.raises(RuntimeInboxConflict) as exc_info:
        _ = await _accept_received(
            service,
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

    result_record = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="DEVICE_RESULT",
        source_event_id="evt-shared-001",
        payload_hash="hash-result-001",
    )
    event_record = await _accept_received(
        service,
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
        await _accept_received(
            service,
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

    first = await _accept_received(
        service,
        db_session,
        provider_code="ECS",
        event_type="COMMAND_RESULT",
        source_event_id="evt-device-001",
        payload_hash="hash-device-001",
        correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )
    second = await _accept_received(
        service,
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
        await _accept_received(
            RuntimeInboxService(),
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

    first = await _accept_received(
        service,
        db_session,
        provider_code="AGV",
        event_type="external",
        source_event_id=None,
        payload_hash="hash-missing-001",
    )
    second = await _accept_received(
        service,
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
    correlation = ExecutionCorrelation(
        correlation_id="corr-lower-priority",
        execution_session_id=session.id,
        trace_id="trace-lower-priority",
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await _accept_received(
        RuntimeInboxService(),
        db_session,
        provider_code="WMS",
        event_type="WMS_TASK_CHANGE",
        source_event_id="evt-bucket-001",
        payload_hash="hash-bucket-001",
        execution_session_id=session.id,
        correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert result.record.claim_bucket_key == f"execution-session:{session.id}"
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

    _ = await _accept_received(
        RuntimeInboxService(),
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
