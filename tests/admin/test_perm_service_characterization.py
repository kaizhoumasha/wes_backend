"""
PermissionService._query_user_ids_by_permission_id 特性测试

TDD 重构原则：在重构前锁定现有行为

重构后：Service 调用 Repository 方法，测试验证 Repository 被正确调用
"""

import pytest
from unittest.mock import AsyncMock

from src.app.admin.services.perm_service import PermissionService


class TestQueryUserIdsByPermissionId:
    """_query_user_ids_by_permission_id 方法特性测试"""

    @pytest.fixture
    def service(self) -> PermissionService:
        """创建 PermissionService 实例"""
        return PermissionService()

    @pytest.mark.asyncio
    async def test_calls_repository_with_correct_parameters(self, service: PermissionService):
        """验证 Service 调用 Repository 方法并传递正确参数"""
        # Arrange
        mock_db = AsyncMock()
        # Mock Repository 方法
        service.repo.get_user_ids_by_permission_id = AsyncMock(return_value={1, 2, 3})

        # Act
        result = await service._query_user_ids_by_permission_id(mock_db, permission_id=42)

        # Assert
        service.repo.get_user_ids_by_permission_id.assert_called_once_with(mock_db, 42)
        assert result == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_returns_empty_set_from_repository(self, service: PermissionService):
        """验证返回空集合时行为正确"""
        # Arrange
        mock_db = AsyncMock()
        service.repo.get_user_ids_by_permission_id = AsyncMock(return_value=set())

        # Act
        result = await service._query_user_ids_by_permission_id(mock_db, permission_id=1)

        # Assert
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_user_ids_from_repository(self, service: PermissionService):
        """验证返回用户 ID 集合时行为正确"""
        # Arrange
        mock_db = AsyncMock()
        expected_ids = {10, 20, 30}
        service.repo.get_user_ids_by_permission_id = AsyncMock(return_value=expected_ids)

        # Act
        result = await service._query_user_ids_by_permission_id(mock_db, permission_id=5)

        # Assert
        assert result == expected_ids

    @pytest.mark.asyncio
    async def test_delegates_to_repository(self, service: PermissionService):
        """验证方法委托给 Repository"""
        # Arrange
        mock_db = AsyncMock()
        service.repo.get_user_ids_by_permission_id = AsyncMock(return_value={1})

        # Act
        await service._query_user_ids_by_permission_id(mock_db, permission_id=1)

        # Assert - 验证调用了 Repository 方法而不是 db.execute
        service.repo.get_user_ids_by_permission_id.assert_called_once()
        # db.execute 不应该被直接调用
        mock_db.execute.assert_not_called()