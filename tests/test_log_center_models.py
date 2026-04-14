from __future__ import annotations

from datetime import datetime

from src.app.api_auth.models.api_access_log import APIAccessLogResponse
from src.app.sys.models.audit_log import AuditLogResponse, OperaStatus


def test_audit_log_response_exposes_structured_audit_dimensions() -> None:
    response = AuditLogResponse.model_validate(
        {
            "id": 1,
            "trace_id": "trace-1",
            "username": "auditor",
            "method": "PUT",
            "title": "UPDATE user",
            "path": "/repository/user",
            "ip": "127.0.0.1",
            "country": "CN",
            "region": "Shanghai",
            "city": "Shanghai",
            "user_agent": "pytest",
            "os": "macOS",
            "browser": "Chrome",
            "device": "Desktop",
            "args": {"model": "user", "operation": "update"},
            "status": OperaStatus.SUCCESS,
            "code": "200",
            "msg": None,
            "cost_time": 0.25,
            "opera_time": datetime(2026, 4, 13, 12, 0, 0),
            "object_type": "user",
            "action": "update",
            "object_id": "42",
            "change_summary": "更新字段：username、status",
        }
    )

    assert response.object_type == "user"
    assert response.action == "update"
    assert response.object_id == "42"
    assert response.change_summary == "更新字段：username、status"


def test_api_access_log_response_exposes_created_at() -> None:
    response = APIAccessLogResponse.model_validate(
        {
            "id": 1,
            "app_id": "app-1",
            "app_name": "Test App",
            "request_id": "req-1",
            "method": "GET",
            "path": "/api/v1/demo",
            "status_code": 200,
            "response_time_ms": 128,
            "ip_address": "127.0.0.1",
            "user_agent": "pytest",
            "error_message": None,
            "created_at": datetime(2026, 4, 13, 12, 30, 0),
        }
    )

    assert response.created_at == datetime(2026, 4, 13, 12, 30, 0)
