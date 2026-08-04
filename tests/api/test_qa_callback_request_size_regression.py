"""QA callback 请求体边界回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.conf import settings

# Regression: ISSUE-002 — callback 必须在 JSON 解析与日志落库前拒绝超限请求体
# Found by /qa on 2026-07-24


def _oversized_request() -> Request:
    chunks = [b'{"callback_type":"', b"x" * 128 + b'"}']
    index = 0

    async def receive() -> dict[str, object]:
        nonlocal index
        chunk = chunks[index]
        index += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks),
        }

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
async def test_external_callback_rejects_raw_body_before_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "callback_request_body_max_bytes", 64)
    db_session = AsyncMock(spec=AsyncSession)

    with (
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ) as callback_log,
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ) as audit_log,
    ):
        from src.app.callback.v1.callback import callback_external

        with pytest.raises(HTTPException) as exc_info:
            await callback_external(request=_oversized_request(), db=db_session)

    assert exc_info.value.status_code == 413
    callback_log.assert_not_awaited()
    audit_log.assert_not_awaited()
