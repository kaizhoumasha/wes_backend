"""API 认证依赖测试。"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.core.api_security import APIAppContext, require_api_auth, verify_api_auth
from src.core.conf import settings


def build_request(*, headers: dict[str, str]) -> Request:
    request = MagicMock()
    request.headers = headers
    request.method = "POST"
    request.url = MagicMock()
    request.url.path = "/api/v1/callback/result"
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return cast("Request", request)


class TestVerifyAPIAuth:
    @pytest.mark.asyncio
    async def test_verify_api_auth_skips_signed_request_when_skip_api_auth_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", True)
        monkeypatch.setattr(settings, "APP_DEBUG", True)

        request = build_request(
            headers={
                "X-App-ID": "app_test",
                "X-Timestamp": "1702627200",
                "X-Signature": "signed-value",
            }
        )
        db = AsyncMock()
        cache = AsyncMock()

        with (
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(side_effect=AssertionError("skip auth should not query api app")),
            ) as mock_get_app,
            patch(
                "src.core.api_security.encryption_service.decrypt",
                side_effect=AssertionError("skip auth should not decrypt app secret"),
            ) as mock_decrypt,
        ):
            result = await verify_api_auth(request, db, cache)

        assert result is None
        mock_get_app.assert_not_awaited()
        mock_decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_api_auth_raises_when_skip_api_auth_used_in_non_debug_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", True)
        monkeypatch.setattr(settings, "APP_DEBUG", False)

        request = build_request(headers={})
        db = AsyncMock()
        cache = AsyncMock()

        with pytest.raises(RuntimeError, match="SKIP_API_AUTH=True is not allowed when APP_DEBUG=False"):
            await verify_api_auth(request, db, cache)


class TestRequireAPIAuth:
    @pytest.mark.asyncio
    async def test_require_api_auth_returns_dev_context_when_skip_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", True)
        monkeypatch.setattr(settings, "APP_DEBUG", True)

        result = await require_api_auth(None)

        assert result == APIAppContext(
            app_id="dev_skip",
            app_name="dev_skip",
            app_type="dev",
            permissions={"*"},
        )
