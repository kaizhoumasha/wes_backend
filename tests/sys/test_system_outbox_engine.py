from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.runtime.orchestration.services.device_command_gateway import _DeviceCommandGovernanceError
from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.dispatch_concurrency import (
    DispatchBucketKey,
    DispatchBucketPolicy,
    DispatchClaimBatch,
    DispatchClaimMetrics,
    DispatchLeaseClaim,
)
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.services import SystemOutboxEngine as SystemOutboxDispatcher
from tests.support.external_http import StaticTestCredentialProvider, frozen_outbox_namespace


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

    async def begin_physical_dispatch(
        self,
        _db: Any,
        outbox_id: int,
        *,
        lease_owner_token: str,
        lease_seconds: int,
    ) -> Any | None:
        assert lease_seconds > 0
        return next(
            (
                message
                for message in self.messages
                if message.id == outbox_id
                and message.status is SystemOutboxStatus.DISPATCHING
                and message.lease_owner_token == lease_owner_token
            ),
            None,
        )

    async def get_by_id_for_update(self, _db: Any, outbox_id: int) -> Any | None:
        return next((message for message in self.messages if message.id == outbox_id), None)

    async def mark_as_sent(self, _db: Any, outbox_id: int, *, lease_owner_token: str) -> Any | None:
        assert lease_owner_token
        self.mark_sent_calls.append(outbox_id)
        for message in self.messages:
            if message.id == outbox_id and message.status == SystemOutboxStatus.DISPATCHING:
                message.status = SystemOutboxStatus.SENT
                return message
        return None

    async def mark_as_failed(
        self,
        _db: Any,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
        *,
        lease_owner_token: str,
    ) -> Any | None:
        assert lease_owner_token
        self.mark_failed_calls.append((outbox_id, error, max_retries))
        for message in self.messages:
            if message.id == outbox_id:
                message.status = SystemOutboxStatus.RETRY_WAIT
                message.last_error = error
                return message
        return None

    async def mark_as_unknown(self, _db: Any, outbox_id: int, error: str, *, lease_owner_token: str) -> Any | None:
        assert lease_owner_token
        for message in self.messages:
            if message.id == outbox_id and message.status == SystemOutboxStatus.DISPATCHING:
                message.status = SystemOutboxStatus.UNKNOWN
                message.last_error = error
                return message
        return None

    async def mark_as_terminal_failure(
        self, _db: Any, outbox_id: int, error: str, *, lease_owner_token: str
    ) -> Any | None:
        assert lease_owner_token
        for message in self.messages:
            if message.id == outbox_id and message.status == SystemOutboxStatus.DISPATCHING:
                message.status = SystemOutboxStatus.FAILED
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
        lease_owner_token: str | None = None,
    ) -> Any | None:
        assert lease_owner_token
        call = {
            "outbox_id": outbox_id,
            "blocked_device_id": blocked_device_id,
            "blocked_workline_id": blocked_workline_id,
            "reason": reason,
            "last_error": last_error,
            "detail": dict(detail or {}),
            "lease_owner_token": lease_owner_token,
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


class FakeDispatchAttemptService:
    def __init__(self) -> None:
        self.attempt = SimpleNamespace(status="DISPATCHING", attempt_no=1)
        self.created: list[Any] = []
        self.finalized: list[dict[str, Any]] = []

    async def create_attempt(self, _db: Any, *, outbox: Any, auto_commit: bool) -> Any:
        assert auto_commit is False
        self.created.append(outbox)
        return self.attempt

    async def finalize_external_http_attempt_record(self, _db: Any, **kwargs: Any) -> Any:
        assert kwargs["auto_commit"] is False
        self.finalized.append(kwargs)
        return kwargs["attempt"]

    async def finalize_attempt_record(self, _db: Any, **kwargs: Any) -> Any:
        assert kwargs["auto_commit"] is False
        self.finalized.append(kwargs)
        return kwargs["attempt"]


class FakeFairDispatchScheduler:
    def __init__(self, repository: FakeSystemOutboxRepository) -> None:
        self.repository = repository
        self.claim_calls: list[dict[str, Any]] = []

    async def claim(self, _db: Any, **kwargs: Any) -> DispatchClaimBatch:
        self.claim_calls.append(dict(kwargs))
        excluded_domains = tuple(kwargs.get("exclude_operation_domains") or ())
        messages = [
            message
            for message in self.repository.messages
            if (
                message.status in {SystemOutboxStatus.NEW, SystemOutboxStatus.RETRY_WAIT}
                or (
                    message.status == SystemOutboxStatus.DISPATCHING
                    and message.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP
                    and getattr(message, "lease_expires_at", None) is not None
                    and message.lease_expires_at <= datetime(2026, 5, 22, 8, 0, 0)
                )
            )
            and getattr(message, "operation_domain", None) not in excluded_domains
        ][: int(kwargs["limit"])]
        policy = DispatchBucketPolicy()
        claims = []
        for message in messages:
            owner = f"test-owner:{message.id}"
            expires_at = datetime(2026, 5, 22, 8, 5, 0)
            message.status = SystemOutboxStatus.DISPATCHING
            message.lease_owner_token = owner
            message.lease_expires_at = expires_at
            claims.append(
                DispatchLeaseClaim(
                    outbox=message,
                    bucket=DispatchBucketKey("test.profile", "test.operation"),
                    lease_owner_token=owner,
                    lease_expires_at=expires_at,
                    policy=policy,
                )
            )
        return DispatchClaimBatch(
            claims=tuple(claims),
            metrics=DispatchClaimMetrics(0, len(claims), 0, None, (), (), ()),
        )


class NoopEffectTransportBridge:
    async def record_result(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class RecordingEffectTransportBridge:
    def __init__(self) -> None:
        self.record_result = AsyncMock()


async def _no_workline_messages(_db: Any, _limit: int) -> dict[str, int]:
    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


def _outbox(**overrides: Any) -> SimpleNamespace:
    canonical = CanonicalPayload.from_projection({"operation_key": "bin-operation:trace-001"})
    values = {
        "id": 1,
        "dispatch_key": "handling:bin-operation:trace-001:move:1",
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "target_code": "WMS_INVENTORY_TRANSFER",
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
    if values["dispatch_type"] is SystemOutboxDispatchType.EXTERNAL_HTTP:
        target_code = values.pop("target_code")
        projection = values.pop("payload_json")
        values.pop("canonical_payload_bytes")
        values.pop("payload_hash")
        target_url = (
            "http://wms-rcs/api/wes/transport-request"
            if target_code == "WMS_INVENTORY_TRANSFER"
            else "https://wms.example/effects"
        )
        return frozen_outbox_namespace(projection, target_code=target_code, target_url=target_url, **values)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_sends_external_http_and_marks_sent() -> None:
    message = _outbox()
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        effect_transport_bridge=NoopEffectTransportBridge(),
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    request = sender.await_args.args[0]
    assert isinstance(request, ExternalHttpDispatchRequest)
    assert request.endpoint.url == "http://wms-rcs/api/wes/transport-request"
    assert request.body == message.canonical_payload_bytes
    assert repo.mark_dispatching_calls == []
    assert repo.mark_sent_calls == [1]
    assert message.status == SystemOutboxStatus.SENT
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_appends_late_transport_evidence_after_unknown_fence() -> None:
    message = _outbox(id=11)
    repo = FakeSystemOutboxRepository([message])
    bridge = RecordingEffectTransportBridge()
    transport_result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )

    async def sender(_request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        message.status = SystemOutboxStatus.UNKNOWN
        message.dispatch_started_at = datetime(2026, 5, 22, 8, 1, 0)
        return transport_result

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        effect_transport_bridge=bridge,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    bridge.record_result.assert_awaited_once()
    assert bridge.record_result.await_args.kwargs["dispatch_key"] == message.dispatch_key
    assert bridge.record_result.await_args.kwargs["result"] is transport_result


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_persists_full_claim_batch_attempts_before_first_send() -> None:
    messages = [
        _outbox(id=31, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND),
        _outbox(id=32, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND),
    ]
    repo = FakeSystemOutboxRepository(messages)
    events: list[str] = []

    class AttemptService(FakeDispatchAttemptService):
        async def create_attempt(self, db: Any, *, outbox: Any, auto_commit: bool) -> Any:
            events.append(f"attempt:{outbox.id}")
            return await super().create_attempt(db, outbox=outbox, auto_commit=auto_commit)

    async def device_sender(_db: Any, outbox: Any) -> bool:
        events.append(f"send:{outbox.id}")
        return True

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=AttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=device_sender,
    )

    result = await dispatcher.dispatch(SimpleNamespace(commit=AsyncMock()), limit=10)

    assert result == {"dispatched": 2, "success": 2, "failed": 0, "skipped": 0}
    assert events[:2] == ["attempt:31", "attempt:32"]


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_rechecks_claim_before_each_physical_send() -> None:
    messages = [
        _outbox(id=41, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND),
        _outbox(id=42, dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND),
    ]
    repo = FakeSystemOutboxRepository(messages)
    sent_ids: list[int] = []

    async def device_sender(_db: Any, outbox: Any) -> bool:
        sent_ids.append(outbox.id)
        if outbox.id == 41:
            messages[1].status = SystemOutboxStatus.CANCELLED
        return True

    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=device_sender,
    )

    result = await dispatcher.dispatch(SimpleNamespace(commit=AsyncMock()), limit=10)

    assert sent_ids == [41]
    assert result == {"dispatched": 2, "success": 1, "failed": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_marks_failed_when_external_http_fails() -> None:
    message = _outbox(id=2)
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
            error_message="connection refused",
        )
    )
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        external_http_sender=sender,
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        effect_transport_bridge=NoopEffectTransportBridge(),
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}
    assert repo.mark_failed_calls == [(2, "connection refused", 3)]
    assert message.status == SystemOutboxStatus.RETRY_WAIT
    assert message.last_error == "connection refused"


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_bridges_persisted_transport_evidence_to_effect_reducer() -> None:
    message = _outbox(id=22, dispatch_key="effect-dispatch-22")
    repo = FakeSystemOutboxRepository([message])
    sender_result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    effect_bridge = SimpleNamespace(record_result=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        external_http_sender=AsyncMock(return_value=sender_result),
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        effect_transport_bridge=effect_bridge,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result["success"] == 1
    effect_bridge.record_result.assert_awaited_once()
    call = effect_bridge.record_result.await_args
    assert call.kwargs["dispatch_key"] == "effect-dispatch-22"
    assert call.kwargs["attempt_no"] == 1
    assert call.kwargs["result"] is sender_result
    assert call.kwargs["retry_exhausted"] is False


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_reclaims_stale_non_http_dispatching_message() -> None:
    message = _outbox(
        id=3,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="DEVICE-LEASE-3",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="old-owner",
        lease_expires_at=datetime(2026, 5, 22, 7, 59, 0),
    )
    repo = FakeSystemOutboxRepository([message])
    device_sender = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        device_command_dispatcher=device_sender,
        dispatch_attempt_service=FakeDispatchAttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}
    device_sender.assert_awaited_once_with(db, message)
    assert repo.mark_dispatching_calls == []
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
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        workline_domain_dispatcher=fake_workline_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_dispatching_calls == []


