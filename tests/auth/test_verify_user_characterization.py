"""
AuthService.verify_user 表征测试

目的：在重构后验证 verify_user 方法正确调用 Repository

注意：User.roles 是在 models/__init__.py 中定义的 SQLAlchemy relationship，
不是 Pydantic 字段，因此测试中使用 MagicMock 模拟 User 对象。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.admin.models import User
from src.app.auth.services.auth_service import AuthService
from src.core.exceptions import AuthException, InvalidCredentialsException


class TestVerifyUserCharacterization:
    """verify_user 方法表征测试 - 验证 Repository 调用"""

    @pytest.fixture
    def valid_user(self):
        """创建有效用户 - 使用 MagicMock 模拟 roles 关系"""
        user = MagicMock(spec=User)
        user.id = 1
        user.username = "testuser"
        user.email = "test@example.com"
        user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$test_salt$test_hash"
        user.is_deleted = False
        user.is_superuser = False
        user.roles = []  # SQLAlchemy relationship，不是 Pydantic 字段
        return user

    @pytest.fixture
    def deleted_user(self):
        """创建已删除用户"""
        user = MagicMock(spec=User)
        user.id = 2
        user.username = "deleted_user"
        user.email = "deleted@example.com"
        user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$test_salt$test_hash"
        user.is_deleted = True
        user.is_superuser = False
        user.roles = []
        return user

    @pytest.mark.asyncio
    async def test_verify_user_calls_repository(self, valid_user):
        """测试：verify_user 调用 UserRepository.get_by_username_with_roles"""
        # Arrange
        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=valid_user)

            with patch(
                "src.app.auth.services.auth_service.verify_password", return_value=True
            ):
                # Act
                result = await AuthService.verify_user(mock_db, "testuser", "correct_password")

            # Assert
            mock_repo.get_by_username_with_roles.assert_called_once_with(mock_db, "testuser")
            assert result == valid_user

    @pytest.mark.asyncio
    async def test_verify_user_fails_with_nonexistent_user(self):
        """测试：用户不存在时抛出 InvalidCredentialsException"""
        # Arrange
        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=None)

            # Act & Assert
            with pytest.raises(InvalidCredentialsException) as exc_info:
                await AuthService.verify_user(mock_db, "nonexistent", "any_password")

            assert "用户名或密码错误" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_verify_user_fails_with_deleted_user(self, deleted_user):
        """测试：已删除用户抛出 AuthException"""
        # Arrange
        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=deleted_user)

            # Act & Assert
            with pytest.raises(AuthException) as exc_info:
                await AuthService.verify_user(mock_db, "deleted_user", "any_password")

            assert "用户已被禁用" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_verify_user_fails_with_wrong_password(self, valid_user):
        """测试：密码错误时抛出 InvalidCredentialsException"""
        # Arrange
        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=valid_user)

            # 模拟密码验证失败
            with patch(
                "src.app.auth.services.auth_service.verify_password", return_value=False
            ):
                # Act & Assert
                with pytest.raises(InvalidCredentialsException) as exc_info:
                    await AuthService.verify_user(mock_db, "testuser", "wrong_password")

                assert "用户名或密码错误" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_verify_user_returns_user_with_roles(self, valid_user):
        """测试：验证用户时返回带 roles 的用户"""
        # Arrange - 添加角色
        role = MagicMock()
        role.name = "admin"
        valid_user.roles = [role]

        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=valid_user)

            with patch(
                "src.app.auth.services.auth_service.verify_password", return_value=True
            ):
                # Act
                result = await AuthService.verify_user(mock_db, "testuser", "correct_password")

            # Assert - 验证 roles 已加载
            assert result.roles is not None
            assert len(result.roles) == 1
            assert result.roles[0].name == "admin"

    @pytest.mark.asyncio
    async def test_verify_user_does_not_call_db_execute(self, valid_user):
        """测试：verify_user 不直接调用 db.execute"""
        # Arrange
        mock_db = AsyncMock()
        with patch(
            "src.app.auth.services.auth_service.user_repository"
        ) as mock_repo:
            mock_repo.get_by_username_with_roles = AsyncMock(return_value=valid_user)

            with patch(
                "src.app.auth.services.auth_service.verify_password", return_value=True
            ):
                # Act
                await AuthService.verify_user(mock_db, "testuser", "password")

            # Assert - db.execute 不应该被直接调用
            mock_db.execute.assert_not_called()
