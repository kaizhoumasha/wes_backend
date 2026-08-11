"""QA Callback 审计响应码回归测试。"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from src.app.sys.models.audit_log import OperaStatus
from tests.api import callback_test_support


@pytest.mark.asyncio
async def test_external_validation_audit_records_actual_http_status() -> None:
    # Regression: ISSUE-002 — Callback 失败审计不得把所有 4xx 固定记录为 500
    # Found by /qa on 2026-08-11
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-08-11.md
    db_session = callback_test_support.db_session.__wrapped__()
    request = callback_test_support.build_request.__wrapped__()(
        body={},
        path="/api/v1/callback/external",
    )

    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch("src.app.callback.v1.callback.get_request_id", return_value="req-audit-status"),
    ):
        from src.app.callback.v1.callback import callback_external

        response = await callback_external(request=request, db=db_session)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    audit_kwargs = callback_test_support._await_kwargs(audit_log)
    assert audit_kwargs["status"] == OperaStatus.FAIL
    assert audit_kwargs["code"] == "400"


@pytest.mark.asyncio
async def test_result_validation_audit_records_actual_http_status() -> None:
    db_session = callback_test_support.db_session.__wrapped__()
    request = callback_test_support.build_request.__wrapped__()(
        body={},
        path="/api/v1/callback/result",
    )

    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch(
            "src.app.callback.services.callback_ingress_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        patch("src.app.callback.v1.callback.get_request_id", return_value="req-result-audit-status"),
    ):
        from src.app.callback.v1.callback import callback_result

        response = await callback_result(request=request, db=db_session)

    assert isinstance(response, dict)
    audit_kwargs = callback_test_support._await_kwargs(audit_log)
    assert audit_kwargs["status"] == OperaStatus.FAIL
    assert audit_kwargs["code"] == "200"


@pytest.mark.asyncio
async def test_event_validation_audit_records_actual_http_status() -> None:
    db_session = callback_test_support.db_session.__wrapped__()
    request = callback_test_support.build_request.__wrapped__()(
        body={},
        path="/api/v1/callback/event",
    )
    http_response = Response()

    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
        patch(
            "src.app.callback.services.callback_ingress_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        patch("src.app.callback.v1.callback.get_request_id", return_value="req-event-audit-status"),
    ):
        from src.app.callback.v1.callback import callback_event

        response = await callback_event(request=request, db=db_session, response=http_response)

    assert isinstance(response, dict)
    assert http_response.status_code == 200
    audit_kwargs = callback_test_support._await_kwargs(audit_log)
    assert audit_kwargs["status"] == OperaStatus.FAIL
    assert audit_kwargs["code"] == "200"
