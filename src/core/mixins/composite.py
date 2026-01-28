"""
组合 Mixin

提供常用的 Mixin 组合,简化模型定义
"""

# type: ignore - PrimaryKeyMixin 是动态生成的类
from src.core.mixins.audit import AuditMixin
from src.core.mixins.optimistic_lock import OptimisticLockMixin
from src.core.mixins.primary_key import PrimaryKeyMixin  # type: ignore[misc]
from src.core.mixins.repr import ReprMixin
from src.core.mixins.soft_delete import SoftDeleteMixin
from src.core.mixins.timestamp import TimestampMixin

# ==================== 组合 Mixin ====================


class BaseModelMixin(TimestampMixin, ReprMixin):
    """
    基础模型 Mixin

    组合了最常用的 Mixin:时间戳 + repr
    适用于大多数业务模型

    使用示例:
        class User(BaseModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            name: str
    """


class AuditModelMixin(AuditMixin, ReprMixin):
    """
    审计模型 Mixin

    组合了审计字段 + repr
    适用于需要审计追踪的业务模型

    使用示例:
        class Article(AuditModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建时记录创建人
        article = Article(title="测试", created_by=user_id)
    """


class FullModelMixin(AuditModelMixin, SoftDeleteMixin, OptimisticLockMixin):
    """
    完整模型 Mixin

    组合了所有 Mixin:时间戳 + 审计 + 软删除 + repr
    适用于需要完整功能的模型

    使用示例:
        class Article(FullModelMixin, table=True):
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


# ==================== 带主键的组合 Mixin ====================


# type: ignore - PrimaryKeyMixin 是动态生成的类
class BaseTableModelMixin(PrimaryKeyMixin, BaseModelMixin):  # type: ignore[misc]
    """
    基础表模型 Mixin(主键)

    组合了:主键(自增/雪花) + 时间戳 + repr
    最常用的表模型配置

    使用示例:
        class User(BaseTableModelMixin, table=True):
            username: str
            email: str

        # 无需定义 id,自动继承
    """
