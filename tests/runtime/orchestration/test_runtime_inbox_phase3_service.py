"""Phase 3 RuntimeInbox production service contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

# 这些模型 import 用于注册隔离 SQLite create_all 所需的跨表 FK metadata。
from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.workline.models import WorkLine


class _AuditServiceStub:
    """捕获 RuntimeInbox 人工重放审计调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_audit_log(self, _db: Any, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(id=len(self.calls))


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
