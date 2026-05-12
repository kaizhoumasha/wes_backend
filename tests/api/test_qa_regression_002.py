from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.v1 import operation as operation_api


class _OperationServiceStub:
    async def replay_inbox(self, *_args: Any, **_kwargs: Any) -> object:
        raise ValueError("Inbox 不存在: 999999999")

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
        payload=operation_api.ReplayInboxRequest(reason="QA invalid id", operator_id="qa"),
        db=object(),  # type: ignore[arg-type]
    )

    assert response["code"] == "3000"
    assert response["message"] == "Inbox 不存在: 999999999"


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
