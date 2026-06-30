"""Phase 3 body HMAC canonical signature tests."""

from __future__ import annotations

import hashlib
import hmac


def test_body_hmac_canonical_signature_includes_nonce_body_hash_and_app_id() -> None:
    """External callback 签名必须覆盖 method/path/timestamp/nonce/body hash/app_id。"""

    from src.core.api_security import calculate_body_hmac_signature

    body = b'{"event_id":"EVT-1","result":"OK"}'
    body_hash = hashlib.sha256(body).hexdigest()

    signature = calculate_body_hmac_signature(
        app_secret="secret-1",
        method="POST",
        path="/api/v1/callback/event",
        timestamp="1782843000",
        nonce="nonce-1",
        body_sha256=body_hash,
        app_id="ecs-app",
    )

    canonical = "POST\n/api/v1/callback/event\n1782843000\nnonce-1\n" + body_hash + "\necs-app"
    expected = hmac.new(b"secret-1", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    assert signature == expected


def test_nonce_guard_accepts_once_then_rejects_replay() -> None:
    """nonce 在 TTL 窗口内只能消费一次。"""

    from src.core.api_security import InMemoryNonceReplayGuard

    guard = InMemoryNonceReplayGuard()

    assert guard.consume(app_id="ecs-app", nonce="nonce-1", now=100, ttl_seconds=300) is True
    assert guard.consume(app_id="ecs-app", nonce="nonce-1", now=101, ttl_seconds=300) is False
    assert guard.consume(app_id="ecs-app", nonce="nonce-1", now=401, ttl_seconds=300) is True


def test_callback_path_requires_body_hmac_headers() -> None:
    """callback 路径必须升级到 body HMAC header 集合。"""

    from src.core.api_security import missing_body_hmac_headers

    headers = {
        "X-App-ID": "ecs-app",
        "X-Timestamp": "1782843000",
        "X-Signature": "sig",
    }

    assert missing_body_hmac_headers("/api/v1/callback/event", headers) == ["X-Nonce", "X-Body-SHA256"]
    assert missing_body_hmac_headers("/api/v1/api-auth/applications/try/invoke", headers) == []


def test_callback_path_uses_strict_timestamp_skew() -> None:
    """callback HMAC timestamp 偏差窗口固定为 30 秒。"""

    from src.core.api_security import signature_clock_skew_seconds

    assert signature_clock_skew_seconds("/api/v1/callback/event") == (30, 30)
    assert signature_clock_skew_seconds("/api/v1/api-auth/applications/try/invoke") == (300, 60)
