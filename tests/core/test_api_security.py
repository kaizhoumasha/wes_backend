"""API 认证依赖测试。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.core.api_security import APIAppContext, require_api_auth, verify_api_auth
from src.core.conf import settings


def build_request(*, headers: dict[str, str], client_host: str = "127.0.0.1") -> Request:
    request = MagicMock()
    request.headers = headers
    request.method = "POST"
    request.url = MagicMock()
    request.url.path = "/api/v1/callback/result"
    request.client = MagicMock()
    request.client.host = client_host
    request.state = SimpleNamespace()
    return cast("Request", request)


def build_signed_headers() -> dict[str, str]:
    return {
        "X-App-ID": "app_test",
        "X-Timestamp": "1702627200",
        "X-Signature": "signed-value",
    }


def build_api_app(*, ip_whitelist: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        app_id="app_test",
        app_name="测试应用",
        app_type="ECS",
        app_secret_encrypted="encrypted",
        status="active",
        expires_at=None,
        ip_whitelist=ip_whitelist,
        rate_limit_per_minute=100,
        rate_limit_per_hour=5000,
    )


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

    @pytest.mark.asyncio
    async def test_verify_api_auth_ip_whitelist_uses_trusted_proxy_client_ip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = build_request(
            headers={
                **build_signed_headers(),
                "X-Real-IP": "203.0.113.10",
            },
            client_host="10.0.0.10",
        )
        db = AsyncMock()
        cache = AsyncMock()
        cache.incr_with_expire = AsyncMock(return_value=1)

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app(ip_whitelist=["203.0.113.10"])),
            ),
            patch("src.app.api_auth.services.get_app_permissions", new=AsyncMock(return_value={"api:callback:result"})),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.calculate", return_value="expected-signature"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=True),
        ):
            result = await verify_api_auth(request, db, cache)

        assert result == APIAppContext(
            app_id="app_test",
            app_name="测试应用",
            app_type="ECS",
            permissions={"api:callback:result"},
        )

    @pytest.mark.asyncio
    async def test_verify_api_auth_ip_whitelist_ignores_spoofed_forwarded_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = build_request(
            headers={
                **build_signed_headers(),
                "X-Forwarded-For": "203.0.113.10",
            },
            client_host="198.51.100.20",
        )
        db = AsyncMock()
        cache = AsyncMock()

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app(ip_whitelist=["203.0.113.10"])),
            ),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.calculate", return_value="expected-signature"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=True),
            pytest.raises(Exception, match=r"IP 198\.51\.100\.20 不在白名单中"),
        ):
            await verify_api_auth(request, db, cache)

    @pytest.mark.asyncio
    async def test_verify_api_auth_ip_whitelist_matches_equivalent_ipv6_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["2001:db8:ffff::1"])
        request = build_request(
            headers={
                **build_signed_headers(),
                "X-Forwarded-For": "2001:0db8::10, 2001:db8:ffff::1",
            },
            client_host="2001:db8:ffff::1",
        )
        db = AsyncMock()
        cache = AsyncMock()
        cache.incr_with_expire = AsyncMock(return_value=1)

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app(ip_whitelist=["2001:0db8::10"])),
            ),
            patch("src.app.api_auth.services.get_app_permissions", new=AsyncMock(return_value={"api:callback:result"})),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.calculate", return_value="expected-signature"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=True),
        ):
            result = await verify_api_auth(request, db, cache)

        assert result is not None
        assert result.app_id == "app_test"


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
