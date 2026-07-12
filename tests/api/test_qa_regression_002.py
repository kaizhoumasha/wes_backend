from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Response

from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxAuditPersistenceFailed,
    RuntimeInboxConflict,
    RuntimeInboxNotFound,
    RuntimeInboxReplayNotAllowed,
)
from src.app.workline.v1 import operation as operation_api


class _OperationServiceStub:
    replay_kwargs: dict[str, Any] | None = None

    async def replay_inbox(self, *_args: Any, **_kwargs: Any) -> object:
        self.replay_kwargs = _kwargs
        raise RuntimeInboxNotFound(inbox_id=999999999)

    async def create_manual_operation(self, *_args: Any, **_kwargs: Any) -> object:
        raise ValueError("会话不存在: 999999999")


@pytest.mark.asyncio
async def test_replay_missing_inbox_returns_not_found_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay 不存在的 inbox 应返回资源不存在响应，而不是全局 500。"""

    # Regression: ISSUE-002 — replay invalid id returned code 5000.
    # Found by /qa on 2026-04-27
    # Report: .gstack/qa-reports/qa-report-localhost-2026-04-27.md
    monkeypatch.setattr(operation_api, "workline_operation_service", _OperationServiceStub())

    response = await operation_api.replay_inbox(
        inbox_id=999999999,
        payload=operation_api.ReplayInboxRequest(request_id="qa-replay-1", reason="QA invalid id"),
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        current_user_id=42,
    )

    assert response["code"] == "3000"
    assert response["message"] == "RuntimeInbox 不存在: 999999999"


def test_replay_request_requires_stable_request_id_and_rejects_operator_id() -> None:
    with pytest.raises(ValueError):
        operation_api.ReplayInboxRequest(reason="missing request id")

    payload = operation_api.ReplayInboxRequest(request_id="  replay-001  ", reason=" replay reason ")
    assert payload.request_id == "replay-001"
    assert not hasattr(payload, "operator_id")

    with pytest.raises(ValueError):
        operation_api.ReplayInboxRequest(request_id="replay-002", reason="reason", operator_id="forged")

    with pytest.raises(ValueError):
        operation_api.ReplayInboxRequest(request_id="x" * 101, reason="too long")

    max_length = operation_api.ReplayInboxRequest(request_id=f"  {'x' * 100}  ", reason="max")
    assert max_length.request_id == "x" * 100


@pytest.mark.asyncio
async def test_replay_uses_authenticated_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    replay = SimpleNamespace(
        id=5,
        kind="REPLAY_REQUEST",
        source_event_id="replay:4:req-1",
        trace_id="trace-1",
        session_id=3,
        workline_id=2,
        status="RECEIVED",
    )

    class _CapturingService:
        kwargs: dict[str, Any] | None = None

        async def replay_inbox(self, *_args: Any, **kwargs: Any) -> object:
            self.kwargs = kwargs
            return replay

    service = _CapturingService()
    monkeypatch.setattr(operation_api, "workline_operation_service", service)
    monkeypatch.setattr(operation_api, "_enqueue_runtime_inbox_processing", lambda: None)

    response = await operation_api.replay_inbox(
        inbox_id=4,
        payload=operation_api.ReplayInboxRequest(request_id="req-1", reason="manual replay"),
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        current_user_id=99,
    )

    assert response["code"] == "1000"
    assert service.kwargs == {
        "inbox_id": 4,
        "request_id": "req-1",
        "actor": "99",
        "reason": "manual replay",
    }


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeInboxNotFound(inbox_id=1), "3000"),
        (RuntimeInboxReplayNotAllowed(reason_code="SOURCE_NOT_DEAD_LETTER"), "4001"),
        (RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_NOT_FOUND"), "3000"),
        (RuntimeInboxReplayNotAllowed(reason_code="SOURCE_WORKLINE_INACTIVE"), "4001"),
        (RuntimeInboxReplayNotAllowed(reason_code="SOURCE_RECONCILIATION_PENDING"), "4001"),
        (
            RuntimeInboxConflict(
                provider_code="RUNTIME",
                event_type="REPLAY_REQUEST",
                source_event_id="replay:1:req",
                existing_payload_hash="old",
                incoming_payload_hash="new",
            ),
            "3012",
        ),
        (
            RuntimeInboxAuditPersistenceFailed(
                audit_event_type="RUNTIME_INBOX_MANUAL_REPLAY_CONFLICT",
                original_error=RuntimeError("audit storage unavailable"),
            ),
            "RUNTIME_INBOX_AUDIT_PERSISTENCE_FAILED",
        ),
    ],
)
@pytest.mark.asyncio
async def test_replay_maps_typed_domain_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
) -> None:
    class _FailingService:
        async def replay_inbox(self, *_args: Any, **_kwargs: Any) -> object:
            raise error

    monkeypatch.setattr(operation_api, "workline_operation_service", _FailingService())
    http_response = Response()
    response = await operation_api.replay_inbox(
        inbox_id=1,
        payload=operation_api.ReplayInboxRequest(request_id="req", reason="reason"),
        db=object(),  # type: ignore[arg-type]
        current_user_id=7,
        response=http_response,
    )

    assert response["code"] == expected_code
    if isinstance(error, RuntimeInboxAuditPersistenceFailed):
        expected_http_status = 503
    elif isinstance(error, RuntimeInboxConflict):
        expected_http_status = 409
    else:
        expected_http_status = 200
    assert http_response.status_code == expected_http_status


@pytest.mark.asyncio
async def test_manual_missing_session_returns_not_found_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """人工操作不存在的 session 应返回资源不存在响应，而不是全局 500。"""

    # Regression: ISSUE-002 — manual operation invalid id returned code 5000.
    # Found by /qa on 2026-04-27
    # Report: .gstack/qa-reports/qa-report-localhost-2026-04-27.md
    monkeypatch.setattr(operation_api, "workline_operation_service", _OperationServiceStub())

    response = await operation_api.create_manual_operation(
        session_id=999999999,
        payload=operation_api.ManualOperationRequest(
            operation="HOLD",
            operator_id="qa",
            reason="QA invalid id",
        ),
        db=object(),  # type: ignore[arg-type]
    )

    assert response["code"] == "3000"
    assert response["message"] == "会话不存在: 999999999"


def test_pending_outbox_response_does_not_treat_target_device_as_source() -> None:
    outbox = SimpleNamespace(
        id=1,
        session_id=2,
        workline_id=45,
        dispatch_key="device-command:CMD-001",
        dispatch_type="DEVICE_COMMAND",
        target_type="DEVICE",
        target_code="ARM03",
        status="SENT",
        payload_json={"device_code": "ARM03", "command_code": "CMD-001"},
    )

    response = operation_api._outbox_response(outbox)

    assert response["target_code"] == "ARM03"
    assert response["payload_json"]["device_code"] == "ARM03"
    assert response["source_device"] is None
