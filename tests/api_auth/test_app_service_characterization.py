"""
APIAppService 分层架构违规修复表征测试

目的：在重构前锁定 _query_by_app_id 和 assign_permissions 方法的行为
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api_auth.models import APIApplication
from src.app.api_auth.services.app_service import APIAppService


class TestQueryByAppIdCharacterization:
    """_query_by_app_id 方法表征测试 - 锁定当前行为"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def valid_app(self):
        """创建有效应用"""
        app = MagicMock(spec=APIApplication)
        app.id = 1
        app.app_id = "test_app_123"
        app.app_secret_encrypted = "encrypted_secret"
        app.status = MagicMock()
        app.status.value = "active"
        app.is_deleted = False
        return app

    @pytest.fixture
    def deleted_app(self):
        """创建已删除应用"""
        app = MagicMock(spec=APIApplication)
        app.id = 2
        app.app_id = "deleted_app"
        app.is_deleted = True
        return app

    @pytest.mark.asyncio
    async def test_query_by_app_id_returns_app_when_exists(
        self, mock_db: AsyncMock, valid_app
    ):
        """测试：app_id 存在时返回应用"""
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = valid_app
        mock_db.execute.return_value = mock_result

        # Act
        service = APIAppService()
        result = await service._query_by_app_id(mock_db, "test_app_123")

        # Assert
        assert result == valid_app
        assert result.app_id == "test_app_123"
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_by_app_id_returns_none_when_not_found(
        self, mock_db: AsyncMock
    ):
        """测试：app_id 不存在时返回 None"""
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Act
        service = APIAppService()
        result = await service._query_by_app_id(mock_db, "nonexistent")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_query_by_app_id_excludes_deleted_apps(
        self, mock_db: AsyncMock, deleted_app
    ):
        """测试：已删除应用被排除（通过 is_deleted=False 条件）"""
        # Arrange - 模拟查询返回 None（已删除的应用被过滤）
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Act
        service = APIAppService()
        result = await service._query_by_app_id(mock_db, "deleted_app")

        # Assert - 应该返回 None，因为查询条件包含 is_deleted.is_(False)
        assert result is None


class TestAssignPermissionsCharacterization:
    """assign_permissions 方法表征测试 - 锁定当前行为"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def mock_cache(self):
        """创建模拟缓存"""
        cache = AsyncMock()
        cache.delete = AsyncMock()
        return cache

    @pytest.fixture
    def valid_app(self):
        """创建有效应用"""
        app = MagicMock(spec=APIApplication)
        app.id = 1
        app.app_id = "test_app"
        return app

    @pytest.mark.asyncio
    async def test_assign_permissions_success(
        self, mock_db: AsyncMock, mock_cache, valid_app
    ):
        """测试：成功分配权限"""
        # Arrange
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = valid_app

        service = APIAppService()
        service.repo = mock_repo

        # Act
        await service.assign_permissions(mock_db, mock_cache, 1, [100, 200])

        # Assert - 验证 Repository 方法被调用
        mock_repo.get_by_id.assert_called_once_with(mock_db, 1)
        mock_repo.assign_permissions.assert_called_once_with(mock_db, 1, [100, 200])
        mock_cache.delete.assert_called()

    @pytest.mark.asyncio
    async def test_assign_permissions_app_not_found(
        self, mock_db: AsyncMock, mock_cache
    ):
        """测试：应用不存在时抛出 ValueError"""
        # Arrange
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = None

        service = APIAppService()
        service.repo = mock_repo

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await service.assign_permissions(mock_db, mock_cache, 999, [100])

        assert "应用 999 不存在" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_assign_permissions_empty_list(
        self, mock_db: AsyncMock, mock_cache, valid_app
    ):
        """测试：空权限列表调用 Repository 处理"""
        # Arrange
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = valid_app

        service = APIAppService()
        service.repo = mock_repo

        # Act
        await service.assign_permissions(mock_db, mock_cache, 1, [])

        # Assert - Repository 方法被调用，传入空列表
        mock_repo.get_by_id.assert_called_once_with(mock_db, 1)
        mock_repo.assign_permissions.assert_called_once_with(mock_db, 1, [])
