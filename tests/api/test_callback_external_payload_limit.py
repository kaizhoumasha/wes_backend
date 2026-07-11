"""External callback RuntimeInbox payload bytes HTTP 边界。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import func, select

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.core.conf import settings
from tests.api.callback_test_support import create_external_payload


def _request(payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/callback/external",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


@pytest.mark.asyncio
async def test_external_payload_too_large_returns_http_413_without_runtime_inbox_row(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超限 external payload 必须返回 HTTP 413，且 RuntimeInbox 零落库。"""
    monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", 1)

    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ) as callback_log,
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch(
            "src.app.callback.services.callback_orchestration_service.callback_orchestration_service."
            "_resolve_rack_task_service"
        ) as rack_service_resolver,
    ):
        rack_service_resolver.return_value.record_callback_from_external_http = AsyncMock()
        from src.app.callback.v1.callback import callback_external

        with pytest.raises(HTTPException) as exc_info:
            await callback_external(request=_request(create_external_payload()), db=db_session)

    assert exc_info.value.status_code == 413
    logged_body = callback_log.await_args.kwargs["request_body"]
    audited_body = audit_log.await_args.kwargs["args"]
    assert logged_body == audited_body
    assert logged_body["callback_type"] == "AGV_TASK_RESULT"
    assert logged_body["actual_bytes"] > logged_body["max_bytes"]
    assert "data" not in logged_body
    count = await db_session.scalar(select(func.count()).select_from(RuntimeInbox))
    assert count == 0


@pytest.mark.parametrize("payload_changed", [False, True], ids=["same-hash", "different-hash"])
@pytest.mark.asyncio
async def test_existing_external_identity_still_rejects_oversized_payload_before_duplicate_or_conflict(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    payload_changed: bool,
) -> None:
    """已有 identity 不能绕过 payload bytes 守卫或优先返回 hash conflict。"""
    source_event_id = "agv-existing-identity-payload-limit"
    accepted_payload = create_external_payload(
        data={"source_event_id": source_event_id, "to_location": "STATION_OUTPUT1"}
    )
    incoming_payload = dict(accepted_payload)
    if payload_changed:
        incoming_payload["result"] = "FAILED"

    monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", 1024 * 1024)
    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ) as callback_log,
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch(
            "src.app.callback.services.callback_orchestration_service.callback_orchestration_service."
            "_resolve_rack_task_service"
        ) as rack_service_resolver,
    ):
        rack_service_resolver.return_value.record_callback_from_external_http = AsyncMock()
        from src.app.callback.v1.callback import callback_external

        await callback_external(request=_request(accepted_payload), db=db_session)
        existing = await db_session.scalar(select(RuntimeInbox))
        assert existing is not None
        original_evidence = (
            dict(existing.payload_json),
            existing.payload_hash,
            existing.status,
            existing.received_at,
            existing.attempt_count,
        )
        callback_log.reset_mock()
        audit_log.reset_mock()
        monkeypatch.setattr(settings, "runtime_inbox_payload_max_bytes", 1)

        with pytest.raises(HTTPException) as exc_info:
            await callback_external(request=_request(incoming_payload), db=db_session)

    assert exc_info.value.status_code == 413
    logged_body = callback_log.await_args.kwargs["request_body"]
    audited_body = audit_log.await_args.kwargs["args"]
    assert logged_body == audited_body
    assert logged_body["actual_bytes"] > logged_body["max_bytes"]
    assert "data" not in logged_body
    await db_session.refresh(existing)
    assert (
        dict(existing.payload_json),
        existing.payload_hash,
        existing.status,
        existing.received_at,
        existing.attempt_count,
    ) == original_evidence
    count = await db_session.scalar(select(func.count()).select_from(RuntimeInbox))
    assert count == 1
