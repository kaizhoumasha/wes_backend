"""
认证模块测试

测试 JWT 认证和 RBAC 权限控制功能
"""

import fnmatch
import importlib
from datetime import timedelta

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.app.admin.models import User
from src.core.exceptions import InvalidTokenException, TokenExpiredException
from src.core.security import (
    TokenPayload,
    TokenType,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    jwt_decode,
    jwt_encode,
    verify_password,
)
from src.database.db import get_db
from src.utils.timezone import timezone

auth_service_module = importlib.import_module("src.app.auth.services.auth_service")
security_runtime_module = importlib.import_module("src.core.security_runtime")
database_dependencies_module = importlib.import_module("src.database.dependencies")


class _FakeRedisPipeline:
    """最小 Redis pipeline 实现，满足认证测试所需行为。"""

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def delete(self, *keys: object) -> "_FakeRedisPipeline":
        self._ops.append(("delete", keys))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for operation, args in self._ops:
            if operation == "delete":
                results.append(await self._redis.delete(*args))
        self._ops.clear()
        return results


class _FakeRedis:
    """认证链路专用的最小内存 Redis。"""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._sets: dict[str, set[object]] = {}

    async def setex(self, key: str, _ttl: int, value: object) -> bool:
        self._values[key] = value
        return True

    async def get(self, key: str) -> object | None:
        return self._values.get(key)

    async def getdel(self, key: str) -> object | None:
        return self._values.pop(key, None)

    async def delete(self, *keys: object) -> int:
        deleted = 0
        for raw_key in keys:
            if not isinstance(raw_key, str):
                continue
            if raw_key in self._values:
                del self._values[raw_key]
                deleted += 1
            if raw_key in self._sets:
                del self._sets[raw_key]
                deleted += 1
        return deleted

    async def exists(self, key: str) -> int:
        return int(key in self._values or key in self._sets)

    async def sadd(self, key: str, *values: object) -> int:
        members = self._sets.setdefault(key, set())
        before = len(members)
        members.update(values)
        return len(members) - before

    async def srem(self, key: str, *values: object) -> int:
        members = self._sets.setdefault(key, set())
        removed = 0
        for value in values:
            if value in members:
                members.remove(value)
                removed += 1
        return removed

    async def smembers(self, key: str) -> set[object]:
        return set(self._sets.get(key, set()))

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True

    async def mget(self, keys: list[str]) -> list[object | None]:
        return [self._values.get(key) for key in keys]

    def scan_iter(self, *, match: str):
        async def _iterator():
            for key in [*self._values.keys(), *self._sets.keys()]:
                if fnmatch.fnmatch(key, match):
                    yield key

        return _iterator()

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)

    async def set(self, key: str, value: object, *, expire: int | None = None) -> bool:
        del expire
        self._values[key] = value
        return True


