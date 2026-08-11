"""Sandbox external callback 的 RuntimeInbox 幂等与时间合同。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxAcceptResult
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
from src.app.workline.models.workline import WorkLineRunMode
from src.utils.timezone import timezone


class _NestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Db:
    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()


class _InboxRepository:
    def __init__(self) -> None:
        self.record: Any | None = None
        self.add_count = 0

    async def get_by_source_event_identity(self, _db: Any, **_identity: Any) -> Any | None:
        return self.record

    async def add_received(self, _db: Any, data: dict[str, Any]) -> Any:
        self.add_count += 1
        self.record = SimpleNamespace(id=81, **data)
        return self.record


def _build_service(*, inbox_repo: Any) -> WorklineOperationService:
    outbox = SimpleNamespace(
        id=71,
        dispatch_key="external:wms:inventory-updated:1",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=53,
        workline_id=45,
        created_at=datetime(2026, 7, 6, 8, 0, 0, 456000),
        payload_json={"trace_id": "trace-001", "callback_type": "WMS_INVENTORY_UPDATED"},
    )
    session = SimpleNamespace(
        id=53,
        workline_id=45,
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_type="EXTERNAL_HTTP",
        trace_id="trace-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    return WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        outbox_repo=cast("Any", SimpleNamespace(get_by_dispatch_key=AsyncMock(return_value=outbox))),
        session_repo=cast("Any", SimpleNamespace(get_by_id=AsyncMock(return_value=session))),
        workline_repo=cast("Any", SimpleNamespace(get_for_update=AsyncMock(return_value=workline))),
    )


def _status_hint_payload() -> dict[str, object]:
    return {
        "data": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-rack-supply-001",
            "dispatch_key": "external:wms:inventory-updated:1",
        }
    }


@pytest.mark.asyncio
async def test_sandbox_external_callback_rejects_removed_payload_type_before_runtime_inbox() -> None:
    """沙箱不得用允许参数覆盖 payload 内的旧终态类型。"""

    repo = _InboxRepository()
    service = _build_service(inbox_repo=repo)

    with pytest.raises(ValueError, match="callback_type does not match payload"):
        await service.submit_sandbox_external_callback(
            _Db(),
            dispatch_key="external:wms:inventory-updated:1",
            callback_type="WMS_INVENTORY_UPDATED",
            payload={"callback_type": "WMS_RACK_TASK_RESULT"},
            auto_commit=False,
        )

    assert repo.add_count == 0


@pytest.mark.asyncio
async def test_default_external_callback_retry_is_stable_without_legacy_lifecycle_side_effect() -> None:
    repo = _InboxRepository()
    service = _build_service(inbox_repo=repo)

    first = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:wms:inventory-updated:1",
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_status_hint_payload(),
        auto_commit=False,
    )
    second = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:wms:inventory-updated:1",
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_status_hint_payload(),
        auto_commit=False,
    )

    assert first is second
    assert repo.add_count == 1


@pytest.mark.asyncio
async def test_external_callback_closes_dispatching_lease_shape() -> None:
    repo = _InboxRepository()
    service = _build_service(inbox_repo=repo)
    outbox = service.outbox_repo.get_by_dispatch_key.return_value
    outbox.status = SystemOutboxStatus.DISPATCHING
    outbox.lease_owner_token = "sandbox-external-owner"
    outbox.lease_expires_at = timezone.now_for_db() + timedelta(minutes=5)

    await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key=outbox.dispatch_key,
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_status_hint_payload(),
        auto_commit=False,
    )

    assert outbox.status == SystemOutboxStatus.SENT
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == "sandbox-external-owner"


@pytest.mark.asyncio
async def test_duplicate_accept_result_skips_external_lifecycle_side_effect() -> None:
    existing = SimpleNamespace(id=81)
    repo = SimpleNamespace(get_by_source_event_identity=AsyncMock(return_value=None))
    service = _build_service(inbox_repo=repo)
    service._accept_runtime_message = AsyncMock(  # type: ignore[method-assign]
        return_value=RuntimeInboxAcceptResult(record=existing, created=False)
    )

    result = await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:wms:inventory-updated:1",
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_status_hint_payload(),
        auto_commit=False,
    )

    assert result is existing


@pytest.mark.asyncio
async def test_accept_runtime_message_preserves_received_at_milliseconds() -> None:
    repo = SimpleNamespace()
    service = WorklineOperationService(inbox_repo=cast("Any", repo))
    record = SimpleNamespace(id=81)
    service.runtime_inbox_service.accept_received = AsyncMock(
        return_value=RuntimeInboxAcceptResult(record=record, created=True)
    )
    received_at = datetime(2026, 7, 6, 8, 0, 0, 456000, tzinfo=UTC)

    result = await service._accept_runtime_message(
        _Db(),
        kind="EXTERNAL_HTTP",
        event_type="WMS_INVENTORY_UPDATED",
        payload={"message_type": "EXTERNAL_HTTP"},
        source_event_id="event-001",
        received_at=received_at,
    )

    assert result.record is record
    kwargs = service.runtime_inbox_service.accept_received.await_args.kwargs
    assert kwargs["now_ms"] == 1783324800456


@pytest.mark.asyncio
async def test_external_callback_forwards_explicit_received_at_to_accept_helper() -> None:
    existing = SimpleNamespace(id=81)
    repo = SimpleNamespace()
    service = _build_service(inbox_repo=repo)
    service._accept_runtime_message = AsyncMock(  # type: ignore[method-assign]
        return_value=RuntimeInboxAcceptResult(record=existing, created=True)
    )
    received_at = datetime(2026, 7, 6, 8, 0, 0, 456000, tzinfo=UTC)

    await service.submit_sandbox_external_callback(
        _Db(),
        dispatch_key="external:wms:inventory-updated:1",
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_status_hint_payload(),
        timestamp=received_at,
        auto_commit=False,
    )

    assert service._accept_runtime_message.await_args.kwargs["received_at"] is received_at
