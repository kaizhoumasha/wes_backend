"""
测试软删除功能

验证软删除的完整实现：
1. 查询方法自动过滤已删除记录
2. soft_delete() 方法正常工作
3. restore() 方法正常工作
4. get_deleted() 方法正常工作
5. permanent_delete() 方法正常工作
"""

from datetime import datetime

import pytest

from src.core.mixins import SoftDeleteMixin

# ==================== Mixin 功能测试 ====================


class TestSoftDeleteMixin:
    """测试 SoftDeleteMixin 的功能"""

    def test_soft_delete_mixin_attributes(self):
        """测试 SoftDeleteMixin 的属性"""

        # 创建一个简单的类来测试
        class Article(SoftDeleteMixin):
            def __init__(self, **kwargs):
                self.deleted_by = kwargs.get("deleted_by")
                self.deleted_at = kwargs.get("deleted_at")
                self.is_deleted = kwargs.get("is_deleted", False)

        article = Article()

        # 验证初始状态
        assert article.is_deleted is False
        assert article.deleted_by is None
        assert article.deleted_at is None

    def test_soft_delete_method(self):
        """测试 soft_delete() 方法"""
        from src.utils.timezone import timezone

        class Article(SoftDeleteMixin):
            def __init__(self, **kwargs):
                self.deleted_by = kwargs.get("deleted_by")
                self.deleted_at = kwargs.get("deleted_at")
                self.is_deleted = kwargs.get("is_deleted", False)

        article = Article()

        # 调用 soft_delete
        article.soft_delete(deleted_by=100)

        # 验证状态
        assert article.is_deleted is True
        assert article.deleted_by == 100
        assert article.deleted_at is not None
        assert isinstance(article.deleted_at, datetime)

    def test_restore_method(self):
        """测试 restore() 方法"""

        class Article(SoftDeleteMixin):
            def __init__(self, **kwargs):
                self.deleted_by = kwargs.get("deleted_by")
                self.deleted_at = kwargs.get("deleted_at")
                self.is_deleted = kwargs.get("is_deleted", False)

        article = Article()
        article.soft_delete(deleted_by=100)

        # 调用 restore
        article.restore()

        # 验证状态
        assert article.is_deleted is False
        assert article.deleted_by is None
        assert article.deleted_at is None

    def test_soft_delete_without_deleted_by(self):
        """测试没有 deleted_by 字段的软删除"""

        class Article(SoftDeleteMixin):
            def __init__(self, **kwargs):
                self.deleted_at = kwargs.get("deleted_at")
                self.is_deleted = kwargs.get("is_deleted", False)

        article = Article()

        # 调用 soft_delete（没有 deleted_by 字段）
        article.soft_delete(deleted_by=None)

        # 验证状态
        assert article.is_deleted is True
        assert article.deleted_at is not None


# ==================== 集成测试说明 ====================

"""
集成测试需要数据库环境，建议在测试数据库中运行：

1. 创建测试表
2. 插入测试数据
3. 测试软删除流程
4. 验证数据状态
5. 测试恢复功能
6. 测试永久删除

手动验证步骤：

# 1. 创建支持软删除的模型
from sqlmodel import Field, SQLModel
from src.core.mixins import SoftDeleteMixin, DataTableMixin

class Article(SoftDeleteMixin, DataTableMixin, table=True):
    __tablename__ = "articles"
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)

# 2. 使用 API 进行操作
# 创建文章
POST /articles
{"title": "测试文章"}

# 获取文章列表（应该能看到创建的文章）
POST /articles/query
{"limit": 10, "offset": 0}

# 软删除文章
DELETE /articles/1

# 再次获取文章列表（应该看不到已删除的文章）
POST /articles/query
{"limit": 10, "offset": 0}

# 获取回收站列表（应该能看到已删除的文章）
GET /articles/trash?limit=10&offset=0

# 恢复文章
POST /articles/1/restore

# 获取文章列表（应该能看到恢复的文章）
POST /articles/query
{"limit": 10, "offset": 0}

# 永久删除
DELETE /articles/1?permanent=true

# 尝试获取文章（应该返回 404）
GET /articles/1
"""


if __name__ == "__main__":
    # 运行单元测试
    pytest.main([__file__, "-v"])
