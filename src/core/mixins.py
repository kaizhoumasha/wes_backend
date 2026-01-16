"""
SQLModel Mixin 类

提供可复用的模型字段和行为，遵循 DRY 原则

参考: https://sqlmodel.tiangolo.com/tutorial/automatic_id_none_refresh/
"""

from datetime import datetime
from typing import Optional
from sqlmodel import Field
from sqlalchemy import Column, DateTime, func, Integer


class TimestampMixin:
    """
    时间戳 Mixin

    为模型添加 created_at 和 updated_at 字段
    - created_at: 自动设置创建时间
    - updated_at: 自动更新修改时间

    使用示例:
        class User(TimestampMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(),
        sa_column=Column(
            DateTime, server_default=func.now(), nullable=False, comment="创建时间"
        ),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(),
        sa_column=Column(
            DateTime,
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
            comment="更新时间",
        ),
    )


class SoftDeleteMixin:
    """
    软删除 Mixin

    为模型添加软删除功能，数据不会被物理删除
    - deleted_at: 删除时间（None 表示未删除）
    - is_deleted: 删除标记

    使用示例:
        class Article(SoftDeleteMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            title: str

        article.soft_delete()  # 标记为已删除
        article.restore()     # 恢复已删除的记录
    """

    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True, comment="删除时间")
    )
    is_deleted: bool = Field(default=False, sa_column_kwargs={"comment": "是否已删除"})

    def soft_delete(self, deleted_by: Optional[int] = None) -> None:
        """
        标记为已删除

        :param deleted_by: 删除人ID（如果模型有 AuditMixin）
        """
        self.is_deleted = True
        self.deleted_at = datetime.now()
        if deleted_by is not None and hasattr(self, "deleted_by"):
            self.deleted_by = deleted_by

    def restore(self) -> None:
        """恢复已删除的记录"""
        self.is_deleted = False
        self.deleted_at = None
        if hasattr(self, "deleted_by"):
            self.deleted_by = None


class AuditMixin:
    """
    审计字段 Mixin

    为模型添加审计追踪字段，记录操作的执行人
    - created_by: 创建人ID
    - updated_by: 更新人ID
    - deleted_by: 删除人ID

    配合 SoftDeleteMixin 使用可实现完整的审计追踪

    使用示例:
        class Article(AuditMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建时设置
        article = Article(title="测试", created_by=user_id)

        # 更新时设置
        article.title = "新标题"
        article.updated_by = user_id

        # 软删除时设置
        article.soft_delete(deleted_by=user_id)
    """

    created_by: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment="创建人ID")
    )
    updated_by: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment="更新人ID")
    )
    deleted_by: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment="删除人ID")
    )


class ReprMixin:
    """
    通用 __repr__ Mixin

    自动生成包含所有字段值的字符串表示

    使用示例:
        class User(ReprMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str

        print(user)  # <User(id=1, name='test')>
    """

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attributes = [
            f"{k}={repr(v)}" for k, v in self.__dict__.items() if not k.startswith("_")
        ]
        return f"<{class_name}({', '.join(attributes)})>"


# ==================== 组合 Mixin ====================


class BaseModelMixin(TimestampMixin, ReprMixin):
    """
    基础模型 Mixin

    组合了最常用的 Mixin：时间戳 + repr
    适用于大多数业务模型

    使用示例:
        class User(BaseModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            name: str
    """

    pass


class AuditModelMixin(BaseModelMixin, AuditMixin):
    """
    审计模型 Mixin

    组合了基础模型 + 审计字段
    适用于需要审计追踪的业务模型

    使用示例:
        class Article(AuditModelMixin, table=True):
            id: Optional[int] = Field(default=None, primary_key=True)
            title: str

        # 创建时记录创建人
        article = Article(title="测试", created_by=user_id)
    """

    pass


class FullModelMixin(AuditModelMixin, SoftDeleteMixin):
    """
    完整模型 Mixin

    组合了所有 Mixin：时间戳 + 审计 + 软删除 + repr
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

        # 软删除（自动设置 deleted_by）
        article.soft_delete(deleted_by=3)
    """

    pass
