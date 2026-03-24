from unittest.mock import AsyncMock, patch

import pytest

from src.app.auth.services.auth_service import AuthService


class TestAuthServiceSessionCharacterization:
    @pytest.mark.asyncio
    async def test_get_active_sessions_supports_bytes_payload_from_redis(self):
        current_user_id = 1
        session_key = "auth:user_session:1:test-session"
        session_payload = (
            b'{"jti":"access-jti","iat":1710000000,"extra":{"username":"testuser","email":"test@example.com"}}'
        )

        async def scan_iter(*, match: str):
            assert match == f"auth:user_session:{current_user_id}:*"
            yield session_key

        class MockRedis:
            def __init__(self):
                self.mget = AsyncMock(return_value=[session_payload])

            def scan_iter(self, *, match: str):
                return scan_iter(match=match)

        with patch("src.app.auth.services.auth_service.is_redis_available", return_value=True), patch(
            "src.app.auth.services.auth_service.get_redis",
            return_value=MockRedis(),
        ):
            result = await AuthService.get_active_sessions(current_user_id)

        assert result.total == 1
        assert len(result.sessions) == 1
        assert result.sessions[0].session_uuid == "test-session"
        assert result.sessions[0].jti == "access-jti"
        assert result.sessions[0].device_info == {
            "username": "testuser",
            "email": "test@example.com",
        }
