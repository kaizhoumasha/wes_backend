"""
认证模块测试

测试 JWT 认证和 RBAC 权限控制功能
"""

from datetime import datetime, timedelta

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.app.admin.models import User
from src.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    jwt_decode,
    verify_password,
)

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
        assert token_data.access_token_expire_time > datetime.now()

    @pytest.mark.asyncio()
    async def test_create_refresh_token(self):
        """测试：创建刷新令牌"""
        user_id = 1
        session_uuid = "test-session-uuid"
        refresh_data = await create_refresh_token(session_uuid, user_id)

        assert refresh_data.refresh_token is not None
        assert isinstance(refresh_data.refresh_token, str)
        assert refresh_data.refresh_token_expire_time > datetime.now()

    def test_jwt_decode_success(self):
        """测试：解析 JWT 成功"""
        from src.core.security import jwt_encode

        payload = {
            "sub": "123",
            "session_uuid": "test-uuid",
            "exp": (datetime.now() + timedelta(hours=1)).timestamp(),
        }
        token = jwt_encode(payload)
        decoded = jwt_decode(token)

        assert decoded.id == 123
        assert decoded.session_uuid == "test-uuid"

    def test_jwt_decode_expired(self):
        """测试：解析过期的 JWT"""
        from src.core.security import jwt_encode

        payload = {
            "sub": "123",
            "session_uuid": "test-uuid",
            "exp": (datetime.now() - timedelta(hours=1)).timestamp(),
        }
        token = jwt_encode(payload)

        with pytest.raises(Exception) as exc_info:
            jwt_decode(token)
        assert "已过期" in str(exc_info.value) or "expired" in str(exc_info.value).lower()


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
        assert "access_token" in data
        assert "refresh_token" in data
        assert "user" in data
        assert data["user"]["username"] == "testuser"

    @pytest.mark.asyncio()
    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        """测试：登录失败 - 错误密码"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "wrongpass"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio()
    async def test_login_user_not_found(self, client: AsyncClient):
        """测试：登录失败 - 用户不存在"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "testpass123"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio()
    async def test_logout_success(self, auth_client: AsyncClient):
        """测试：登出成功"""
        response = await auth_client.post("/api/v1/auth/logout")

        assert response.status_code == status.HTTP_200_OK
        assert "登出成功" in response.json()["message"]


# ==================== RBAC 权限测试 ====================


class TestRBAC:
    """RBAC 权限测试"""

    @pytest.mark.asyncio()
    async def test_user_without_permission(self, auth_client: AsyncClient):
        """测试：无权限用户访问受保护资源"""
        # 假设有一个需要 "user:delete" 权限的端点
        response = await auth_client.delete("/api/v1/users/1")

        # 应该返回 403 Forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio()
    async def test_superuser_has_all_permissions(self, db: AsyncSession, client: AsyncClient):
        """测试：超级用户拥有所有权限"""
        from src.core.security import create_access_token

        # 创建超级用户
        superuser = User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("admin123"),
            is_superuser=True,
            is_active=True,
        )
        db.add(superuser)
        await db.commit()

        # 创建令牌
        token_data = await create_access_token(superuser.id)
        token = token_data.access_token

        # 使用超级用户令牌访问
        response = await client.delete(
            "/api/v1/users/1",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 超级用户应该能访问（虽然可能因为用户不存在返回 404，但不应该是 403）
        assert response.status_code != status.HTTP_403_FORBIDDEN


# ==================== 集成测试 ====================


class TestAuthIntegration:
    """认证集成测试"""

    @pytest.mark.asyncio()
    async def test_full_auth_flow(self, client: AsyncClient):
        """测试：完整的认证流程"""
        # 1. 登录
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )
        assert login_response.status_code == status.HTTP_200_OK
        login_data = login_response.json()
        access_token = login_data["access_token"]

        # 2. 使用令牌访问受保护资源
        protected_response = await client.get(
            "/api/v1/users/1",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert protected_response.status_code == status.HTTP_200_OK

        # 3. 登出
        logout_response = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert logout_response.status_code == status.HTTP_200_OK

        # 4. 登出后令牌应该失效
        protected_response2 = await client.get(
            "/api/v1/users/1",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # 应该返回 401 Unauthorized
        assert protected_response2.status_code == status.HTTP_401_UNAUTHORIZED


# ==================== Fixtures ====================


@pytest.fixture
async def client(db_session: AsyncSession):
    """创建测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def auth_client(client: AsyncClient, test_user: User):
    """创建已认证的测试客户端"""
    # 登录获取令牌
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["access_token"]

    # 创建带认证的客户端
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
async def test_user(db: AsyncSession):
    """创建测试用户"""
    from src.core.security import get_password_hash

    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=get_password_hash("testpass123"),
        is_active=True,
        is_superuser=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
