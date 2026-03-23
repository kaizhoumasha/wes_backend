"""
组合 Mixin

提供常用的 Mixin 组合,简化模型定义
"""

# type: ignore - PrimaryKeyMixin 是动态生成的类

from src.core.mixins.audit import AuditMixin
from src.core.mixins.base import BaseMixin
from src.core.mixins.optimistic_lock import OptimisticLockMixin
from src.core.mixins.repr import ReprMixin
from src.core.mixins.soft_delete import SoftDeleteMixin
from src.core.mixins.timestamp import TimestampMixin

# ==================== 组合 Mixin ====================


class StandardMixin(
    ReprMixin,
    TimestampMixin,
    BaseMixin,
):
    """
    基础模型 Mixin

    组合了最常用的 Mixin:时间戳 + repr
    适用于大多数业务模型

    使用示例:
        class User(StandardMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            name: str
    """


class AuditableMixin(AuditMixin, StandardMixin):
    """
    审计模型 Mixin

    组合了 标准模型 + 审计字段
    适用于需要审计追踪的业务模型

    使用示例:
        class Article(AuditableMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建时记录创建人
        article = Article(title="测试", created_by=user_id)
    """


class EnterpriseMixin(AuditableMixin, OptimisticLockMixin):
    """
    业务模型 Mixin

    组合 Mixin:审计 + 乐观锁
    适用于基础业务模型
    """


class FullModelMixin(EnterpriseMixin, SoftDeleteMixin):
    """
    完整模型 Mixin

    组合 Mixin:业务模型 + 软删除
    适用于需要完整功能的模型

    使用示例:
        class Article(EnterpriseMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建
        article = Article(title="测试", created_by=1)

        # 更新
        article.title = "新标题"
        article.updated_by = 2

        # 软删除(自动设置 deleted_by)
        article.soft_delete(deleted_by=3)
    """
