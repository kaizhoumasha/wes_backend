from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.app.sys.models.api_access_log import (
    APIAccessLog,
    APIAccessLogCreate,
    APIAccessLogResponse,
    APIAccessLogSummary,
)
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


def test_api_access_log_summary_is_nullable_utc_and_excludes_details() -> None:
    data = {
        "id": 1,
        "system_id": "ecs",
        "direction": "OUTBOUND",
        "method": "POST",
        "path": "/command",
        "status_code": None,
        "response_time_ms": None,
        "created_at": datetime(2026, 4, 13, 12, 30),
        "details": {"private": "snapshot"},
    }
    summary = APIAccessLogSummary.model_validate(data)
    assert summary.status_code is None
    assert summary.response_time_ms is None
    assert summary.created_at.utcoffset().total_seconds() == 0
    assert "details" not in summary.model_dump()
    assert APIAccessLogResponse.model_validate(data).details == data["details"]
    assert APIAccessLogResponse.model_validate({**data, "details": None}).details is None


def test_api_access_log_model_has_only_unified_metadata_and_required_indexes() -> None:
    table = APIAccessLog.__table__
    assert table.schema == "wes_sys"
    assert table.name == "api_access_logs"
    assert set(table.columns.keys()) == set(APIAccessLogSummary.model_fields) | {"details", "updated_at"}
    assert table.c.status_code.nullable
    assert table.c.response_time_ms.nullable
    indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
    assert {("created_at", "id"), ("system_id", "direction", "created_at"), ("request_id",), ("trace_id",)} <= indexes


@pytest.mark.parametrize("override", [{"direction": "BOTH"}, {"response_time_ms": -1}, {"system_id": "x" * 65}])
def test_api_access_log_create_validates_public_dimensions(override) -> None:
    with pytest.raises(ValidationError):
        APIAccessLogCreate.model_validate(
            {"system_id": "ecs", "direction": "INBOUND", "method": "POST", "path": "/event", **override}
        )
