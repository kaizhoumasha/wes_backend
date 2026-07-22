from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import runtime_inbox_repository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import SystemOutboxStatus
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxTargetType,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository


class _FakeResult:
    def __init__(self, outboxes: list[Any]) -> None:
        self._outboxes = outboxes

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._outboxes


class _CapturingDb:
    def __init__(self, outboxes: list[Any]) -> None:
        self.outboxes = outboxes
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _FakeResult:
        self.statement = statement
        return _FakeResult(self.outboxes)


def _compiled_status_values(statement: Any) -> set[SystemOutboxStatus]:
    values: set[SystemOutboxStatus] = set()
    for value in statement.compile().params.values():
        if isinstance(value, list):
            values.update(item for item in value if isinstance(item, SystemOutboxStatus))
    return values


@pytest.mark.asyncio
async def test_cancel_active_by_session_treats_retry_wait_as_active() -> None:
    blocked_outbox = SimpleNamespace(
        status=SystemOutboxStatus.RETRY_WAIT,
        last_error=None,
        finished_at=None,
    )
    db = _CapturingDb([blocked_outbox])

    count = await SystemOutboxRepository().cancel_active_by_session(
        db,
        session_id=7001,
        reason="MANUAL_CANCEL_REQUESTED",
    )

    assert db.statement is not None
    assert SystemOutboxStatus.RETRY_WAIT in _compiled_status_values(db.statement)
    assert count == 1
    assert blocked_outbox.status == SystemOutboxStatus.CANCELLED
    assert blocked_outbox.last_error == "MANUAL_CANCEL_REQUESTED"
    assert blocked_outbox.finished_at is not None


@pytest.mark.asyncio
async def test_repository_rejects_dispatch_key_updates_before_loading_row() -> None:
    with pytest.raises(ValueError, match=r"dispatch_key.*不可变"):
        await SystemOutboxRepository().update(  # type: ignore[arg-type]
            SimpleNamespace(),
            7,
            {"dispatch_key": "replacement"},
        )


@pytest.mark.asyncio
async def test_resource_wait_rejects_uncontrolled_retry_reason_before_loading_row() -> None:
    with pytest.raises(ValueError, match="不受控"):
        await SystemOutboxRepository().block_for_resource_wait(  # type: ignore[arg-type]
            SimpleNamespace(),
            7,
            reason="HTTP_503_BACKOFF",
            blocked_device_id=9,
        )


@pytest.mark.asyncio
async def test_sandbox_completed_messages_join_runtime_inbox_by_explicit_workline_session(
    db_session: Any,
) -> None:
    """沙箱历史用独立 WorklineSession FK 关联 RuntimeInbox，不读取旧 inbox。"""

    session = WorklineSession(
        session_code="sandbox-runtime-inbox-1",
        workline_id=901,
        plugin_key="test",
        run_mode=RunMode.SIMULATION,
        status=SessionStatus.COMPLETED,
    )
    db_session.add(session)
    await db_session.flush()
    canonical = CanonicalPayload.from_projection({})
    outbox = SystemOutbox(
        session_id=session.id,
        workline_id=901,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key="sandbox-runtime-inbox-dispatch-1",
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code="TEST",
        payload_json={},
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        status=SystemOutboxStatus.SENT,
    )
    inbox = RuntimeInbox(
        workline_session_id=session.id,
        provider_code="TEST",
        event_type="DEVICE_EVENT",
        source_event_id="sandbox-runtime-inbox-event-1",
        kind="DEVICE_EVENT",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"session_id": session.id}},
        payload_hash="sha256:sandbox-runtime-inbox-event-1",
        payload_schema_version=1,
        status="PROCESSED",
        claim_bucket_key=f"workline-session:{session.id}",
        received_at=1,
    )
    db_session.add_all([outbox, inbox])
    await db_session.commit()

    rows = await SystemOutboxRepository().get_sandbox_completed_messages(
        db_session,
        inbox_query=runtime_inbox_repository,
        limit=10,
    )

    assert rows[0]["session"]["id"] == session.id
    assert rows[0]["session"]["event_type"] == "SCAN_COMPLETED"
    assert rows[0]["session"]["event_payload"]["data"]["session_id"] == session.id
