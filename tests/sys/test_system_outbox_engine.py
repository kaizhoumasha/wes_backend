from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.runtime.orchestration.services.device_command_gateway import _DeviceCommandGovernanceError
from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.services import SystemOutboxEngine as SystemOutboxDispatcher


class FakeSystemOutboxRepository:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages
        self.block_resource_wait_returns_none = False
        self.mark_dispatching_calls: list[int] = []
        self.mark_sent_calls: list[int] = []
        self.mark_failed_calls: list[tuple[int, str, int]] = []
        self.blocked_resource_calls: list[dict[str, Any]] = []
        self.pending_filters: list[dict[str, Any]] = []

    async def get_pending_messages(
        self,
        _db: Any,
        limit: int = 50,
        **filters: Any,
    ) -> list[Any]:
        self.pending_filters.append({"limit": limit, **filters})
        messages = self.messages
        excluded_domains = tuple(filters.get("exclude_operation_domains") or ())
        if excluded_domains:
            messages = [
                message for message in messages if getattr(message, "operation_domain", None) not in excluded_domains
            ]
        included_domains = tuple(filters.get("operation_domains") or ())
        if included_domains:
            messages = [
                message for message in messages if getattr(message, "operation_domain", None) in included_domains
            ]
        return messages[:limit]

    async def mark_as_dispatching(self, _db: Any, outbox_id: int) -> Any | None:
        self.mark_dispatching_calls.append(outbox_id)
        now = datetime(2026, 5, 22, 8, 0, 0)
        for message in self.messages:
            stale_dispatching = (
                message.status == SystemOutboxStatus.DISPATCHING
                and message.next_retry_at is not None
                and message.next_retry_at <= now
            )
            if message.id == outbox_id and (
                message.status in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT} or stale_dispatching
            ):
                message.status = SystemOutboxStatus.DISPATCHING
                message.next_retry_at = now + timedelta(minutes=5)
                return message
        return None

    async def mark_as_sent(self, _db: Any, outbox_id: int) -> Any | None:
        self.mark_sent_calls.append(outbox_id)
        for message in self.messages:
            if message.id == outbox_id and message.status == SystemOutboxStatus.DISPATCHING:
                message.status = SystemOutboxStatus.SENT
                return message
        return None

    async def mark_as_failed(self, _db: Any, outbox_id: int, error: str, max_retries: int = 3) -> Any | None:
        self.mark_failed_calls.append((outbox_id, error, max_retries))
        for message in self.messages:
            if message.id == outbox_id:
                message.status = SystemOutboxStatus.RETRY_WAIT
                message.last_error = error
                return message
        return None

    async def mark_as_blocked_by_device_busy(
        self,
        _db: Any,
        outbox_id: int,
        *,
        blocked_device_id: int | None,
        blocked_workline_id: int | None = None,
        reason: str = "DEVICE_BUSY",
        last_error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> Any | None:
        call = {
            "outbox_id": outbox_id,
            "blocked_device_id": blocked_device_id,
            "blocked_workline_id": blocked_workline_id,
            "reason": reason,
            "last_error": last_error,
            "detail": dict(detail or {}),
        }
        self.blocked_resource_calls.append(call)
        if self.block_resource_wait_returns_none:
            return None
        for message in self.messages:
            if message.id == outbox_id:
                message.status = SystemOutboxStatus.RETRY_WAIT
                message.blocked_reason = reason
                message.blocked_device_id = blocked_device_id
                message.blocked_workline_id = blocked_workline_id
                message.last_error = last_error
                message.blocked_detail_json = dict(detail or {})
                return message
        return None


async def _no_workline_messages(_db: Any, _limit: int) -> dict[str, int]:
    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


def _outbox(**overrides: Any) -> SimpleNamespace:
    canonical = CanonicalPayload.from_projection({"operation_key": "bin-operation:trace-001"})
    values = {
        "id": 1,
        "dispatch_key": "handling:bin-operation:trace-001:move:1",
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "target_code": "WMS_RCS_BIN_OPERATION",
        "payload_json": {"operation_key": "bin-operation:trace-001"},
        "canonical_payload_bytes": canonical.body,
        "payload_hash": canonical.sha256,
        "status": SystemOutboxStatus.NEW,
        "attempt_count": 0,
        "next_retry_at": None,
        "last_error": None,
        "operation_domain": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_sends_external_http_and_marks_sent() -> None:
    message = _outbox()
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    request = sender.await_args.args[0]
    assert isinstance(request, ExternalHttpDispatchRequest)
    assert request.endpoint.url == "http://wms-rcs/api/wes/transport-request"
    assert request.body == message.canonical_payload_bytes
    assert repo.mark_dispatching_calls == [1]
    assert repo.mark_sent_calls == [1]
    assert message.status == SystemOutboxStatus.SENT
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_marks_failed_when_external_http_fails() -> None:
    message = _outbox(id=2)
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=False)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert repo.mark_failed_calls == [(2, "Dispatch failed", 3)]
    assert message.status == SystemOutboxStatus.RETRY_WAIT
    assert message.last_error == "Dispatch failed"


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_reclaims_stale_dispatching_message() -> None:
    message = _outbox(
        id=3,
        status=SystemOutboxStatus.DISPATCHING,
        next_retry_at=datetime(2026, 5, 22, 7, 59, 0),
    )
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    sender.assert_awaited_once()
    assert repo.mark_dispatching_calls == [3]
    assert message.status == SystemOutboxStatus.SENT


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_delegates_workline_domain_to_workline_governance() -> None:
    repo = FakeSystemOutboxRepository([])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_workline_dispatcher(_db: Any, limit: int = 50) -> dict[str, int]:
        assert _db is db
        assert limit == 5
        return {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=fake_workline_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_dispatching_calls == []


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_excludes_rack_domain_from_generic_http_dispatch() -> None:
    message = _outbox(id=5, operation_domain="RACK", target_code="WMS_RCS_RACK_OPERATION")
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert repo.pending_filters[-1]["exclude_operation_domains"] == ("WORKLINE", "RACK")
    sender.assert_not_awaited()
    assert repo.mark_dispatching_calls == []


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_delegates_device_command_to_device_gateway() -> None:
    message = _outbox(id=4, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND)
    repo = FakeSystemOutboxRepository([message])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_device_dispatcher(_db: Any, outbox: Any) -> bool:
        assert _db is db
        assert outbox is message
        return True

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_parks_device_command_resource_wait() -> None:
    message = _outbox(
        id=6,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_code="ARM01",
        workline_id=45,
        blocked_detail_json={},
    )
    repo = FakeSystemOutboxRepository([message])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_device_dispatcher(_db: Any, _outbox: Any) -> bool:
        raise _DeviceCommandGovernanceError(
            domain="ORCHESTRATION",
            code="DEVICE_BUSY",
            message="设备 ARM01 实时状态忙，拒绝命令派发",
            device_id=7,
            device_code="ARM01",
            detail={"device_code": "ARM01", "last_probe_result": "BUSY"},
        )

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_failed_calls == []
    assert repo.blocked_resource_calls == [
        {
            "outbox_id": 6,
            "blocked_device_id": 7,
            "blocked_workline_id": 45,
            "reason": "DEVICE_BUSY",
            "last_error": "设备 ARM01 实时状态忙，拒绝命令派发",
            "detail": {"device_code": "ARM01", "last_probe_result": "BUSY"},
        }
    ]
    assert message.status == SystemOutboxStatus.RETRY_WAIT
    assert message.blocked_reason == "DEVICE_BUSY"


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_reraises_non_resource_wait_runtime_error() -> None:
    message = _outbox(id=7, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND)
    repo = FakeSystemOutboxRepository([message])
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_device_dispatcher(_db: Any, _outbox: Any) -> bool:
        raise RuntimeError("device gateway exploded")

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    with pytest.raises(RuntimeError, match="device gateway exploded"):
        await dispatcher.dispatch(db, limit=10)

    assert repo.mark_failed_calls == []
    assert repo.blocked_resource_calls == []


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_counts_resource_wait_fencing_as_skipped() -> None:
    message = _outbox(id=8, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND, workline_id=45)
    repo = FakeSystemOutboxRepository([message])
    repo.block_resource_wait_returns_none = True
    db = SimpleNamespace(commit=AsyncMock())

    async def fake_device_dispatcher(_db: Any, _outbox: Any) -> bool:
        raise _DeviceCommandGovernanceError(
            domain="ORCHESTRATION",
            code="DEVICE_STATUS_PRECHECK_WAIT",
            message="设备 ARM01 实时状态查询暂不可用",
            device_id=7,
            device_code="ARM01",
            detail={"device_code": "ARM01", "last_probe_result": "STATUS_WAIT"},
        )

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_failed_calls == []
    assert repo.blocked_resource_calls[0]["reason"] == "DEVICE_STATUS_PRECHECK_WAIT"
