"""API 认证依赖测试。"""

import hashlib
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.core.api_security import APIAppContext, require_api_auth, verify_api_auth
from src.core.conf import settings


def build_request(
    *,
    headers: dict[str, str],
    client_host: str = "127.0.0.1",
    path: str = "/api/v1/api-auth/applications/try/invoke",
    body: bytes = b"{}",
) -> Request:
    request = MagicMock()
    request.headers = headers
    request.method = "POST"
    request.url = MagicMock()
    request.url.path = path
    request.client = MagicMock()
    request.client.host = client_host
    request.body = AsyncMock(return_value=body)
    request.state = SimpleNamespace()
    return cast("Request", request)


def build_signed_headers() -> dict[str, str]:
    return {
        "X-App-ID": "app_test",
        "X-Timestamp": "1702627200",
        "X-Signature": "signed-value",
    }


def build_body_hmac_headers(*, body: bytes, nonce: str = "nonce-1") -> dict[str, str]:
    return {
        **build_signed_headers(),
        "X-Nonce": nonce,
        "X-Body-SHA256": hashlib.sha256(body).hexdigest(),
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

    @pytest.mark.asyncio
    async def test_verify_api_auth_does_not_consume_nonce_before_signature_passes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """body HMAC 签名失败时不能提前消费 nonce。"""

        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        body = b'{"event_id":"evt-001"}'
        request = build_request(
            headers=build_body_hmac_headers(body=body),
            path="/api/v1/callback/result",
            body=body,
        )
        db = AsyncMock()
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock(return_value=True)
        cache.set_if_absent = AsyncMock(return_value=True)

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app()),
            ),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=False),
            pytest.raises(Exception, match="签名验证失败"),
        ):
            await verify_api_auth(request, db, cache)

        cache.set.assert_not_awaited()
        cache.set_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_api_auth_rejects_replayed_nonce_via_atomic_consume(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """签名通过后 nonce 必须通过原子 SET NX 消费，重复消费直接拒绝。"""

        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        body = b'{"event_id":"evt-002"}'
        request = build_request(
            headers=build_body_hmac_headers(body=body),
            path="/api/v1/callback/result",
            body=body,
        )
        db = AsyncMock()
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock(return_value=True)
        cache.set_if_absent = AsyncMock(return_value=False)
        cache.incr_with_expire = AsyncMock(return_value=1)

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app()),
            ),
            patch("src.app.api_auth.services.get_app_permissions", new=AsyncMock(return_value={"api:callback:result"})),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=True),
            pytest.raises(Exception, match="nonce 已被使用"),
        ):
            await verify_api_auth(request, db, cache)

        cache.set_if_absent.assert_awaited_once_with("api_auth:nonce:app_test:nonce-1", "1", expire=300)
        cache.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_api_auth_fails_closed_when_nonce_store_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """callback nonce 去重存储不可用时必须 fail closed。"""

        monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
        body = b'{"event_id":"evt-003"}'
        request = build_request(
            headers=build_body_hmac_headers(body=body),
            path="/api/v1/callback/result",
            body=body,
        )
        db = AsyncMock()
        cache = AsyncMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock(return_value=False)
        cache.set_if_absent = AsyncMock(return_value=None)
        cache.incr_with_expire = AsyncMock(return_value=1)

        with (
            patch("time.time", return_value=1702627200),
            patch(
                "src.app.api_auth.services.api_app_service.get_by_app_id",
                new=AsyncMock(return_value=build_api_app()),
            ),
            patch("src.app.api_auth.services.get_app_permissions", new=AsyncMock(return_value={"api:callback:result"})),
            patch("src.core.api_security.encryption_service.decrypt", return_value="secret"),
            patch("src.app.api_auth.services.SignatureService.verify", return_value=True),
            pytest.raises(Exception, match="nonce 无法校验"),
        ):
            await verify_api_auth(request, db, cache)

        cache.set_if_absent.assert_awaited_once_with("api_auth:nonce:app_test:nonce-1", "1", expire=300)
        cache.set.assert_not_awaited()


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