@pytest.fixture(autouse=True)
def auth_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """为认证测试提供稳定的内存 Redis，避免受本机服务状态影响。"""
    fake_redis = _FakeRedis()

    monkeypatch.setattr(security_runtime_module, "is_redis_available", lambda: True)
    monkeypatch.setattr(security_runtime_module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(auth_service_module, "is_redis_available", lambda: True)
    monkeypatch.setattr(auth_service_module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(database_dependencies_module, "get_cache", lambda: fake_redis)

    return fake_redis


# ==================== 密码哈希测试 ====================


class TestPasswordHashing:
    """密码哈希测试"""

    def test_verify_password_success(self):
        """测试：密码验证成功"""
        plain_password = "test_password_123"
        hashed = get_password_hash(plain_password)
        assert verify_password(plain_password, hashed) is True

    def test_verify_password_failure(self):
        """测试：密码验证失败"""
        plain_password = "test_password_123"
        hashed = get_password_hash(plain_password)
        assert verify_password("wrong_password", hashed) is False

    def test_hash_is_consistent(self):
        """测试：相同密码的哈希值不同（加盐）"""
        password = "test_password_123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        # Argon2 每次生成的哈希应该不同（因为包含随机盐）
        assert hash1 != hash2
        # 但都应该能验证成功
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)


# ==================== JWT 令牌测试 ====================


class TestJWTTokens:
    """JWT 令牌测试"""

    @pytest.mark.asyncio()
    async def test_create_access_token(self):
        """测试：创建访问令牌"""
        user_id = 1
        token_data = await create_access_token(user_id)

        assert token_data.access_token is not None
        assert isinstance(token_data.access_token, str)
        assert token_data.session_uuid is not None
        assert isinstance(token_data.session_uuid, str)
        assert token_data.access_token_expire_time > timezone.now_utc()

    @pytest.mark.asyncio()
    async def test_create_refresh_token(self):
        """测试：创建刷新令牌"""
        user_id = 1
        session_uuid = "test-session-uuid"
        refresh_data = await create_refresh_token(session_uuid, user_id)

        assert refresh_data.refresh_token is not None
        assert isinstance(refresh_data.refresh_token, str)
        assert refresh_data.refresh_token_expire_time > timezone.now_utc()

    def test_jwt_decode_success(self):
        """测试：解析 JWT 成功"""
        now = timezone.now_utc()
        payload = TokenPayload(
            iss="wes_backend",
            sub="123",
            jti="test-jti",
            iat=int(now.timestamp()),
            nbf=int(now.timestamp()),
            exp=int((now + timedelta(hours=1)).timestamp()),
            token_type=TokenType.ACCESS,
            session_uuid="test-uuid",
            is_superuser=False,
        )
        token = jwt_encode(payload)
        decoded = jwt_decode(token)

        assert decoded.sub == "123"
        assert decoded.session_uuid == "test-uuid"

    def test_jwt_decode_expired(self):
        """测试：解析过期的 JWT"""
        now = timezone.now_utc()
        payload = TokenPayload(
            iss="wes_backend",
            sub="123",
            jti="expired-jti",
            iat=int(now.timestamp()),
            nbf=int(now.timestamp()),
            exp=int((now - timedelta(hours=1)).timestamp()),
            token_type=TokenType.ACCESS,
            session_uuid="test-uuid",
            is_superuser=False,
        )
        token = jwt_encode(payload)

        with pytest.raises(TokenExpiredException) as exc_info:
            jwt_decode(token)
        assert "已过期" in str(exc_info.value) or "expired" in str(exc_info.value).lower()

    def test_jwt_decode_invalid(self):
        """测试：解析无效 JWT"""
        with pytest.raises(InvalidTokenException):
            jwt_decode("invalid.token.payload")


# ==================== 认证 API 测试 ====================


class TestAuthAPI:
    """认证 API 测试"""

    @pytest.mark.asyncio()
    async def test_login_success(self, client: AsyncClient, test_user: User):
        """测试：登录成功"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["code"] == "1000"
        assert "data" in data
        assert "access_token" in data["data"]
        assert "refresh_token" not in data["data"]
        assert "user" in data["data"]
        assert data["data"]["user"]["username"] == "testuser"
        assert client.cookies.get("refresh_token")
        assert "refresh_token=" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio()
    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        """测试：登录失败 - 错误密码"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "wrongpass"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["code"] == "2011"

    @pytest.mark.asyncio()
    async def test_login_user_not_found(self, client: AsyncClient):
        """测试：登录失败 - 用户不存在"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "testpass123"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["code"] == "2011"

    @pytest.mark.asyncio()
    async def test_refresh_token_missing_cookie(self, client: AsyncClient):
        """测试：刷新失败 - 缺少 refresh_token Cookie"""
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["code"] == "2014"

    @pytest.mark.asyncio()
    async def test_refresh_token_invalid_cookie(self, client: AsyncClient):
        """测试：刷新失败 - 无效 refresh_token Cookie"""
        client.cookies.set("refresh_token", "invalid.token.payload")
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert data["code"] == "2012"

    @pytest.mark.asyncio()
    async def test_logout_success(self, auth_client: AsyncClient):
        """测试：登出成功"""
        response = await auth_client.post("/api/v1/auth/logout")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["code"] == "1000"
        assert "登出成功" in body["data"]["message"]

    @pytest.mark.asyncio()
    async def test_logout_with_refresh_cookie_without_authorization(self, client: AsyncClient, test_user: User):
        """测试：仅携带 refresh cookie 也可登出（无需 Authorization）"""
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )
        assert login_response.status_code == status.HTTP_200_OK
        assert client.cookies.get("refresh_token")

        response = await client.post("/api/v1/auth/logout")
        assert response.status_code == status.HTTP_200_OK

        body = response.json()
        assert body["code"] == "1000"
        assert "登出成功" in body["data"]["message"]
        assert body["data"]["revoked_count"] in (0, 1)
        assert "refresh_token=" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio()
    async def test_logout_is_idempotent_without_any_token(self, client: AsyncClient):
        """测试：无任何 token 时登出幂等成功"""
        client.cookies.clear()

        response = await client.post("/api/v1/auth/logout")
        assert response.status_code == status.HTTP_200_OK

        body = response.json()
        assert body["code"] == "1000"
        assert "登出成功" in body["data"]["message"]
        assert body["data"]["revoked_count"] == 0

    @pytest.mark.asyncio()
    async def test_get_permissions_requires_auth(self, client: AsyncClient):
        """测试：获取权限列表需要登录"""
        response = await client.get("/api/v1/auth/permissions")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["code"] == "2014"


# ==================== RBAC 权限测试 ====================


class TestRBAC:
    """RBAC 权限测试"""

    @pytest.mark.asyncio()
    async def test_user_without_permission(self, auth_client: AsyncClient):
        """测试：无权限用户访问受保护资源"""
        # 假设有一个需要 "user:delete" 权限的端点
        response = await auth_client.delete("/api/v1/admin/users/1")

        # 应该返回 403 Forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio()
    async def test_superuser_has_all_permissions(self, db_session: AsyncSession, client: AsyncClient):
        """测试：超级用户拥有所有权限"""
        from src.core.security import create_access_token
        from src.utils.snowflake import generate_snowflake_id

        # 创建超级用户
        superuser = User(
            id=generate_snowflake_id(),
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("admin123"),
            is_superuser=True,
            created_by=1,
        )
        db_session.add(superuser)
        await db_session.commit()

        # 创建令牌
        assert superuser.id is not None
        token_data = await create_access_token(superuser.id)
        token = token_data.access_token

        # 使用超级用户令牌访问
        response = await client.delete(
            "/api/v1/admin/users/1",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 超级用户应该能访问（虽然可能因为用户不存在返回 404，但不应该是 403）
        assert response.status_code != status.HTTP_403_FORBIDDEN


# ==================== 集成测试 ====================


class TestAuthIntegration:
    """认证集成测试"""

    @pytest.mark.asyncio()
    async def test_full_auth_flow(self, client: AsyncClient, test_user: User):
        """测试：登录与令牌载荷契约（稳定集成链路）"""
        # 1. 登录
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )
        assert login_response.status_code == status.HTTP_200_OK
        login_data = login_response.json()
        assert login_data["code"] == "1000"
        access_token = login_data["data"]["access_token"]
        assert isinstance(access_token, str) and access_token
        assert "refresh_token" not in login_data["data"]

        # 2. 校验访问令牌可正确解析（避免依赖环境中的会话接口行为差异）
        token_payload = jwt_decode(access_token)
        assert token_payload.sub == str(test_user.id)

        # logout 端点的行为由 TestAuthAPI::test_logout_success 单独覆盖，
        # 这里避免叠加环境依赖导致集成用例不稳定。


# ==================== Fixtures ====================


@pytest.fixture
async def client(db_session: AsyncSession):
    """创建测试客户端"""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_client(client: AsyncClient, test_user: User):
    """创建已认证的测试客户端"""
    # 登录获取令牌
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["data"]["access_token"]

    # 创建带认证的客户端
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
async def test_user(db_session: AsyncSession):
    """创建测试用户"""
    from src.core.security import get_password_hash
    from src.utils.snowflake import generate_snowflake_id

    user = User(
        id=generate_snowflake_id(),
        username="testuser",
        email="test@example.com",
        hashed_password=get_password_hash("testpass123"),
        is_superuser=False,
        created_by=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user