@pytest.mark.asyncio
async def test_system_outbox_dispatcher_excludes_rack_domain_from_generic_http_dispatch() -> None:
    message = _outbox(id=5, operation_domain="RACK", target_code="WMS_FULFILLMENT_REQUEST_RACK_TRANSPORT")
    repo = FakeSystemOutboxRepository([message])
    sender = AsyncMock(
        return_value=ExternalHttpTransportResult.accepted(
            http_status_code=200,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )
    )
    db = SimpleNamespace(commit=AsyncMock())
    scheduler = FakeFairDispatchScheduler(repo)
    dispatcher = SystemOutboxDispatcher(
        outbox_repository=repo,
        dispatch_scheduler=scheduler,
        external_http_sender=sender,
        workline_domain_dispatcher=_no_workline_messages,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
    assert scheduler.claim_calls[-1]["exclude_operation_domains"] == ("WORKLINE", "RACK")
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
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=FakeDispatchAttemptService(),
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
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=FakeDispatchAttemptService(),
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
            "lease_owner_token": "test-owner:6",
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
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=FakeDispatchAttemptService(),
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
        dispatch_scheduler=FakeFairDispatchScheduler(repo),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        workline_domain_dispatcher=_no_workline_messages,
        device_command_dispatcher=fake_device_dispatcher,
    )

    result = await dispatcher.dispatch(db, limit=10)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    assert repo.mark_failed_calls == []
    assert repo.blocked_resource_calls[0]["reason"] == "DEVICE_STATUS_PRECHECK_WAIT"
