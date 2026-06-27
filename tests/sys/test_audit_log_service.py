from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services.audit_service import AuditLogService


@pytest.mark.asyncio
async def test_create_audit_log_extracts_structured_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuditLogService()
    create_mock = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(service._repo_base, "create", create_mock)
    monkeypatch.setattr("src.app.sys.services.audit_service.get_request_info", dict)
    monkeypatch.setattr("src.app.sys.services.audit_service.get_request_id", lambda: "trace-1")
    monkeypatch.setattr("src.app.sys.services.audit_service.get_current_username", lambda: "auditor")

    await service.create_audit_log(
        object(),
        method="PUT",
        title="UPDATE user",
        path="/repository/user",
        args={
            "model": "user",
            "operation": "update",
            "record_id": "42",
            "changes": {
                "username": {"old": "old", "new": "new"},
                "status": {"old": "disabled", "new": "enabled"},
            },
        },
        status=OperaStatus.SUCCESS,
        code="200",
        cost_time=0.25,
    )

    payload = create_mock.await_args.args[1]
    assert payload["object_type"] == "user"
    assert payload["action"] == "update"
    assert payload["object_id"] == "42"
    assert payload["change_summary"] == "更新字段：username、status"


@pytest.mark.asyncio
async def test_create_operation_log_generates_delete_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AuditLogService()
    create_audit_log_mock = AsyncMock(return_value={"id": 2})
    monkeypatch.setattr(service, "create_audit_log", create_audit_log_mock)
    monkeypatch.setattr("src.utils.audit.get_request_method", lambda: None)

    await service.create_operation_log(
        object(),
        operation="delete",
        model_name="user",
        record_id=9,
        data={"username": "alice", "status": "enabled"},
        success=False,
        error_msg="boom",
        cost_time=0.5,
    )

    args = create_audit_log_mock.await_args.kwargs["args"]
    assert args["model"] == "user"
    assert args["operation"] == "delete"
    assert args["record_id"] == "9"
    assert args["changes"] == {"username": "alice", "status": "enabled"}
